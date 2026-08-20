# One-time setup: Cloudflare named tunnel for https://beanthentic.com/
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$CfExe = Join-Path $PSScriptRoot "cloudflared.exe"
$TunnelName = "beanthentic-client"
$Hostname = "beanthentic.com"
$WwwHost = "www.beanthentic.com"
$PublicUrl = "https://beanthentic.com/"
$ConfigPath = Join-Path $Root "cloudflare\config.yml"
$CloudflaredDir = Join-Path $env:USERPROFILE ".cloudflared"

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

if (-not (Test-Path $CfExe)) {
    Write-Step "Installing cloudflared..."
    & (Join-Path $PSScriptRoot "install-cloudflared.bat")
    if (-not (Test-Path $CfExe)) { throw "cloudflared install failed." }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " Beanthentic.com — Cloudflare Tunnel Setup"
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Before continuing, make sure:"
Write-Host "  1) beanthentic.com is added to your Cloudflare account"
Write-Host "  2) Cloudflare nameservers are active on the domain"
Write-Host ""

$continue = Read-Host "Continue setup? (y/n)"
if ($continue -notmatch "^[Yy]") { exit 0 }

Write-Step "Login to Cloudflare (browser will open)..."
& $CfExe tunnel login
if ($LASTEXITCODE -ne 0) { throw "cloudflared login failed." }

Write-Step "Checking tunnel '$TunnelName'..."
$listJson = & $CfExe tunnel list --output json 2>$null | Out-String
$tunnelId = ""
if ($listJson) {
    try {
        $tunnels = $listJson | ConvertFrom-Json
        foreach ($t in $tunnels) {
            if ($t.name -eq $TunnelName) {
                $tunnelId = [string]$t.id
                break
            }
        }
    } catch { }
}

if (-not $tunnelId) {
    Write-Step "Creating tunnel '$TunnelName'..."
    $createOut = & $CfExe tunnel create $TunnelName 2>&1 | Out-String
    Write-Host $createOut
    if ($createOut -match "([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})") {
        $tunnelId = $Matches[1]
    }
    if (-not $tunnelId) {
        $listJson = & $CfExe tunnel list --output json 2>$null | Out-String
        $tunnels = $listJson | ConvertFrom-Json
        foreach ($t in $tunnels) {
            if ($t.name -eq $TunnelName) {
                $tunnelId = [string]$t.id
                break
            }
        }
    }
}

if (-not $tunnelId) { throw "Could not find or create tunnel ID." }
Write-Host "Tunnel ID: $tunnelId"

$credFile = Join-Path $CloudflaredDir "$tunnelId.json"
if (-not (Test-Path $credFile)) {
    throw "Credentials file not found: $credFile (re-run: cloudflared tunnel create $TunnelName)"
}

Write-Step "Routing DNS: $Hostname"
& $CfExe tunnel route dns $TunnelName $Hostname
if ($LASTEXITCODE -ne 0) {
    Write-Host "DNS route may already exist — continuing." -ForegroundColor Yellow
}

Write-Step "Routing DNS: $WwwHost"
& $CfExe tunnel route dns $TunnelName $WwwHost
if ($LASTEXITCODE -ne 0) {
    Write-Host "www DNS route may already exist — continuing." -ForegroundColor Yellow
}

Write-Step "Writing cloudflare\config.yml..."
$credEscaped = $credFile -replace "\\", "/"
$config = @"
# Beanthentic Client Web — permanent Cloudflare Tunnel
# Domain: https://beanthentic.com/

tunnel: $tunnelId
credentials-file: $credFile

ingress:
  - hostname: beanthentic.com
    service: http://127.0.0.1:5001
  - hostname: www.beanthentic.com
    service: http://127.0.0.1:5001
  - service: http_status:404
"@
Set-Content -Path $ConfigPath -Value $config -Encoding UTF8

$publicUrlFile = Join-Path $Root "public-url.txt"
Set-Content -Path $publicUrlFile -Value $PublicUrl -Encoding UTF8

Write-Step "Updating .env with BEANTHENTIC_PUBLIC_URL..."
$envFile = Join-Path $Root ".env"
$envLines = @()
if (Test-Path $envFile) {
    $envLines = Get-Content $envFile -Encoding UTF8
    $envLines = $envLines | Where-Object {
        $_ -notmatch "^\s*BEANTHENTIC_PUBLIC_URL\s*=" -and
        $_ -notmatch "^\s*BEANTHENTIC_CLOUDFLARE_HOSTNAME\s*="
    }
}
$envLines += "BEANTHENTIC_PUBLIC_URL=$PublicUrl"
$envLines += "BEANTHENTIC_CLOUDFLARE_HOSTNAME=$Hostname"
Set-Content -Path $envFile -Value $envLines -Encoding UTF8

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " SETUP COMPLETE"
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host " Permanent URL:  $PublicUrl"
Write-Host " Config file:    $ConfigPath"
Write-Host ""
Write-Host " Next: double-click scripts\run-beanthentic-cloudflare.bat"
Write-Host " Then open:      $PublicUrl"
Write-Host " QR download:    ${PublicUrl}download/client-website-qr"
Write-Host ""
