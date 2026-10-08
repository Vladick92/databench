# databench Azure infrastructure

Everything the app needs in Azure, as code. Modular (`modules/`, one module per resource
family) per `docs/PLAN.md`'s convention. The first 12 resources were checked with `terraform plan`
against the real subscription (read-only, clean). The Cosmos DB account, its role assignments and the
login secret were added later and only checked with `terraform validate` - run `terraform plan` before
the first apply.

## What this creates
```
Resource Group (rg-databench-dev)
├── Budget alert (only if budget_alert_email is set)
├── Container Registry (Basic, admin disabled)
├── App Service Plan (B1 Linux) - shared by both Web Apps below
│   ├── Web App: backend (FastAPI, port 8000, Docker from ACR)
│   │     └── AcrPull + Storage Blob Data Contributor + Cosmos data role (system-assigned identity)
│   └── Web App: ui (Streamlit, port 8501, Docker from ACR, WebSockets on)
│         └── AcrPull (system-assigned identity)
├── Storage Account (Standard/LRS/Hot) - one private container per user, made by the backend at sign-up
└── Cosmos DB account (free tier, Entra ID only) + database "databench" + containers users, chats, messages
```
The app's own database is Cosmos DB (users, chats, messages). `POSTGRES_DSN` is a different thing - a
database for the data agent to *analyse* - and is still not set in Azure. No ACR admin password, no
storage key, no Cosmos key anywhere: every binding is a role assignment on a system-assigned managed
identity. The one secret Terraform makes is `AUTH_SECRET`, which signs the login tokens.

### What each database resource does, and why
| Resource | What it does |
|---|---|
| `azurerm_cosmosdb_account` | The database service. Free tier (first 1000 RU/s and 25 GB free, one such account per subscription), Entra ID sign-in only, so it has no keys to leak. |
| `azurerm_cosmosdb_sql_database` | The database `databench`. 1000 RU/s shared by its containers - exactly what the free tier covers. |
| `azurerm_cosmosdb_sql_container` x3 | `users` and `chats` (partitioned by username), `messages` (partitioned by chat id): the "users -> chats -> messages" structure, with each request staying inside one partition. |
| `azurerm_cosmosdb_sql_role_assignment.backend_cosmos_data` | Lets the backend read and write documents, as its own managed identity. Cosmos data access is a role system of its own, separate from the Azure roles. |
| `azurerm_cosmosdb_sql_role_assignment.owner_cosmos_data` | Lets *you* (whoever runs `apply`) browse the data in the portal's Data Explorer. Without a key, the portal only shows data to someone with a data role. |
| `random_password.auth_secret` | The key that signs login tokens, passed to the backend as `AUTH_SECRET`. |

The shared `tabular-data` blob container is gone: each user gets `user-<username>`, created by the
backend when they register (the backend's Blob Data Contributor role allows it).

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
   export TF_VAR_openrouter_api_key=$(grep '^OPENROUTER_API_KEY=' ../../model_service/.env | cut -d= -f2)   # optional: enables the OpenRouter fallback
   ```
   Never put this in a `.tfvars` file you might commit - `TF_VAR_*` env vars only.

## Usage
```bash
cd databench/terraform
az login
export ARM_SUBSCRIPTION_ID=$(az account show --query id -o tsv)
export TF_VAR_groq_api_key=$(grep '^GROQ_API_KEY=' ../../model_service/.env | cut -d= -f2)
export TF_VAR_openrouter_api_key=$(grep '^OPENROUTER_API_KEY=' ../../model_service/.env | cut -d= -f2)   # optional: enables the OpenRouter fallback
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
- **State has secrets.** `groq_api_key` (as `MODEL_API_KEY` in the backend Web App's settings, and `openrouter_api_key` as `OPENROUTER_API_KEY` if set) is stored in `terraform.tfstate` in plain text - same tradeoff project 01 already made for `model_api_key`; fine for a local/solo learning setup with local state, not for a shared/remote backend without also adding Key Vault.
- **Container logging is on from the start** (`logs` block in `modules/web_app`) - project 01 hit a 503 with nothing to read because this was off; not repeating that here.
- **B1, not F1.** Two Web Apps share one plan; F1's 60 CPU-min/day cap and no-Always-On would hurt with two apps on it. `always_on = true` and `use_32_bit_worker = false` follow from being off F1.
- **Blob access tier is Hot**, not Cool/Archive, despite "least expensive" - Cool/Archive charge per-operation retrieval fees and have minimum retention periods, which cost more overall for a workbench with active upload/list/delete from the UI, not archived data.
- **Naming**: ACR and Storage Account names must be globally unique and can't contain hyphens - both get a random 4-character suffix (`random_string.suffix`) appended. Web App names are also globally unique (they become `<name>.azurewebsites.net`) and get the same suffix.
- **`destroy` deletes the accounts, chats and every user's files** (Cosmos and the storage account go with the stack). Fine while testing; after that, don't destroy what people use.
- **Cosmos free tier is one account per subscription.** If the subscription already has one, apply fails on the account with a message saying so. If Cosmos refuses new accounts in the region (a capacity error, not a code one), change `location` - everything is created there.
- **`AUTH_SECRET` is in state** with the other secrets. Creating the stack again makes a new one, which logs everybody out; nothing else is lost.
- **Registration is open** to anyone who can reach the UI, and every account can spend model calls (Groq's free quota, or OpenRouter credit if that is the model). Fine for a demo behind an unshared URL; add an invite code before sharing the link.
- **`image_tag` (default `v1`) must match what you actually pushed** - same pattern as project 01: push `./push_images.sh v2`, set `image_tag = "v2"`, `terraform apply` to roll out a new version.

Clean up everything: `terraform destroy` (or delete the resource group).
