param(
    [string]$CloudSqlConnectionName,
    [string]$DbUser,
    [string]$DbPass,
    [string]$DbName
)

if (-Not $CloudSqlConnectionName -or -Not $DbUser -or -Not $DbPass -or -Not $DbName) {
    Write-Host "Usage: .\scripts\reset_admin.ps1 -CloudSqlConnectionName PROJECT:REGION:INSTANCE -DbUser USER -DbPass PASS -DbName DB"
    exit 1
}

$env:CLOUD_SQL_CONNECTION_NAME = $CloudSqlConnectionName
$env:DB_USER = $DbUser
$env:DB_PASS = $DbPass
$env:DB_NAME = $DbName

Write-Host ">> Running reset_admin.py using Cloud SQL Connector env vars"
python reset_admin.py

