# Users, chats and messages (backend/app/store/cosmos.py). Free tier: the first 1000 RU/s and 25 GB
# cost nothing, but only one account per subscription can have it, and only from creation.
resource "azurerm_cosmosdb_account" "this" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  offer_type          = "Standard"
  kind                = "GlobalDocumentDB" # the NoSQL API

  free_tier_enabled = true

  # Entra ID sign-in only: no keys exist to leak; the backend uses its managed identity.
  local_authentication_enabled = false

  minimal_tls_version = "Tls12"

  consistency_policy {
    consistency_level = "Session" # a client reads its own writes, enough for a chat app
  }

  geo_location {
    location          = var.location
    failover_priority = 0
  }
}

# Throughput is shared by the containers; 1000 RU/s is exactly the free allowance.
resource "azurerm_cosmosdb_sql_database" "this" {
  name                = var.database_name
  resource_group_name = var.resource_group_name
  account_name        = azurerm_cosmosdb_account.this.name
  throughput          = 1000
}

# Partition key = what each request filters on, so no query leaves its partition.
locals {
  containers = {
    users    = "/username"
    chats    = "/username"
    messages = "/chat_id"
  }
}

resource "azurerm_cosmosdb_sql_container" "this" {
  for_each = local.containers

  name                  = each.key
  resource_group_name   = var.resource_group_name
  account_name          = azurerm_cosmosdb_account.this.name
  database_name         = azurerm_cosmosdb_sql_database.this.name
  partition_key_paths   = [each.value]
  partition_key_version = 2
}
