# Azure Deployment — Step-by-Step Guide

This document walks through deploying The Analyst Copilot to Azure from scratch.
The automated script (`infra/scripts/deploy.sh`) does all of this — read here if
you want to understand each step or need to debug something.

---

## Architecture on Azure

```
Internet
  └── Azure Static Web Apps (React SPA, CDN-backed)
        └── API calls → Azure App Service (Python/FastAPI, Docker container)
                            └── Azure File Share (ChromaDB + BM25 indexes, persistent)
                            └── Azure Key Vault (LLM API keys, secret references)
                            └── Azure Container Registry (Docker images)
```

---

## Prerequisites

```bash
# Install Azure CLI
# macOS: brew install azure-cli
# Linux: curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
# Windows: https://aka.ms/installazurecliwindows

az --version        # should print 2.60+
az login            # opens browser for auth
az account show     # confirm the right subscription is active

# If you have multiple subscriptions:
az account set --subscription "<subscription-name-or-id>"
```

---

## Step 1: Create a service principal (for CI/CD)

```bash
# Create a service principal with Contributor role on the subscription
az ad sp create-for-rbac \
  --name "analyst-copilot-sp" \
  --role Contributor \
  --scopes "/subscriptions/$(az account show --query id -o tsv)" \
  --sdk-auth

# Save the JSON output — you'll need it for GitHub Secrets:
# {
#   "clientId": "...",
#   "clientSecret": "...",
#   "subscriptionId": "...",
#   "tenantId": "..."
# }
```

---

## Step 2: Run the automated deployment

```bash
./infra/scripts/deploy.sh \
  --env prod \
  --prefix acopilot \
  --location eastus \
  --provider anthropic \
  --api-key "sk-ant-api03-..."

# Supported locations: eastus, westus2, westeurope, eastasia, australiaeast
# --env: dev | staging | prod (controls resource naming)
# --prefix: 3-8 lowercase alphanum chars, must be globally unique for storage/ACR
```

If the prefix is already taken (storage account names are globally unique),
try a different one: `--prefix mycopilot`, `--prefix acplt42`, etc.

---

## Step 3: Manual steps after deployment

### 3a. Verify the API key is in Key Vault

The deploy script stores the key if you pass `--api-key`. Verify:

```bash
az keyvault secret list --vault-name acopilot-prod-kv --query "[].name" -o tsv
# Should show: ANTHROPIC-API-KEY (or OPENAI-API-KEY / GEMINI-API-KEY)
```

If missing, add it:
```bash
az keyvault secret set \
  --vault-name acopilot-prod-kv \
  --name ANTHROPIC-API-KEY \
  --value "sk-ant-..."
```

### 3b. Confirm the App Service can read the secret

```bash
# Check managed identity is assigned
az webapp identity show -g acopilot-prod-rg -n acopilot-prod-api

# Test the health endpoint
curl https://acopilot-prod-api.azurewebsites.net/health
```

### 3c. Ingest your first filing

```bash
curl -X POST https://acopilot-prod-api.azurewebsites.net/upload \
  -F "files=@./filings/3M_2018_10K.htm"

# Or use the UI at the Static Web App URL
```

---

## Step 4: Set up GitHub Actions CI/CD

Add these secrets to your GitHub repo (Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `AZURE_CLIENT_ID` | Service principal clientId |
| `AZURE_TENANT_ID` | Service principal tenantId |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |
| `ACR_LOGIN_SERVER` | e.g. `acopilotprodacr.azurecr.io` |
| `ACR_USERNAME` | ACR admin username (from portal or `az acr credential show`) |
| `ACR_PASSWORD` | ACR admin password |
| `AZURE_BACKEND_APP` | `acopilot-prod-api` |
| `AZURE_RG` | `acopilot-prod-rg` |
| `VITE_API_BASE` | `https://acopilot-prod-api.azurewebsites.net` |
| `SWA_DEPLOYMENT_TOKEN` | From portal: Static Web Apps → Manage deployment token |

Get ACR credentials:
```bash
az acr credential show -n acopilotprodacr
```

Get SWA deployment token:
```bash
az staticwebapp secrets list -n acopilot-prod-web -g acopilot-prod-rg \
  --query properties.apiKey -o tsv
```

---

## Step 5: Monitoring and logs

```bash
# Stream live logs from the App Service
az webapp log tail -g acopilot-prod-rg -n acopilot-prod-api

# Check deployment history
az webapp deployment list-publishing-credentials -g acopilot-prod-rg -n acopilot-prod-api

# SSH into the container (useful for debugging)
az webapp ssh -g acopilot-prod-rg -n acopilot-prod-api
```

---

## Cost estimate (prod, light usage)

| Resource | SKU | Monthly cost (approx) |
|---|---|---|
| App Service Plan | B2 Linux | ~$70/mo |
| App Service | (included in plan) | — |
| Container Registry | Basic | ~$5/mo |
| Static Web Apps | Free | $0 |
| Storage Account | Standard LRS, 50GB | ~$2/mo |
| Key Vault | Standard, <100 ops/day | ~$0.10/mo |
| **Total** | | **~$77/mo** |

To reduce cost in dev/staging: use B1 plan (~$13/mo) — slower embedding inference
but functional. The embedding model (all-MiniLM-L6-v2) needs ~1GB RAM at minimum.

---

## Teardown

```bash
# Remove all Azure resources
./infra/scripts/teardown.sh prod acopilot

# Or manually:
az group delete --name acopilot-prod-rg --yes
```

---

## Troubleshooting

**App Service won't start / 503 errors:**
```bash
az webapp log tail -g acopilot-prod-rg -n acopilot-prod-api
# Common causes: wrong Docker image, missing API key in KV, not enough RAM for embedding model
```

**"Key Vault secret not found" in logs:**
- Make sure the App Service managed identity has the "Key Vault Secrets User" role
- Check the secret name matches exactly (case-sensitive in KV)
- The Bicep templates grant this role automatically — if you created KV manually, run:
  ```bash
  PRINCIPAL_ID=$(az webapp identity show -g acopilot-prod-rg -n acopilot-prod-api --query principalId -o tsv)
  KV_ID=$(az keyvault show -g acopilot-prod-rg -n acopilot-prod-kv --query id -o tsv)
  az role assignment create --role "Key Vault Secrets User" --assignee "$PRINCIPAL_ID" --scope "$KV_ID"
  ```

**ChromaDB data lost after restart:**
- Verify the Azure File Share is mounted: check `WEBSITES_ENABLE_APP_SERVICE_STORAGE=false`
  and the `azureStorageAccounts` section in the App Service config
- The file share mount path should be `/mnt/data` and `DATA_DIR=/mnt/data` in env

**CORS errors in browser:**
- Update `CORS_ORIGINS` in App Service env to include the exact Static Web App URL
- `az webapp config appsettings set -g acopilot-prod-rg -n acopilot-prod-api --settings "CORS_ORIGINS=https://your-swa-url.azurestaticapps.net"`
