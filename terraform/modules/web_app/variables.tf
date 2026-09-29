variable "name" {
  type        = string
  description = "Web App name. Globally unique (becomes <name>.azurewebsites.net)."
}

variable "resource_group_name" {
  type = string
}

variable "location" {
  type = string
}

variable "service_plan_id" {
  type = string
}

variable "acr_login_server" {
  type        = string
  description = "e.g. acrdatabenchxxxx.azurecr.io - used to build the docker_registry_url."
}

variable "image_name" {
  type        = string
  description = "Repository name inside the ACR, e.g. 'databench-backend'."
}

variable "image_tag" {
  type = string
}

variable "container_port" {
  type        = number
  description = "Port the container listens on inside itself (WEBSITES_PORT)."
}

variable "websockets_enabled" {
  type        = bool
  default     = true
  description = "Streamlit needs this; harmless for the backend too."
}

variable "app_settings" {
  type        = map(string)
  default     = {}
  description = "Extra app settings beyond the ones this module always sets (port, storage, container-identity pull)."
}
