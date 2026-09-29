variable "name" {
  type        = string
  description = "Resource group name, e.g. rg-databench-dev."
}

variable "location" {
  type        = string
  description = "Azure region for the resource group (and, by default, every resource inside it)."
}
