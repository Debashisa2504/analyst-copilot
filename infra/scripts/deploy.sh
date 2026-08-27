#!/usr/bin/env bash
# =============================================================================
# deploy.sh — Full Azure deployment for The Analyst Copilot
# =============================================================================
# Usage:
#   ./infra/scripts/deploy.sh [OPTIONS]
#
# Options:
#   -e, --env        Environment: dev | staging | prod   (default: prod)
#   -p, --prefix     Resource prefix (3-8 chars)         (default: acopilot)
#   -l, --location   Azure region                        (default: eastus)
#   --provider       LLM provider: anthropic|openai|gemini (default: anthropic)
#   --api-key        LLM API key (stored in Key Vault)
#   --skip-build     Skip Docker build (use existing image)
#   --skip-infra     Skip Bicep deployment (use existing infra)
#   -h, --help       Show this help
#
# Prerequisites:
#   az CLI logged in (az login), Docker running, jq installed
# =============================================================================
set -euo pipefail

# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------
ENV="prod"
PREFIX="acopilot"
LOCATION="eastus"
LLM_PROVIDER="anthropic"
API_KEY=""
SKIP_BUILD=false
SKIP_INFRA=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# --------------------------------------------------------------------------
# Parse args
# --------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case $1 in
    -e|--env)      ENV="$2"; shift 2 ;;
    -p|--prefix)   PREFIX="$2"; shift 2 ;;
    -l|--location) LOCATION="$2"; shift 2 ;;
    --provider)    LLM_PROVIDER="$2"; shift 2 ;;
    --api-key)     API_KEY="$2"; shift 2 ;;
    --skip-build)  SKIP_BUILD=true; shift ;;
    --skip-infra)  SKIP_INFRA=true; shift ;;
    -h|--help)
      head -35 "$0" | tail -30
      exit 0
      ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

# Derived names (must match main.bicep)
RG_NAME="${PREFIX}-${ENV}-rg"
ACR_NAME="${PREFIX}${ENV}acr"
BACKEND_APP="${PREFIX}-${ENV}-api"
BACKEND_IMAGE="${PREFIX}-backend"
FRONTEND_IMAGE="${PREFIX}-frontend"

echo "======================================================================"
echo " The Analyst Copilot — Azure Deployment"
echo "   Environment : ${ENV}"
echo "   Prefix      : ${PREFIX}"
echo "   Location    : ${LOCATION}"
echo "   LLM Provider: ${LLM_PROVIDER}"
echo "   Resource Grp: ${RG_NAME}"
echo "======================================================================"

# --------------------------------------------------------------------------
# Step 1: Verify prerequisites
# --------------------------------------------------------------------------
echo ""
echo "[1/7] Checking prerequisites…"
command -v az    >/dev/null 2>&1 || { echo "ERROR: az CLI not found. Install from https://aka.ms/installazurecli"; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "ERROR: Docker not found."; exit 1; }
command -v jq    >/dev/null 2>&1 || { echo "ERROR: jq not found. Install with: brew install jq / apt-get install jq"; exit 1; }

# Check az login
az account show --query id -o tsv >/dev/null 2>&1 || {
  echo "ERROR: Not logged in to Azure. Run: az login"
  exit 1
}

SUBSCRIPTION_ID=$(az account show --query id -o tsv)
echo "    Azure subscription: ${SUBSCRIPTION_ID}"
echo "    ✓ Prerequisites OK"

