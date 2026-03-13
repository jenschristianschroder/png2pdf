#!/usr/bin/env pwsh
# Post-deploy hook — removes the temporary deployer IP firewall rule that was
# added by predeploy.ps1. Ensures the storage account returns to private-only access.

$storageAccountName = $env:STORAGE_ACCOUNT_NAME
$rgName             = "rg-$env:AZURE_ENV_NAME"
$subId              = $env:AZURE_SUBSCRIPTION_ID

if (-not $storageAccountName) {
    Write-Warning "STORAGE_ACCOUNT_NAME env var is not set — skipping firewall cleanup."
    exit 0
}

Write-Host "Detecting deployer public IP..."
$deployerIp = (Invoke-WebRequest -Uri 'https://api.ipify.org' -UseBasicParsing -TimeoutSec 10).Content.Trim()
if (-not $deployerIp) {
    Write-Warning "Could not detect public IP — skipping firewall cleanup."
    exit 0
}
Write-Host "  Deployer IP: $deployerIp"

Write-Host "Removing temporary firewall rule from storage account '$storageAccountName'..."
az storage account network-rule remove `
    --account-name $storageAccountName `
    --resource-group $rgName `
    --subscription $subId `
    --ip-address $deployerIp `
    --only-show-errors 2>&1 | Out-Null

Write-Host "Deployer IP firewall rule removed. Storage account is private-only."
