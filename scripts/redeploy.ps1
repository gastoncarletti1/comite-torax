param(
    [string]$Message = "Trigger deploy"
)

Write-Host ">> Triggering deploy with empty commit"
git commit --allow-empty -m $Message
git push

Write-Host ">> Done. Check GitHub Actions for deploy status."

