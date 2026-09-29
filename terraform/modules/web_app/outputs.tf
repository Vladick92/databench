output "id" {
  value = azurerm_linux_web_app.this.id
}

output "name" {
  value = azurerm_linux_web_app.this.name
}

output "principal_id" {
  value       = azurerm_linux_web_app.this.identity[0].principal_id
  description = "System-assigned identity's principal ID, for role assignments at the root module."
}

output "default_hostname" {
  value       = azurerm_linux_web_app.this.default_hostname
  description = "e.g. app-databench-backend-xxxx.azurewebsites.net (no scheme)."
}
