variable "resource_group_id" {
  type        = string
  description = "Resource group to scope the budget to."
}

variable "name_suffix" {
  type        = string
  description = "Appended to 'budget-' for the budget's name, e.g. 'databench-dev'."
}

variable "amount" {
  type        = number
  description = "Monthly budget amount in the subscription's currency."
  default     = 15
}

variable "alert_email" {
  type        = string
  description = "Email for budget alerts. Leave empty (\"\") to skip creating a budget - not recommended, but supported for local iteration."
  default     = ""
}
