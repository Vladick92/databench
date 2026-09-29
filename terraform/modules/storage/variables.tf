variable "name" {
  type        = string
  description = "Storage account name. Globally unique, lowercase alphanumeric only (no hyphens), <=24 chars."
}

variable "resource_group_name" {
  type = string
}

variable "location" {
  type = string
}

variable "container_name" {
  type    = string
  default = "tabular-data"
}
