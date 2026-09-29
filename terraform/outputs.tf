output "resource_group_name" {
  value = module.resource_group.name
}

output "acr_login_server" {
  value = module.acr.login_server
}

output "acr_name" {
  value = module.acr.name
}

output "backend_url" {
  value = "https://${module.backend_app.default_hostname}"
}

output "ui_url" {
  value = "https://${module.ui_app.default_hostname}"
}

output "storage_account_name" {
  value = module.storage.name
}

output "storage_container_name" {
  value = module.storage.container_name
}

output "push_images_command" {
  value       = "ACR_NAME=${module.acr.name} IMAGE_TAG=${var.image_tag} ./push_images.sh"
  description = "The images don't exist in a fresh ACR yet - both sites 503 until you run this."
}
