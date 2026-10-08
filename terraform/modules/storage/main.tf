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

# No container here: the backend creates one per user ("user-<username>") at registration.
