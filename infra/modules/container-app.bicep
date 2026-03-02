// Azure Container Apps — Flask web UI
// Serverless container hosting for the frontend

@description('Base name for the resource')
param name string

@description('Azure region')
param location string = resourceGroup().location

@description('Resource tags')
param tags object = {}

@description('Log Analytics workspace customer ID')
param logAnalyticsCustomerId string

@description('Log Analytics workspace primary shared key')
@secure()
param logAnalyticsPrimaryKey string

@description('ACR login server')
param acrLoginServer string

@description('ACR admin username')
param acrUsername string

@description('ACR admin password')
@secure()
param acrPassword string

@description('Container image (e.g. myacr.azurecr.io/web:latest). Leave empty to use a placeholder.')
param containerImage string = ''

var useAcr = !empty(containerImage)
var effectiveImage = useAcr ? containerImage : 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Azure Function URL for the API backend')
param functionUrl string = ''

@description('API identifier URI for managed identity token audience')
param apiIdentifierUri string = ''

var resourceSuffix = take(uniqueString(subscription().id, resourceGroup().id, name), 6)
var envName = 'cae-${name}-${resourceSuffix}'
var appName = 'ca-${name}-${resourceSuffix}'

// ─── Container Apps Environment ───
resource containerAppEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: envName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: logAnalyticsPrimaryKey
      }
    }
  }
}

// ─── Container App ───
resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  tags: union(tags, { 'azd-service-name': 'web' })
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
        allowInsecure: false
      }
      registries: [
        {
          server: acrLoginServer
          username: acrUsername
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acrPassword
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'web'
          image: effectiveImage
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            {
              name: 'FUNCTION_URL'
              value: functionUrl
            }
            {
              name: 'API_IDENTIFIER_URI'
              value: apiIdentifierUri
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 2
      }
    }
  }
}

@description('Container App FQDN')
output fqdn string = containerApp.properties.configuration.ingress.fqdn

@description('Container App URL')
output url string = 'https://${containerApp.properties.configuration.ingress.fqdn}'

@description('Container App resource name')
output appName string = containerApp.name

@description('Container App resource ID')
output id string = containerApp.id

@description('Container App system-assigned managed identity principal ID')
output principalId string = containerApp.identity.principalId
