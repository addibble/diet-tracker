# Download production database from https://diettracker.kndyman.com
# Requires the APP_PASSWORD from .env

# Read .env file to get the password
$env_file = ".env"
if (-not (Test-Path $env_file)) {
    Write-Host "Error: .env file not found in current directory"
    exit 1
}

$env_content = Get-Content $env_file -Raw
$app_password = [regex]::Match($env_content, 'APP_PASSWORD=(.+)').Groups[1].Value.Trim()

if (-not $app_password) {
    Write-Host "Error: APP_PASSWORD not found in .env file"
    exit 1
}

Write-Host "Logging in to production..."
$login_response = Invoke-RestMethod `
    -Uri "https://diettracker.kndyman.com/api/auth/login" `
    -Method Post `
    -ContentType "application/json" `
    -Body (ConvertTo-Json @{ password = $app_password }) `
    -SessionVariable web_session `
    -SkipCertificateCheck

Write-Host "Downloading database..."
$timestamp = Get-Date -Format "yyyy-MM-dd-HHmmss"
$output_file = "production_db_backup_$timestamp.db"

Invoke-WebRequest `
    -Uri "https://diettracker.kndyman.com/api/database/download" `
    -Method Get `
    -OutFile $output_file `
    -WebSession $web_session `
    -SkipCertificateCheck

$file_size_mb = (Get-Item $output_file).Length / 1MB
Write-Host "✓ Database downloaded successfully: $output_file ($file_size_mb MB)"
