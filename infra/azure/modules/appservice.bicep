// App Service Plan + Backend Web App (Python, Docker)
param planName string
param backendName string
param location string
param acrLoginServer string
param acrName string
param environment string
param prefix string
param llmProvider string
param storageAccountName string
param storageAccountKey string
param fileShareName string
param kvUri string

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

resource plan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: planName
  location: location
  kind: 'linux'
  sku: {
    // B2: 2 vCPUs, 3.5GB RAM — minimum for sentence-transformers embedding model
    name: 'B2'
    tier: 'Basic'
  }
  properties: {
    reserved: true  // required for Linux
  }
}

resource backend 'Microsoft.Web/sites@2023-01-01' = {
  name: backendName
  location: location
  kind: 'app,linux,container'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    siteConfig: {
      linuxFxVersion: 'DOCKER|${acrLoginServer}/${prefix}-backend:latest'
      acrUseManagedIdentityCreds: false
      acrUserManagedIdentityID: ''
      appSettings: [
        { name: 'DOCKER_REGISTRY_SERVER_URL',      value: 'https://${acrLoginServer}' }
        { name: 'DOCKER_REGISTRY_SERVER_USERNAME',  value: acr.listCredentials().username }
        { name: 'DOCKER_REGISTRY_SERVER_PASSWORD',  value: acr.listCredentials().passwords[0].value }
        { name: 'WEBSITES_ENABLE_APP_SERVICE_STORAGE', value: 'false' }
        // LLM config — API keys are read from Key Vault via secret references
        { name: 'LLM_PROVIDER',   value: llmProvider }
        { name: 'DRAFT_MODEL',    value: 'claude-sonnet-4-6' }
        { name: 'VERIFY_MODEL',   value: 'claude-sonnet-4-6' }
        { name: 'EMBEDDING_MODEL', value: 'all-MiniLM-L6-v2' }
        // Abstain threshold
        { name: 'ABSTAIN_THRESHOLD', value: '0.75' }
        // Data paths — mapped to the Azure File Share mount
        { name: 'FILINGS_DIR', value: '/mnt/data/filings' }
        { name: 'DATA_DIR',    value: '/mnt/data' }
        // CORS — will be updated once Static Web App hostname is known
        { name: 'CORS_ORIGINS', value: '*' }
        { name: 'API_HOST',  value: '0.0.0.0' }
        { name: 'API_PORT',  value: '8000' }
        // Key Vault reference for API key (add secret to KV separately)
        { name: 'ANTHROPIC_API_KEY', value: '@Microsoft.KeyVault(SecretUri=${kvUri}secrets/ANTHROPIC-API-KEY/)' }
        { name: 'OPENAI_API_KEY',    value: '@Microsoft.KeyVault(SecretUri=${kvUri}secrets/OPENAI-API-KEY/)' }
        { name: 'GEMINI_API_KEY',    value: '@Microsoft.KeyVault(SecretUri=${kvUri}secrets/GEMINI-API-KEY/)' }
        // SCM / deployment
        { name: 'WEBSITES_PORT', value: '8000' }
      ]
      azureStorageAccounts: {
        data: {
          type: 'AzureFiles'
          accountName: storageAccountName
          shareName: fileShareName
          mountPath: '/mnt/data'
          accessKey: storageAccountKey
        }
      }
      alwaysOn: true
      httpLoggingEnabled: true
      detailedErrorLoggingEnabled: true
    }
    httpsOnly: true
  }
}

// Grant the App Service managed identity read access to Key Vault secrets
resource kvRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(backendName, 'KeyVaultSecretsUser')
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '4633458b-17de-408a-b874-0445c86b69e6'  // Key Vault Secrets User
    )
    principalId: backend.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output backendUrl string = 'https://${backend.properties.defaultHostName}'
output principalId string = backend.identity.principalId
