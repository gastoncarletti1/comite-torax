param(
    [Parameter(Mandatory = $true)]
    [string]$Username
)

$ghCmd = "C:\Program Files\GitHub CLI\gh.exe"

if (-not (Test-Path $ghCmd)) {
    Write-Host "GitHub CLI not found at $ghCmd"
    exit 1
}

Write-Host "Triggering Promote User (Manual) for $Username"
& $ghCmd workflow run "Promote User (Manual)" -f username="$Username"