# --------------------------------------------------------------------------
# Step 2: Deploy Azure infrastructure via Bicep
# --------------------------------------------------------------------------
if [ "$SKIP_INFRA" = false ]; then
  echo ""
  echo "[2/7] Deploying Azure infrastructure (Bicep)…"
  DEPLOY_OUTPUT=$(az deployment sub create \
    --name "analyst-copilot-${ENV}-$(date +%Y%m%d%H%M%S)" \
    --location "${LOCATION}" \
    --template-file "${PROJECT_ROOT}/infra/azure/main.bicep" \
    --parameters \
      location="${LOCATION}" \
      environment="${ENV}" \
      prefix="${PREFIX}" \
      llmProvider="${LLM_PROVIDER}" \
    --output json)

  ACR_LOGIN_SERVER=$(echo "$DEPLOY_OUTPUT" | jq -r '.properties.outputs.acrLoginServer.value')
  BACKEND_URL=$(echo "$DEPLOY_OUTPUT" | jq -r '.properties.outputs.backendUrl.value')
  FRONTEND_URL=$(echo "$DEPLOY_OUTPUT" | jq -r '.properties.outputs.frontendUrl.value')
  KV_URI=$(echo "$DEPLOY_OUTPUT" | jq -r '.properties.outputs.keyVaultUri.value')
  echo "    ✓ Infrastructure deployed"
  echo "    ACR:      ${ACR_LOGIN_SERVER}"
  echo "    Backend:  ${BACKEND_URL}"
  echo "    Frontend: ${FRONTEND_URL}"
else
  echo ""
  echo "[2/7] Skipping infrastructure deployment (--skip-infra)"
  ACR_LOGIN_SERVER=$(az acr show -n "${ACR_NAME}" --query loginServer -o tsv 2>/dev/null || echo "")
  BACKEND_URL="https://$(az webapp show -g "${RG_NAME}" -n "${BACKEND_APP}" --query defaultHostName -o tsv 2>/dev/null || echo "unknown")"
  FRONTEND_URL="(run without --skip-infra to get URL)"
  KV_URI=$(az keyvault show -g "${RG_NAME}" -n "${PREFIX}-${ENV}-kv" --query properties.vaultUri -o tsv 2>/dev/null || echo "")
fi

# --------------------------------------------------------------------------
# Step 3: Store API key in Key Vault
# --------------------------------------------------------------------------
echo ""
echo "[3/7] Storing LLM API key in Key Vault…"
if [ -n "${API_KEY}" ] && [ -n "${KV_URI}" ]; then
  KV_NAME=$(echo "${KV_URI}" | sed 's|https://||' | sed 's|\.vault\.azure\.net/||')
  case "${LLM_PROVIDER}" in
    anthropic) SECRET_NAME="ANTHROPIC-API-KEY" ;;
    openai)    SECRET_NAME="OPENAI-API-KEY" ;;
    gemini)    SECRET_NAME="GEMINI-API-KEY" ;;
    *)         SECRET_NAME="LLM-API-KEY" ;;
  esac
  az keyvault secret set \
    --vault-name "${KV_NAME}" \
    --name "${SECRET_NAME}" \
    --value "${API_KEY}" \
    --output none
  echo "    ✓ API key stored as secret '${SECRET_NAME}'"
else
  echo "    ⚠ No --api-key provided or KV URI missing."
  echo "      Manually add secret to Key Vault: ${KV_URI}"
  echo "      az keyvault secret set --vault-name <kv> --name ANTHROPIC-API-KEY --value <key>"
fi

# --------------------------------------------------------------------------
# Step 4: Build and push Docker images
# --------------------------------------------------------------------------
if [ "$SKIP_BUILD" = false ] && [ -n "${ACR_LOGIN_SERVER}" ]; then
  echo ""
  echo "[4/7] Building and pushing Docker images to ACR…"

  # Login to ACR
  az acr login --name "${ACR_NAME}"

  # Backend
  echo "    Building backend image…"
  docker build \
    -t "${ACR_LOGIN_SERVER}/${BACKEND_IMAGE}:latest" \
    -f "${PROJECT_ROOT}/backend/Dockerfile" \
    "${PROJECT_ROOT}"
  docker push "${ACR_LOGIN_SERVER}/${BACKEND_IMAGE}:latest"
  echo "    ✓ Backend image pushed"

  # Frontend (built with backend URL baked in)
  echo "    Building frontend image…"
  docker build \
    --build-arg "VITE_API_BASE=${BACKEND_URL}" \
    -t "${ACR_LOGIN_SERVER}/${FRONTEND_IMAGE}:latest" \
    -f "${PROJECT_ROOT}/frontend/Dockerfile" \
    "${PROJECT_ROOT}/frontend"
  docker push "${ACR_LOGIN_SERVER}/${FRONTEND_IMAGE}:latest"
  echo "    ✓ Frontend image pushed"
