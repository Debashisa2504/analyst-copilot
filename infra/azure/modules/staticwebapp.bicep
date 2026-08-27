// Azure Static Web Apps (React SPA)
param frontendName string
param location string
param backendUrl string

resource swa 'Microsoft.Web/staticSites@2023-01-01' = {
  name: frontendName
  location: location
  sku: { name: 'Free', tier: 'Free' }
  properties: {}
}

output frontendUrl string = 'https://${swa.properties.defaultHostname}'
