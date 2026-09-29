# One Web App, parameterized so the root module can call this twice (backend, ui) without
# repeating the boilerplate. Which resources this app's identity is allowed to touch (AcrPull,
# Storage Blob Data Contributor, ...) is decided at the root, not here - this module only
# creates the identity and exposes its principal_id.
resource "azurerm_linux_web_app" "this" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  service_plan_id     = var.service_plan_id

  https_only = true

  identity {
    type = "SystemAssigned"
  }

  app_settings = merge(
    {
      WEBSITES_PORT                       = tostring(var.container_port)
      WEBSITES_ENABLE_APP_SERVICE_STORAGE = "false"
    },
    var.app_settings,
  )

  site_config {
    always_on          = true  # B1 supports it, unlike project 01's F1 - keeps the container warm, no cold-start on first request after idle
    use_32_bit_worker  = false # only an F1 requirement; B1 runs 64-bit, better for pandas/duckdb's memory use
    websockets_enabled = var.websockets_enabled
    ftps_state         = "FtpsOnly"

    container_registry_use_managed_identity = true

    application_stack {
      docker_registry_url = "https://${var.acr_login_server}"
      docker_image_name   = "${var.image_name}:${var.image_tag}"
    }
  }

  # Project 01's documented lesson: container logging was off, so a 503 had nothing to read.
  # On by default here instead of discovered as a gap after something breaks.
  logs {
    application_logs {
      file_system_level = "Information"
    }
    http_logs {
      file_system {
        retention_in_days = 3
        retention_in_mb   = 35
      }
    }
  }
}
