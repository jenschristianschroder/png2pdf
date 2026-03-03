// Azure Container Apps — MCP Server
// Hosts the MCP server for Copilot Studio agent integration

@description('Environment name prefix')
param environmentName string

@description('Azure region')
param location string

@description('Container Apps Environment resource ID (shared with web Container App)')
param containerAppsEnvironmentId string

@description('ACR login server')
param acrLoginServer string

@description('ACR admin username')
param acrUsername string

@description('ACR admin password')
@secure()
param acrPassword string

@description('Container image to deploy')
param imageName string

@description('MCP app registration client ID')
param mcpClientId string

@description('Entra ID tenant ID')
param tenantId string = tenant().tenantId

@description('Application Insights connection string')
param appInsightsConnectionString string

@description('Storage account name for PDF blob access')
param storageAccountName string = ''

var resourceSuffix = take(uniqueString(subscription().id, resourceGroup().id, environmentName), 6)
var appName = 'ca-mcp-${environmentName}-${resourceSuffix}'

var useAcr = !empty(imageName)
var effectiveImage = useAcr ? imageName : 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

resource mcpContainerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  tags: {
    'azd-env-name': environmentName
    'azd-service-name': 'mcp'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerAppsEnvironmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'http'
        allowInsecure: false
      }
      secrets: [
        {
          name: 'acr-password'
          value: acrPassword
        }
      ]
      registries: [
        {
          server: acrLoginServer
          username: acrUsername
          passwordSecretRef: 'acr-password'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'mcp-server'
          image: effectiveImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'FUNCTION_URL', value: '' }
            { name: 'API_IDENTIFIER_URI', value: '' }
            { name: 'AZURE_TENANT_ID', value: tenantId }
            { name: 'MCP_CLIENT_ID', value: mcpClientId }
            { name: 'MCP_IDENTIFIER_URI', value: 'api://png2pdf-mcp' }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
            { name: 'STORAGE_ACCOUNT_NAME', value: storageAccountName }
            { name: 'PORT', value: '8080' }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8080
              }
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health'
                port: 8080
              }
              periodSeconds: 10
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

@description('MCP Container App FQDN')
output fqdn string = mcpContainerApp.properties.configuration.ingress.fqdn

@description('MCP Container App URL')
output url string = 'https://${mcpContainerApp.properties.configuration.ingress.fqdn}'

@description('MCP Container App resource name')
output name string = mcpContainerApp.name

@description('MCP Container App system-assigned managed identity principal ID')
output principalId string = mcpContainerApp.identity.principalId
