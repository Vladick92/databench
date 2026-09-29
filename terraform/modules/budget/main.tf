# Same pattern as 01-base-agent-app-service/terraform - only created when an alert email is
# given, and only created once per resource group (see docs/PLAN.md: "budget alert before any
# other resource").
resource "azurerm_consumption_budget_resource_group" "this" {
  count = var.alert_email == "" ? 0 : 1

  name              = "budget-${var.name_suffix}"
  resource_group_id = var.resource_group_id
  amount            = var.amount
  time_grain        = "Monthly"

  time_period {
    start_date = formatdate("YYYY-MM-01'T'00:00:00'Z'", plantimestamp())
  }

  notification {
    enabled        = true
    threshold      = 80
    operator       = "GreaterThan"
    threshold_type = "Actual"
    contact_emails = [var.alert_email]
  }

  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThan"
    threshold_type = "Forecasted"
    contact_emails = [var.alert_email]
  }

  lifecycle {
    ignore_changes = [time_period] # start date is computed from "now"; do not churn every month
  }
}
