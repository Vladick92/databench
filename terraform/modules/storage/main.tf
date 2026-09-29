# Least expensive config that still fits actively-used, upload/list/delete-from-the-UI access:
# Standard performance + LRS redundancy (cheapest replication tier) + Hot access tier. Cool/Cold
# tiers store data cheaper per GB but charge per-operation retrieval fees and have minimum
# retention periods - worse overall for a workbench where files are actively read/written each
# session, not archived.
resource "azurerm_storage_account" "this" {
  name                     = var.name
  resource_group_name      = var.resource_group_name
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  access_tier              = "Hot"

  # Blob access is via each Web App's managed identity (Storage Blob Data Contributor role,
  # assigned at the root module) - shared keys are not needed and left available only because
  # disabling them entirely also blocks a couple of convenience data-plane operations Terraform
  # itself sometimes uses. HTTPS-only regardless.
  https_traffic_only_enabled = true
}

resource "azurerm_storage_container" "tabular_data" {
  name                  = var.container_name
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}
