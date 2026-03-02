// Log Analytics Workspace
// Shared logging backend for Application Insights and Container Apps

@description('Base name for the resource')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Resource tags')
param tags object = {}

var resourceSuffix = take(uniqueString(subscription().id, resourceGroup().id, name), 6)
var workspaceName = 'law-${name}-${resourceSuffix}'

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

@description('Resource ID of the workspace')
output id string = logAnalytics.id

@description('Workspace name')
output workspaceName string = logAnalytics.name

@description('Customer ID (workspace ID) for agents/container apps')
output customerId string = logAnalytics.properties.customerId

@description('Primary shared key')
#disable-next-line outputs-should-not-contain-secrets
output primarySharedKey string = logAnalytics.listKeys().primarySharedKey
