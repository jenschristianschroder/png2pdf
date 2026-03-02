// Entra ID App Registration
// Creates API app registration for the Function App (Easy Auth)
// Uses the Microsoft Graph Bicep extension

extension 'br:mcr.microsoft.com/bicep/extensions/microsoftgraph/v1.0:1.0.0'

@description('Base name for the resources')
param name string

@description('Entra ID tenant ID')
param tenantId string = tenant().tenantId

@description('Web app URL for redirect URI (Container App URL)')
param webAppUrl string = ''

// ─── API App Registration (protects the Function) ───
resource apiApp 'Microsoft.Graph/applications@v1.0' = {
  displayName: '${name}-api'
  uniqueName: '${name}-api'
  signInAudience: 'AzureADMyOrg'
  identifierUris: [
    'api://${name}-api'
  ]
  api: {
    requestedAccessTokenVersion: 2
  }
  web: {
    implicitGrantSettings: {
      enableAccessTokenIssuance: false
      enableIdTokenIssuance: false
    }
  }
}

resource apiServicePrincipal 'Microsoft.Graph/servicePrincipals@v1.0' = {
  appId: apiApp.appId
}

// ─── Web App Registration (Entra ID auth for Container App) ───
resource webApp 'Microsoft.Graph/applications@v1.0' = {
  displayName: '${name}-web'
  uniqueName: '${name}-web'
  signInAudience: 'AzureADMyOrg'
  web: {
    redirectUris: !empty(webAppUrl) ? ['${webAppUrl}/.auth/login/aad/callback'] : []
    implicitGrantSettings: {
      enableAccessTokenIssuance: false
      enableIdTokenIssuance: true
    }
  }
}

resource webServicePrincipal 'Microsoft.Graph/servicePrincipals@v1.0' = {
  appId: webApp.appId
}

@description('API App client ID')
output apiClientId string = apiApp.appId

@description('API identifier URI')
output apiIdentifierUri string = apiApp.identifierUris[0]

@description('Web App client ID')
output webClientId string = webApp.appId

@description('Tenant ID')
output tenantIdOutput string = tenantId
