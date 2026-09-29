# B1, not the free F1 tier used in project 01 - the owner asked for one tier above free.
# Removes F1's 60 CPU-min/day cap and lack of Always On, which matter now that two apps
# (backend + UI) share this one plan instead of one app having it to itself.
resource "azurerm_service_plan" "this" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  os_type             = "Linux"
  sku_name            = "B1"
}
