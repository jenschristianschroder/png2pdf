// ACR Credentials — helper to extract admin username/password
// Needed because listCredentials is a runtime function that can't be called
// directly in a subscription-scoped template on a module output reference.

@description('Name of the Azure Container Registry')
param acrName string

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: acrName
}

@description('ACR admin username')
#disable-next-line outputs-should-not-contain-secrets
output username string = acr.listCredentials().username

@description('ACR admin credential value')
#disable-next-line outputs-should-not-contain-secrets
output acrCredential string = acr.listCredentials().passwords[0].value
