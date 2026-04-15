param(
  [Parameter(Mandatory = $true)]
  [string]$Message
)

$ErrorActionPreference = "Stop"

Write-Host "== Git status =="
git status --short

Write-Host "`n== Staging changes =="
git add .

Write-Host "`n== Committing =="
git commit -m $Message

Write-Host "`n== Pushing =="
git push

Write-Host "`nDone."
