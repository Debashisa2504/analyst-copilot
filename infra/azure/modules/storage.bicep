// Storage Account + Azure File Share for persistent ChromaDB / BM25 data
param storageName string
param location string
param fileShareName string = 'analyst-copilot-data'

resource storage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
  }
}

resource fileServices 'Microsoft.Storage/storageAccounts/fileServices@2023-01-01' = {
  parent: storage
  name: 'default'
}

resource fileShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-01-01' = {
  parent: fileServices
  name: fileShareName
  properties: { shareQuota: 50 }
}

output storageKey string = storage.listKeys().keys[0].value
output fileShareName string = fileShareName
output storageName string = storageName
