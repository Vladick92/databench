variable "subscription_id" {
  type        = string
  description = "Azure subscription ID. Usually left to ARM_SUBSCRIPTION_ID instead of set here."
  default     = null
}

variable "location" {
  type        = string
  description = "Azure region for everything."
  default     = "polandcentral" # cheap region; change freely
}

variable "environment" {
  type        = string
  description = "Short environment tag used in resource names, e.g. 'dev'."
  default     = "dev"
}

variable "image_tag" {
  type        = string
  description = "Tag for both the backend and ui images in ACR. Must exist in ACR before apply, or the sites 503 until pushed."
  default     = "v1"
}

# --- Model (Groq, matching backend/app/config.py's MODEL_PROVIDER=groq preset) ---
variable "model_name" {
  type        = string
  description = "Overrides the Groq preset's default model if set."
  default     = "openai/gpt-oss-120b"
}

variable "groq_api_key" {
  type        = string
  description = "Set via TF_VAR_groq_api_key, sourced from ../model_service/.env - never put this in a .tfvars file that could get committed."
  sensitive   = true
}

variable "openrouter_api_key" {
  type        = string
  description = "Optional. If set, the backend falls back to OpenRouter (a free model, see backend/app/config.py) whenever Groq is rate-limited or down. Set via TF_VAR_openrouter_api_key, sourced from ../model_service/.env - never put this in a .tfvars file that could get committed. Leave empty for no fallback."
  sensitive   = true
  default     = ""
}

# --- Cost guard ---
variable "budget_alert_email" {
  type        = string
  description = "Leave empty to skip creating a budget alert - not recommended, but supported."
  default     = ""
}

variable "budget_amount" {
  type    = number
  default = 15
}
