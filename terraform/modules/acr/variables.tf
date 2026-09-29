variable "name" {
  type        = string
  description = "ACR name. Globally unique, alphanumeric only (no hyphens), 5-50 chars."
}

variable "resource_group_name" {
  type = string
}

variable "location" {
  type = string
}
