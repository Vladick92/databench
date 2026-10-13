# See docs/PLAN.md's architecture diagram note for how these fit together, including the
# manual steps (pushing images, setting OPENROUTER_API_KEY) that Terraform itself doesn't cover.

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
}

# Users, chats and messages; free tier.
module "cosmos" {
  source              = "./modules/cosmos"
  name                = "cosmos-databench-${random_string.suffix.result}"
  resource_group_name = module.resource_group.name
  location            = module.resource_group.location
}

# Signs the login tokens; stable across applies, new after a destroy.
resource "random_password" "auth_secret" {
  length  = 48
  special = false
}

# --- Backend Web App ---
# POSTGRES_DSN (a database for the agent to analyse, not the app's own) is not set: there is none in Azure yet.
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

  app_settings = merge(
    {
      # The run mode (backend/app/config.py): Cosmos + Blob + AUTH_SECRET below. Without it the
      # backend would start in local mode and refuse the Cosmos/Blob settings.
      APP_ENV = "azure"

      MODEL_PROVIDER     = "openrouter"
      OPENROUTER_MODEL   = var.model_name         # not MODEL_NAME: this one only applies while openrouter is the provider
      OPENROUTER_API_KEY = var.openrouter_api_key # the preset's own key variable, so no MODEL_API_KEY

      AZURE_STORAGE_ACCOUNT_NAME = module.storage.name
      COSMOS_ENDPOINT            = module.cosmos.endpoint
      COSMOS_DATABASE            = module.cosmos.database_name
      AUTH_SECRET                = random_password.auth_secret.result

      # Behaviour knobs that also exist in a local .env: set here too, so the deployed app doesn't quietly
      # run on code defaults while a local run uses something else. Defaults in variables.tf = config.py's.
      MAX_ROWS_RETURNED = tostring(var.max_rows_returned)
      CHART_MIN_ROWS    = tostring(var.chart_min_rows)
      MAX_UPLOAD_MB     = tostring(var.max_upload_mb)
      AUTH_TOKEN_HOURS  = tostring(var.auth_token_hours)
    },
    # Opt-in: OpenRouter's free router answers when the model above can't (backend/app/llm.py).
    var.openrouter_free_fallback ? { MODEL_FALLBACK_PROVIDERS = "openrouter" } : {},
  )
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

# Only the backend touches Blob Storage (the UI goes through its API); this also lets it create each user's container.
resource "azurerm_role_assignment" "backend_storage_blob_contributor" {
  scope                            = module.storage.id
  role_definition_name             = "Storage Blob Data Contributor"
  principal_id                     = module.backend_app.principal_id
  skip_service_principal_aad_check = true
}

# Cosmos data access is a role system of its own; with no keys on the account it is the only way in.
# 00000000-0000-0000-0000-000000000002 is the built-in "Cosmos DB Built-in Data Contributor" (read/write documents).
locals {
  cosmos_data_contributor = "${module.cosmos.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
}

data "azurerm_client_config" "current" {}

resource "azurerm_cosmosdb_sql_role_assignment" "backend_cosmos_data" {
  resource_group_name = module.resource_group.name
  account_name        = module.cosmos.name
  role_definition_id  = local.cosmos_data_contributor
  principal_id        = module.backend_app.principal_id
  scope               = module.cosmos.id
}

# You, so the portal's Data Explorer can show the data (without a key it only lets in a data role).
resource "azurerm_cosmosdb_sql_role_assignment" "owner_cosmos_data" {
  resource_group_name = module.resource_group.name
  account_name        = module.cosmos.name
  role_definition_id  = local.cosmos_data_contributor
  principal_id        = data.azurerm_client_config.current.object_id
  scope               = module.cosmos.id
}
