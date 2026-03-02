// Application Insights
// APM for the Azure Function App

@description('Base name for the resource')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Resource tags')
param tags object = {}

@description('Log Analytics workspace ID')
param logAnalyticsWorkspaceId string

var resourceSuffix = take(uniqueString(subscription().id, resourceGroup().id, name), 6)
var appInsightsName = 'appi-${name}-${resourceSuffix}'

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalyticsWorkspaceId
  }
}

@description('Resource ID')
output id string = appInsights.id

@description('Instrumentation key')
output instrumentationKey string = appInsights.properties.InstrumentationKey

@description('Connection string')
output connectionString string = appInsights.properties.ConnectionString
