// infra/azure/main.bicep
// -------------------------------------------------------------------
// Analyst Copilot — Azure infrastructure
// Deploys:
//   - Resource Group (via subscription-scope deployment)
//   - App Service Plan (Linux, B2 tier — enough for embedding model)
//   - Backend Web App (Python 3.11 container from ACR)
//   - Frontend Static Web App (React SPA)
//   - Azure Container Registry (builds pushed from CI)
//   - Azure File Share (persistent storage for ChromaDB + BM25 indexes)
//   - Storage Account (for the file share)
//   - Key Vault (API keys — never in env vars in plaintext)
// -------------------------------------------------------------------

targetScope = 'subscription'

@description('Azure region for all resources')
param location string = 'eastus'

@description('Short environment tag: dev | staging | prod')
param environment string = 'prod'

@description('Unique prefix for resource names (3-8 lowercase alphanum)')
param prefix string = 'acopilot'

@description('LLM provider: anthropic | openai | gemini')
param llmProvider string = 'anthropic'

var rgName      = '${prefix}-${environment}-rg'
var acrName     = replace('${prefix}${environment}acr', '-', '')
var planName    = '${prefix}-${environment}-plan'
var backendName = '${prefix}-${environment}-api'
var frontendName= '${prefix}-${environment}-web'
var storageName = replace('${prefix}${environment}stor', '-', '')
var kvName      = '${prefix}-${environment}-kv'

// --------------------------------------------------------------------------
// Resource Group
// --------------------------------------------------------------------------
resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: rgName
  location: location
}

// --------------------------------------------------------------------------
// Storage Account + File Share (for persistent data volume)
// --------------------------------------------------------------------------
module storage './modules/storage.bicep' = {
  name: 'storage'
  scope: rg
  params: {
    storageName: storageName
    location: location
  }
}

// --------------------------------------------------------------------------
// Container Registry
// --------------------------------------------------------------------------
module acr './modules/acr.bicep' = {
  name: 'acr'
  scope: rg
  params: {
    acrName: acrName
    location: location
  }
}

// --------------------------------------------------------------------------
// Key Vault
// --------------------------------------------------------------------------
module kv './modules/keyvault.bicep' = {
  name: 'keyvault'
  scope: rg
  params: {
    kvName: kvName
    location: location
  }
}

// --------------------------------------------------------------------------
// App Service Plan + Backend Web App
// --------------------------------------------------------------------------
module appService './modules/appservice.bicep' = {
  name: 'appservice'
  scope: rg
  params: {
    planName: planName
    backendName: backendName
    location: location
    acrLoginServer: acr.outputs.loginServer
    acrName: acrName
    environment: environment
    prefix: prefix
    llmProvider: llmProvider
    storageAccountName: storageName
    storageAccountKey: storage.outputs.storageKey
    fileShareName: storage.outputs.fileShareName
    kvUri: kv.outputs.kvUri
  }
  dependsOn: [acr, storage, kv]
}

// --------------------------------------------------------------------------
// Static Web App (Frontend)
// --------------------------------------------------------------------------
module staticWeb './modules/staticwebapp.bicep' = {
  name: 'staticwebapp'
  scope: rg
  params: {
    frontendName: frontendName
    location: location
    backendUrl: appService.outputs.backendUrl
  }
}

// --------------------------------------------------------------------------
// Outputs
// --------------------------------------------------------------------------
output resourceGroup string = rgName
output backendUrl string = appService.outputs.backendUrl
output frontendUrl string = staticWeb.outputs.frontendUrl
output acrLoginServer string = acr.outputs.loginServer
output keyVaultUri string = kv.outputs.kvUri
