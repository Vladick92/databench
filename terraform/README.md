# databench Azure infrastructure

Everything the app needs in Azure, as code. Modular (`modules/`, one module per resource
family) per `docs/PLAN.md`'s convention. `terraform plan` was run against the real subscription
while writing this (read-only, no resources created) - 12 resources, clean plan, no errors.

## What this creates
```
Resource Group (rg-databench-dev)
├── Budget alert (only if budget_alert_email is set)
├── Container Registry (Basic, admin disabled)
├── App Service Plan (B1 Linux) - shared by both Web Apps below
│   ├── Web App: backend (FastAPI, port 8000, Docker from ACR)
│   │     └── AcrPull + Storage Blob Data Contributor (system-assigned identity)
│   └── Web App: ui (Streamlit, port 8501, Docker from ACR, WebSockets on)
│         └── AcrPull (system-assigned identity)
└── Storage Account (Standard/LRS/Hot) + Blob container "tabular-data"
```
No database yet - `docs/PLAN.md` defers it, and `backend/app/config.py` already treats an unset
`POSTGRES_DSN` as "file/blob sources only," so nothing needed disabling in code. No ACR admin
password, no storage connection string/key anywhere - every binding is a role assignment on a
system-assigned managed identity.

## Before `apply`
1. **Push the images first, or the sites 503 on first boot.** A fresh ACR has nothing in it yet:
   ```bash
   az acr login --name <will exist only after apply - see below>
   ```
   This is a chicken-and-egg: the ACR has to exist before you can push to it, but the Web Apps
   reference an image tag that has to already exist in the ACR. Order that works:
   1. `terraform apply` (Web Apps will show unhealthy/503 - expected, no image yet)
   2. `ACR_NAME=$(terraform output -raw acr_name) ./push_images.sh` (from the repo root)
   3. Restart both Web Apps (`az webapp restart ...`, printed by the push script) so they pick up the newly-pushed image
2. **Budget alert email** - set `budget_alert_email` before applying, per `docs/PLAN.md`'s cost discipline rule (budget before spend).
3. **Groq API key** - required, not optional:
   ```bash
   export ARM_SUBSCRIPTION_ID=$(az account show --query id -o tsv)
   export TF_VAR_groq_api_key=$(grep '^GROQ_API_KEY=' ../../model_service/.env | cut -d= -f2)
   ```
   Never put this in a `.tfvars` file you might commit - `TF_VAR_*` env vars only.

## Usage
```bash
cd databench/terraform
az login
export ARM_SUBSCRIPTION_ID=$(az account show --query id -o tsv)
export TF_VAR_groq_api_key=$(grep '^GROQ_API_KEY=' ../../model_service/.env | cut -d= -f2)
cp terraform.tfvars.example terraform.tfvars   # then edit budget_alert_email at minimum

terraform init
terraform plan
terraform apply
```

Then push the images (see "Before apply" above):
```bash
cd ..   # databench/
ACR_NAME=$(terraform -chdir=terraform output -raw acr_name) ./push_images.sh
az webapp restart -g $(terraform -chdir=terraform output -raw resource_group_name) -n <backend app name>
az webapp restart -g $(terraform -chdir=terraform output -raw resource_group_name) -n <ui app name>
```

Open the UI: `terraform output ui_url`.

## Things worth knowing
- **State has secrets.** `groq_api_key` (as `MODEL_API_KEY` in the backend Web App's settings) is stored in `terraform.tfstate` in plain text - same tradeoff project 01 already made for `model_api_key`; fine for a local/solo learning setup with local state, not for a shared/remote backend without also adding Key Vault.
- **Container logging is on from the start** (`logs` block in `modules/web_app`) - project 01 hit a 503 with nothing to read because this was off; not repeating that here.
- **B1, not F1.** Two Web Apps share one plan; F1's 60 CPU-min/day cap and no-Always-On would hurt with two apps on it. `always_on = true` and `use_32_bit_worker = false` follow from being off F1.
- **Blob access tier is Hot**, not Cool/Archive, despite "least expensive" - Cool/Archive charge per-operation retrieval fees and have minimum retention periods, which cost more overall for a workbench with active upload/list/delete from the UI, not archived data.
- **Naming**: ACR and Storage Account names must be globally unique and can't contain hyphens - both get a random 4-character suffix (`random_string.suffix`) appended. Web App names are also globally unique (they become `<name>.azurewebsites.net`) and get the same suffix.
- **`image_tag` (default `v1`) must match what you actually pushed** - same pattern as project 01: push `./push_images.sh v2`, set `image_tag = "v2"`, `terraform apply` to roll out a new version.

Clean up everything: `terraform destroy` (or delete the resource group).
