param(
    [string]$Service = "comite-torax-app",
    [string]$Region = "southamerica-east1",
    [switch]$OpenUrl,
    [switch]$ShowLogs,
    [switch]$TriggerSchemaSync,
    [switch]$TriggerAdminReset,
    [switch]$TriggerBackup
)

$gcloudCmd = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
$ghCmd = "C:\Program Files\GitHub CLI\gh.exe"

if (-not (Test-Path $gcloudCmd)) {
    Write-Host "gcloud.cmd not found. Install Google Cloud SDK first."
    exit 1
}

if (-not (Test-Path $ghCmd)) {
    Write-Host "GitHub CLI not found at $ghCmd"
    exit 1
}

Write-Host "== Service status =="
& $gcloudCmd run services describe $Service --region $Region --format "table(metadata.name,status.url,status.latestReadyRevisionName)"

$url = & $gcloudCmd run services describe $Service --region $Region --format "value(status.url)"
if ($url) {
    Write-Host "`n== Service URL =="
    Write-Host $url
}

Write-Host "`n== Latest workflow runs =="
& $ghCmd run list --limit 5

if ($TriggerSchemaSync) {
    Write-Host "`n== Triggering schema sync workflow =="
    & $ghCmd workflow run "Sync DB Schema (Manual)"
}

if ($TriggerAdminReset) {
    Write-Host "`n== Triggering admin reset workflow =="
    & $ghCmd workflow run "Reset Admin (Manual)"
}

if ($TriggerBackup) {
    Write-Host "`n== Triggering backup workflow =="
    & $ghCmd workflow run "Backup DB"
}

if ($OpenUrl -and $url) {
    Write-Host "`n== Opening service URL =="
    Start-Process $url
}

if ($ShowLogs) {
    Write-Host "`n== Latest service logs =="
    & $gcloudCmd run services logs read $Service --region $Region --limit 50
}
