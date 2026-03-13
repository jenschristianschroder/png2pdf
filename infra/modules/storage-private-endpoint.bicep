// Private endpoint for the storage account's blob sub-resource.
// Routes blob traffic through the VNet instead of the public internet.

@description('Base name for the resource')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Resource tags')
param tags object = {}

@description('Storage account resource ID')
param storageAccountId string

@description('Subnet resource ID for the private endpoint')
param subnetId string

@description('Private DNS zone resource ID for blob storage')
param privateDnsZoneId string

var resourceSuffix = take(uniqueString(subscription().id, resourceGroup().id, name), 6)
var privateEndpointName = 'pe-stfunc-${name}-${resourceSuffix}'

// ─── Private Endpoint for blob storage ───
resource privateEndpoint 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: privateEndpointName
  location: location
  tags: tags
  properties: {
    subnet: {
      id: subnetId
    }
    privateLinkServiceConnections: [
      {
        name: privateEndpointName
        properties: {
          privateLinkServiceId: storageAccountId
          groupIds: [
            'blob'
          ]
        }
      }
    ]
  }
}

// ─── DNS zone group — auto-registers A record in private DNS zone ───
resource privateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {
  parent: privateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'privatelink-blob-core-windows-net'
        properties: {
          privateDnsZoneId: privateDnsZoneId
        }
      }
    ]
  }
}

@description('Private endpoint resource ID')
output id string = privateEndpoint.id