else
  echo ""
  echo "[4/7] Skipping Docker build (--skip-build or no ACR server)"
fi

# --------------------------------------------------------------------------
# Step 5: Restart the App Service to pull new image
# --------------------------------------------------------------------------
echo ""
echo "[5/7] Restarting App Service…"
if az webapp restart -g "${RG_NAME}" -n "${BACKEND_APP}" --output none 2>/dev/null; then
  echo "    ✓ Backend restarted"
else
  echo "    ⚠ Could not restart backend (may not exist yet)"
fi

# --------------------------------------------------------------------------
# Step 6: Deploy React frontend to Static Web App
# --------------------------------------------------------------------------
echo ""
echo "[6/7] Deploying frontend to Azure Static Web Apps…"
SWA_NAME="${PREFIX}-${ENV}-web"
SWA_TOKEN=$(az staticwebapp secrets list -n "${SWA_NAME}" -g "${RG_NAME}" \
  --query properties.apiKey -o tsv 2>/dev/null || echo "")

if [ -n "${SWA_TOKEN}" ]; then
  cd "${PROJECT_ROOT}/frontend"
  # Build with Azure backend URL
  VITE_API_BASE="${BACKEND_URL}" npm run build
  npx @azure/static-web-apps-cli deploy ./dist \
    --deployment-token "${SWA_TOKEN}" \
    --env "production" \
    --no-use-keychain
  echo "    ✓ Frontend deployed to Static Web App"
  cd "${PROJECT_ROOT}"
else
  echo "    ⚠ Could not get SWA deployment token."
  echo "      Manually deploy from the Azure portal or set up GitHub Actions."
fi

# --------------------------------------------------------------------------
# Step 7: Health check
# --------------------------------------------------------------------------
echo ""
echo "[7/7] Running health check…"
sleep 15  # Give app service a moment to start
if curl -sf "${BACKEND_URL}/health" >/dev/null 2>&1; then
  echo "    ✓ Backend health check passed: ${BACKEND_URL}/health"
else
  echo "    ⚠ Backend not yet reachable (may still be starting)."
  echo "      Check: curl ${BACKEND_URL}/health"
  echo "      Logs:  az webapp log tail -g ${RG_NAME} -n ${BACKEND_APP}"
fi

# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------
echo ""
echo "======================================================================"
echo " Deployment complete!"
echo "======================================================================"
echo ""
echo "  Backend API : ${BACKEND_URL}"
echo "  Frontend UI : ${FRONTEND_URL}"
echo "  Health check: ${BACKEND_URL}/health"
echo ""
echo "  Next steps:"
echo "  1. If you haven't yet, add your LLM API key to Key Vault:"
echo "     az keyvault secret set --vault-name ${PREFIX}-${ENV}-kv \\"
echo "       --name ANTHROPIC-API-KEY --value <your_key>"
echo ""
echo "  2. Upload a test filing:"
echo "     curl -X POST ${BACKEND_URL}/upload \\"
echo "       -F 'files=@<path_to_filing.htm>'"
echo ""
echo "  3. Ask a question:"
echo "     curl -X POST ${BACKEND_URL}/answer \\"
echo "       -H 'Content-Type: application/json' \\"
echo "       -d '{\"question\":\"What was FY2018 capital expenditure?\",\"doc_name\":\"ALL\"}'"
echo ""
echo "  4. Tail logs if anything looks wrong:"
echo "     az webapp log tail -g ${RG_NAME} -n ${BACKEND_APP}"
echo "======================================================================"
