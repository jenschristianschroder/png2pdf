#!/usr/bin/env pwsh
# Pre-deploy hook — temporarily allows the deployer's public IP on the storage
# account firewall so that azd can upload the Function App zip package.
# The postdeploy hook removes this rule after deployment completes.

$storageAccountName = $env:STORAGE_ACCOUNT_NAME
$rgName             = "rg-$env:AZURE_ENV_NAME"
$subId              = $env:AZURE_SUBSCRIPTION_ID

if (-not $storageAccountName) {
    Write-Warning "STORAGE_ACCOUNT_NAME env var is not set — skipping firewall exception."
    exit 0
}

Write-Host "Detecting deployer public IP..."
$deployerIp = (Invoke-WebRequest -Uri 'https://api.ipify.org' -UseBasicParsing -TimeoutSec 10).Content.Trim()
if (-not $deployerIp) {
    Write-Warning "Could not detect public IP — skipping firewall exception."
    exit 0
}
Write-Host "  Deployer IP: $deployerIp"

Write-Host "Adding temporary firewall rule on storage account '$storageAccountName'..."
az storage account network-rule add `
    --account-name $storageAccountName `
    --resource-group $rgName `
    --subscription $subId `
    --ip-address $deployerIp `
    --only-show-errors

# Wait for the network rule to propagate
Write-Host "Waiting 30 seconds for firewall rule propagation..."
Start-Sleep -Seconds 30

Write-Host "Deployer IP firewall rule added. Proceeding with deployment."
