// Azure Container Registry (Basic SKU)
// Hosts the Docker image for the Flask web UI

@description('Base name for the resource')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Resource tags')
param tags object = {}

var resourceSuffix = take(uniqueString(subscription().id, resourceGroup().id, name), 6)
// ACR names must be alphanumeric, 5-50 chars
var acrName = replace('acr${name}${resourceSuffix}', '-', '')

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: acrName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: true
  }
}

@description('ACR login server')
output loginServer string = containerRegistry.properties.loginServer

@description('ACR resource ID')
output id string = containerRegistry.id

@description('ACR name')
output acrName string = containerRegistry.name
