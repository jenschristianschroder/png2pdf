#!/usr/bin/env pwsh
# Post-provision hook - updates the Container App with final API config
# that was not available during the initial Bicep deployment (circular dependency),
# and configures Entra ID authentication (Easy Auth).

Write-Host "Updating Container App environment variables with API config..."

$rgName    = "rg-$env:AZURE_ENV_NAME"
$appName   = $env:CONTAINER_APP_NAME
$subId     = $env:AZURE_SUBSCRIPTION_ID
$funcUrl   = $env:FUNCTION_URL
$apiUri    = $env:API_IDENTIFIER_URI
$webClientId = $env:WEB_CLIENT_ID
$tenantId  = $env:AZURE_TENANT_ID

if (-not $appName) {
    Write-Warning "CONTAINER_APP_NAME env var is not set - cannot update."
    exit 0
}

Write-Host "  Container App      : $appName"
Write-Host "  Function URL       : $funcUrl"
Write-Host "  API Identifier URI : $apiUri"

az containerapp update -n $appName -g $rgName --subscription $subId --set-env-vars "FUNCTION_URL=$funcUrl" "API_IDENTIFIER_URI=$apiUri"

Write-Host "Container App updated successfully."

# ─── Entra ID authentication (Easy Auth) ───
if (-not $webClientId) {
    Write-Warning "WEB_CLIENT_ID env var is not set - skipping auth configuration."
    exit 0
}

Write-Host ""
Write-Host "Configuring Entra ID authentication on Container App..."
Write-Host "  Web Client ID : $webClientId"
Write-Host "  Tenant ID     : $tenantId"

# Create/reset client secret for the web app registration (30-day max per org policy)
Write-Host "  Creating client secret for web app registration..."
$endDate = (Get-Date).AddDays(30).ToString("yyyy-MM-dd")
$secret = az ad app credential reset --id $webClientId --display-name "ContainerAppEasyAuth" --end-date $endDate --query "password" -o tsv 2>&1 | Where-Object { $_ -notmatch "^WARNING:" } | Select-Object -First 1
if (-not $secret) {
    Write-Warning "Failed to create client secret. Skipping auth configuration."
    exit 0
}
Write-Host "  Client secret created."

# Enable authentication with redirect for unauthenticated users
Write-Host "  Enabling authentication..."
az containerapp auth update `
    --name $appName `
    --resource-group $rgName `
    --subscription $subId `
    --enabled true `
    --unauthenticated-client-action RedirectToLoginPage `
    --redirect-provider azureactivedirectory

# Configure Microsoft (Entra ID) identity provider
Write-Host "  Configuring Microsoft identity provider..."
az containerapp auth microsoft update `
    --name $appName `
    --resource-group $rgName `
    --subscription $subId `
    --client-id $webClientId `
    --client-secret $secret `
    --tenant-id $tenantId `
    --yes

Write-Host "Entra ID authentication configured successfully."
