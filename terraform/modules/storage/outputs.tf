output "id" {
  value = azurerm_storage_account.this.id
}

output "name" {
  value = azurerm_storage_account.this.name
}

output "container_name" {
  value = azurerm_storage_container.tabular_data.name
}
