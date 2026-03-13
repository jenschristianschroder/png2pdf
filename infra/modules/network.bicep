// Virtual Network, subnets, and private DNS zone for storage private endpoints.
// Provides network isolation so the solution works with publicNetworkAccess: Disabled
// on the storage account (enforced nightly by Azure Policy).

@description('Base name for the resource')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Resource tags')
param tags object = {}

var resourceSuffix = take(uniqueString(subscription().id, resourceGroup().id, name), 6)
var vnetName = 'vnet-${name}-${resourceSuffix}'

// ─── Virtual Network ───
resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.0.0.0/16'
      ]
    }
    subnets: [
      {
        // Container Apps Environment requires a /23 minimum
        name: 'snet-container-apps'
        properties: {
          addressPrefix: '10.0.0.0/23'
          delegations: [
            {
              name: 'Microsoft.App.environments'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        // Function App (Flex Consumption) VNet integration
        name: 'snet-functions'
        properties: {
          addressPrefix: '10.0.2.0/24'
          delegations: [
            {
              name: 'Microsoft.Web.serverFarms'
              properties: {
                serviceName: 'Microsoft.Web/serverFarms'
              }
            }
          ]
        }
      }
      {
        // Private endpoints — no delegation required
        name: 'snet-private-endpoints'
        properties: {
          addressPrefix: '10.0.3.0/24'
        }
      }
    ]
  }
}

// ─── Private DNS Zone for blob storage ───
resource privateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.blob.${environment().suffixes.storage}'
  location: 'global'
  tags: tags
}

// ─── Link the private DNS zone to the VNet ───
resource privateDnsZoneVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: privateDnsZone
  name: '${vnetName}-link'
  location: 'global'
  tags: tags
  properties: {
    virtualNetwork: {
      id: vnet.id
    }
    registrationEnabled: false
  }
}

@description('Virtual Network resource ID')
output vnetId string = vnet.id

@description('Container Apps Environment subnet resource ID')
output containerAppsSubnetId string = vnet.properties.subnets[0].id

@description('Functions subnet resource ID')
output functionsSubnetId string = vnet.properties.subnets[1].id

@description('Private endpoints subnet resource ID')
output privateEndpointsSubnetId string = vnet.properties.subnets[2].id

@description('Private DNS zone resource ID for blob storage')
output privateDnsZoneId string = privateDnsZone.id
