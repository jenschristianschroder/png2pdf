// PNG-to-PDF — Main Bicep entry point
// Subscription-scoped deployment that creates a resource group and all services.
//
// Deployment order (avoids circular refs):
//   1. Foundation: RG → Log Analytics → App Insights → ACR
//   2. Container App (web UI)     — deployed first so we know its FQDN
//   3. App Registrations          — SPA redirect URI needs the Container App FQDN
//   4. Function App (API)         — CORS + Easy Auth need Container App URL + app reg IDs
//   5. Post-deploy: azd hooks update Container App env vars with final Function URL + auth values

targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the environment (e.g. dev, staging, prod)')
param environmentName string

@description('Primary location for all resources')
param location string

@description('Entra ID tenant ID (auto-detected)')
param tenantId string = tenant().tenantId

@description('Principal ID of the deploying user (for storage blob deployment access)')
param principalId string = ''

// ─── Resource Group ───
resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: {
    'azd-env-name': environmentName
  }
}

// ─── Log Analytics ───
module logAnalytics 'modules/log-analytics.bicep' = {
  name: 'log-analytics'
  scope: rg
  params: {
    name: environmentName
    location: location
    tags: { 'azd-env-name': environmentName }
  }
}

// ─── Application Insights ───
module appInsights 'modules/app-insights.bicep' = {
  name: 'app-insights'
  scope: rg
  params: {
    name: environmentName
    location: location
    tags: { 'azd-env-name': environmentName }
    logAnalyticsWorkspaceId: logAnalytics.outputs.id
  }
}

// ─── Container Registry ───
module acr 'modules/container-registry.bicep' = {
  name: 'container-registry'
  scope: rg
  params: {
    name: environmentName
    location: location
    tags: { 'azd-env-name': environmentName }
  }
}

// ─── ACR Credentials (needed by Container App for image pull) ───
module acrCredentials 'modules/acr-credentials.bicep' = {
  name: 'acr-credentials'
  scope: rg
  params: {
    acrName: acr.outputs.acrName
  }
}

// ─── Container App (Web UI) ───
// Deployed before Function App & App Registration so we have the FQDN.
// MSAL config (client IDs, scopes) and Function URL are injected as env vars
// and updated via azd post-provision hook once all resources exist.
module containerApp 'modules/container-app.bicep' = {
  name: 'container-app'
  scope: rg
  params: {
    name: environmentName
    location: location
    tags: { 'azd-env-name': environmentName }
    logAnalyticsCustomerId: logAnalytics.outputs.customerId
    logAnalyticsPrimaryKey: logAnalytics.outputs.primarySharedKey
    acrLoginServer: acr.outputs.loginServer
    acrUsername: acrCredentials.outputs.username
    acrPassword: acrCredentials.outputs.acrCredential
    containerImage: ''
    // These will be updated by post-provision hook once all resources exist
    functionUrl: ''
    apiIdentifierUri: ''
    // Storage account name is set after Function App deploys (see post-provision hook)
    storageAccountName: ''
  }
}

// ─── App Registrations (Entra ID) ───
// NOTE: The Microsoft Graph Bicep extension requires special permissions.
// If this module fails, create app registrations manually via Azure Portal
// or az CLI and pass the client IDs as parameters.
module appRegistration 'modules/app-registration.bicep' = {
  name: 'app-registration'
  scope: rg
  params: {
    name: environmentName
    tenantId: tenantId
    webAppUrl: containerApp.outputs.url
  }
}

// ─── MCP Container App (MCP Server) ───
module mcpContainerApp 'modules/mcp-container-app.bicep' = {
  name: 'mcp-container-app'
  scope: rg
  params: {
    environmentName: environmentName
    location: location
    containerAppsEnvironmentId: containerApp.outputs.environmentId
    acrLoginServer: acr.outputs.loginServer
    acrUsername: acrCredentials.outputs.username
    acrPassword: acrCredentials.outputs.acrCredential
    imageName: ''
    mcpClientId: appRegistration.outputs.mcpClientId
    tenantId: tenantId
    appInsightsConnectionString: appInsights.outputs.connectionString
    // Storage account name is set after Function App deploys (see post-provision hook)
    storageAccountName: ''
  }
}

// ─── Function App (API) ───
module functionApp 'modules/function-app.bicep' = {
  name: 'function-app'
  scope: rg
  params: {
    name: environmentName
    location: location
    tags: { 'azd-env-name': environmentName }
    appInsightsConnectionString: appInsights.outputs.connectionString
    allowedOrigins: [ containerApp.outputs.url ]
    authClientId: appRegistration.outputs.apiClientId
    authTenantId: tenantId
    authIdentifierUri: appRegistration.outputs.apiIdentifierUri
    webAppPrincipalId: containerApp.outputs.principalId
    mcpAppPrincipalId: mcpContainerApp.outputs.principalId
    deployerPrincipalId: principalId
  }
}

// ─── Outputs (consumed by azd as env vars) ───
output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = tenantId
output FUNCTION_URL string = functionApp.outputs.url
output WEB_URL string = containerApp.outputs.url
output FUNCTION_APP_NAME string = functionApp.outputs.functionAppName
output CONTAINER_APP_NAME string = containerApp.outputs.appName
output ACR_LOGIN_SERVER string = acr.outputs.loginServer
output API_CLIENT_ID string = appRegistration.outputs.apiClientId
output API_IDENTIFIER_URI string = appRegistration.outputs.apiIdentifierUri
output WEB_CLIENT_ID string = appRegistration.outputs.webClientId
output MCP_URL string = 'https://${mcpContainerApp.outputs.fqdn}'
output MCP_CONTAINER_APP_NAME string = mcpContainerApp.outputs.name
output MCP_CLIENT_ID string = appRegistration.outputs.mcpClientId
output MCP_IDENTIFIER_URI string = appRegistration.outputs.mcpIdentifierUri
output STORAGE_ACCOUNT_NAME string = functionApp.outputs.storageAccountName
