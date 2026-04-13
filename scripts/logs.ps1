param(
    [string]$Service = "comite-torax-app",
    [string]$Region = "southamerica-east1",
    [int]$Limit = 200
)

$gcloudCmd = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"

if (-Not (Test-Path $gcloudCmd)) {
    Write-Host "gcloud.cmd not found. Please install Google Cloud SDK."
    exit 1
}

& $gcloudCmd run services logs read $Service --region $Region --limit $Limit

