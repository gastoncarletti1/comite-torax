param(
    [string]$Service = "comite-torax-app",
    [string]$Region = "southamerica-east1"
)

$gcloudCmd = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"

if (-Not (Test-Path $gcloudCmd)) {
    Write-Host "gcloud.cmd not found. Please install Google Cloud SDK."
    exit 1
}

& $gcloudCmd run services describe $Service --region $Region

