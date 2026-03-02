// Azure Function App — Flex Consumption plan, Python 3.11
// Hosts the PNG-to-PDF HTTP trigger
// Uses System Assigned Managed Identity for storage (no shared keys)

@description('Base name for the resource')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Resource tags')
param tags object = {}

@description('Application Insights connection string')
param appInsightsConnectionString string

@description('Allowed CORS origins (Container Apps FQDN)')
param allowedOrigins array = []

@description('Entra ID client ID for Easy Auth (API app registration)')
param authClientId string = ''

@description('Entra ID tenant ID')
param authTenantId string = ''

@description('API identifier URI for audience validation')
param authIdentifierUri string = ''

var resourceSuffix = take(uniqueString(subscription().id, resourceGroup().id, name), 6)
var funcAppName = 'func-${name}-${resourceSuffix}'
var planName = 'asp-${name}-${resourceSuffix}'
// Storage account names: 3-24 lowercase alphanum
var storageName = take('stfunc${replace(name, '-', '')}${resourceSuffix}', 24)
var deploymentContainerName = 'deploymentpackages'

// ─── Storage Account (required by Functions runtime) ───
// No shared key access — uses managed identity only
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowSharedKeyAccess: false
  }
}

// ─── Blob container for Flex Consumption deployment packages ───
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource deploymentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: deploymentContainerName
}

// ─── Role assignments for identity-based storage access ───
// Storage Blob Data Owner – required for AzureWebJobsStorage + deployment
resource storageBlobDataOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, functionApp.id, 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
  scope: storageAccount
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
  }
}

// Storage Queue Data Contributor – required for internal queues
resource storageQueueDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, functionApp.id, '974c5e8b-45b9-4653-ba55-5f855dd0fb88')
  scope: storageAccount
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '974c5e8b-45b9-4653-ba55-5f855dd0fb88')
  }
}

// Storage Table Data Contributor – required for timer/lease management
resource storageTableDataContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, functionApp.id, '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3')
  scope: storageAccount
  properties: {
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3')
  }
}

// ─── Flex Consumption App Service Plan ───
resource hostingPlan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: planName
  location: location
  tags: tags
  kind: 'functionapp'
  sku: {
    name: 'FC1'
    tier: 'FlexConsumption'
  }
  properties: {
    reserved: true // Linux
  }
}

// ─── Function App (Flex Consumption with MI-based storage + deployment) ───
resource functionApp 'Microsoft.Web/sites@2024-04-01' = {
  name: funcAppName
  location: location
  tags: union(tags, { 'azd-service-name': 'api' })
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: hostingPlan.id
    httpsOnly: true
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storageAccount.properties.primaryEndpoints.blob}${deploymentContainerName}'
          authentication: {
            type: 'SystemAssignedIdentity'
          }
        }
      }
      scaleAndConcurrency: {
        maximumInstanceCount: 40
        instanceMemoryMB: 2048
      }
      runtime: {
        name: 'python'
        version: '3.11'
      }
    }
    siteConfig: {
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      appSettings: [
        {
          name: 'AzureWebJobsStorage__accountName'
          value: storageAccount.name
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsightsConnectionString
        }
        {
          name: 'AZURE_TENANT_ID'
          value: authTenantId
        }
        {
          name: 'API_CLIENT_ID'
          value: authClientId
        }
      ]
      cors: {
        allowedOrigins: allowedOrigins
        supportCredentials: true
      }
    }
  }
}

// Easy Auth removed — token validation done in application code
// to avoid issuer-mismatch issues with managed identity v1 tokens

@description('Function App default hostname')
output hostname string = functionApp.properties.defaultHostName

@description('Function App URL')
output url string = 'https://${functionApp.properties.defaultHostName}'

@description('Function App resource ID')
output id string = functionApp.id

@description('Function App name')
output functionAppName string = functionApp.name
