param(
    [string]$Message = "Deploy"
)

Write-Host ">> Git status"
git status

Write-Host ">> Committing and pushing to trigger GitHub Actions deploy"
git add .
git commit -m $Message
git push

Write-Host ">> Done. Check GitHub Actions for deploy status."

