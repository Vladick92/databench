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

# --- Model (OpenRouter, the same model the local run mode uses: MODEL_PROVIDER=openrouter in databench/.env) ---
variable "model_name" {
  type        = string
  description = "The OpenRouter model id the backend uses. A paid model (not :free): it spends the OpenRouter credit."
  default     = "openai/gpt-6-luna"
}

variable "openrouter_api_key" {
  type        = string
  description = "Set via TF_VAR_openrouter_api_key, sourced from ../model_service/.env - never put this in a .tfvars file that could get committed."
  sensitive   = true
}

variable "openrouter_free_fallback" {
  type        = bool
  description = "If true, a request OpenRouter can't serve (429/5xx, unreachable) is retried on OpenRouter's free router (backend/app/llm.py). Free endpoints may log or train on prompts, so it is off by default."
  default     = false
}

# --- App behaviour (same names as the .env settings; the defaults are backend/app/config.py's) ---
variable "max_rows_returned" {
  type        = number
  description = "Rows one query call retrieves; the statistician charts all of them."
  default     = 1000
}

variable "chart_min_rows" {
  type        = number
  description = "A result with more rows than this is charted and summarized; up to this many the data agent shows a table."
  default     = 15
}

variable "max_upload_mb" {
  type        = number
  description = "Largest file a user can upload, in MB."
  default     = 25
}

variable "auth_token_hours" {
  type        = number
  description = "How long a login lasts, in hours."
  default     = 12
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
