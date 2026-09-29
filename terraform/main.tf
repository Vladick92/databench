# See docs/PLAN.md's architecture diagram note for how these fit together, including the
# manual steps (pushing images, setting GROQ_API_KEY) that Terraform itself doesn't cover.

# ACR and Storage Account names must be globally unique across all of Azure; this suffix is
# what makes "databench" collision-safe without the owner having to pick one by hand.
resource "random_string" "suffix" {
  length  = 4
  special = false
  upper   = false
}

module "resource_group" {
  source   = "./modules/resource_group"
  name     = "rg-databench-${var.environment}"
  location = var.location
}

module "budget" {
  source            = "./modules/budget"
  resource_group_id = module.resource_group.id
  name_suffix       = "databench-${var.environment}"
  amount            = var.budget_amount
  alert_email       = var.budget_alert_email
}

module "acr" {
  source              = "./modules/acr"
  name                = "acrdatabench${random_string.suffix.result}"
  resource_group_name = module.resource_group.name
  location            = module.resource_group.location
}

module "app_service_plan" {
  source              = "./modules/app_service_plan"
  name                = "asp-databench-${var.environment}"
  resource_group_name = module.resource_group.name
  location            = module.resource_group.location
}

module "storage" {
  source              = "./modules/storage"
  name                = "stdatabench${random_string.suffix.result}"
  resource_group_name = module.resource_group.name
  location            = module.resource_group.location
  container_name      = "tabular-data"
}

# --- Backend Web App ---
# POSTGRES_DSN is deliberately not set here: docs/PLAN.md defers the DB, and backend/app/config.py
# already treats an unset POSTGRES_DSN as "file sources only" with no code change needed.
module "backend_app" {
  source              = "./modules/web_app"
  name                = "app-databench-backend-${random_string.suffix.result}"
  resource_group_name = module.resource_group.name
  location            = module.resource_group.location
  service_plan_id     = module.app_service_plan.id
  acr_login_server    = module.acr.login_server
  image_name          = "databench-backend"
  image_tag           = var.image_tag
  container_port      = 8000
  websockets_enabled  = false # FastAPI here doesn't use websockets - only the UI does

  app_settings = {
    MODEL_PROVIDER               = "groq"
    MODEL_NAME                   = var.model_name
    MODEL_API_KEY                = var.groq_api_key
    AZURE_STORAGE_ACCOUNT_NAME   = module.storage.name
    AZURE_STORAGE_CONTAINER_NAME = module.storage.container_name
  }
}

# --- UI Web App ---
module "ui_app" {
  source              = "./modules/web_app"
  name                = "app-databench-ui-${random_string.suffix.result}"
  resource_group_name = module.resource_group.name
  location            = module.resource_group.location
  service_plan_id     = module.app_service_plan.id
  acr_login_server    = module.acr.login_server
  image_name          = "databench-ui"
  image_tag           = var.image_tag
  container_port      = 8501
  websockets_enabled  = true # Streamlit needs this

  app_settings = {
    BACKEND_URL = "https://${module.backend_app.default_hostname}"
  }
}

# --- Role assignments: system-assigned identity is how every binding below works, no
# passwords/connection strings for infra access anywhere. ---

resource "azurerm_role_assignment" "backend_acr_pull" {
  scope                            = module.acr.id
  role_definition_name             = "AcrPull"
  principal_id                     = module.backend_app.principal_id
  skip_service_principal_aad_check = true # the identity may not be visible in AAD yet right after creation
}

resource "azurerm_role_assignment" "ui_acr_pull" {
  scope                            = module.acr.id
  role_definition_name             = "AcrPull"
  principal_id                     = module.ui_app.principal_id
  skip_service_principal_aad_check = true
}

# Only the backend touches Blob Storage - the UI goes through the backend's API, not Azure
# directly, so only the backend's identity needs this.
resource "azurerm_role_assignment" "backend_storage_blob_contributor" {
  scope                            = module.storage.id
  role_definition_name             = "Storage Blob Data Contributor"
  principal_id                     = module.backend_app.principal_id
  skip_service_principal_aad_check = true
}
