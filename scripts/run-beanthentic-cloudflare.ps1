# Run Beanthentic Client Web on https://beanthentic.com/ via named Cloudflare Tunnel.
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$CfExe = Join-Path $PSScriptRoot "cloudflared.exe"
$ConfigPath = Join-Path $Root "cloudflare\config.yml"
$Port = if ($env:BEANTHENTIC_PORT) { $env:BEANTHENTIC_PORT } else { "5001" }
$PublicUrl = "https://beanthentic.com/"

Set-Location $Root

if (-not (Test-Path $CfExe)) {
    & (Join-Path $PSScriptRoot "install-cloudflared.bat")
    if (-not (Test-Path $CfExe)) { throw "cloudflared not installed." }
}

if (-not (Test-Path $ConfigPath)) {
    Write-Host ""
    Write-Host "Tunnel not configured yet." -ForegroundColor Yellow
    Write-Host "Run first: scripts\setup-cloudflare-beanthentic.bat"
    Write-Host ""
    exit 1
}

$configText = Get-Content $ConfigPath -Raw
if ($configText -match "REPLACE_TUNNEL_ID") {
    Write-Host ""
    Write-Host "cloudflare\config.yml is still a template." -ForegroundColor Yellow
    Write-Host "Run first: scripts\setup-cloudflare-beanthentic.bat"
    Write-Host ""
    exit 1
}

Write-Host ""
Write-Host "Installing Python packages..."
python -m pip install -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed." }

$env:BEANTHENTIC_LIVE_UPDATES = "0"
$env:BEANTHENTIC_SERVER = "waitress"
$env:BEANTHENTIC_RELOADER = "0"
$env:BEANTHENTIC_DEBUG = "0"
$env:BEANTHENTIC_HOST = "127.0.0.1"
$env:BEANTHENTIC_PUBLIC_URL = $PublicUrl

Set-Content -Path (Join-Path $Root "public-url.txt") -Value $PublicUrl -Encoding UTF8

Write-Host ""
Write-Host "Starting Beanthentic server on http://127.0.0.1:$Port ..."
$server = Start-Process -FilePath "python" -ArgumentList "web.py" -WorkingDirectory $Root -PassThru -WindowStyle Minimized

Start-Sleep -Seconds 4

Write-Host ""
Write-Host "========================================"
Write-Host " PUBLIC URL:  $PublicUrl"
Write-Host " QR download: ${PublicUrl}download/client-website-qr"
Write-Host "========================================"
Write-Host ""
Write-Host "Starting Cloudflare Tunnel for beanthentic.com ..."
Write-Host "No time limit on free tier — runs until YOU close this window."
Write-Host "Keep this window open (or install as Windows service for auto-start)."
Write-Host ""

try {
    & $CfExe tunnel --config $ConfigPath run beanthentic-client
}
finally {
    if ($server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    }
}
