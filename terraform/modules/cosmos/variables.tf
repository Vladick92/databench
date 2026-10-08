variable "name" {
  type        = string
  description = "Cosmos DB account name. Globally unique, 3-44 characters, lowercase letters, digits and hyphens."
}

variable "resource_group_name" {
  type = string
}

variable "location" {
  type = string
}

variable "database_name" {
  type    = string
  default = "databench"
}
