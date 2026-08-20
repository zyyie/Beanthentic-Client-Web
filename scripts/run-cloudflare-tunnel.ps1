# Beanthentic + Cloudflare QUICK tunnel (trycloudflare.com)
# Auto-restarts tunnel if it drops. URL is valid ONLY while this window is open.
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$CfExe = Join-Path $PSScriptRoot "cloudflared.exe"
$Port = if ($env:BEANTHENTIC_PORT) { $env:BEANTHENTIC_PORT } else { "5001" }
$PublicUrlFile = Join-Path $Root "public-url.txt"
$TunnelUrlFile = Join-Path $Root "tunnel-url.txt"
$CfLogFile = Join-Path $Root "cloudflared-live.log"
$LocalUrl = "http://127.0.0.1:$Port/"

Set-Location $Root

function Write-Banner([string]$Url, [switch]$Changed, [switch]$Verified) {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    if ($Changed) {
        Write-Host " NEW TUNNEL URL (old link is dead - update QR/bookmark)" -ForegroundColor Yellow
    } else {
        Write-Host " LIVE TUNNEL URL" -ForegroundColor Green
    }
    if ($Verified) {
        Write-Host " Status: ONLINE (tested OK)" -ForegroundColor Green
    } else {
        Write-Host " Status: NOT REACHABLE YET - do not share this link" -ForegroundColor Red
    }
    Write-Host " $Url" -ForegroundColor White
    Write-Host "========================================" -ForegroundColor Green
    Write-Host " QR download: ${Url}download/client-website-qr"
    Write-Host " Saved to:    $TunnelUrlFile"
    Write-Host ""
    Write-Host " Rules:" -ForegroundColor Cyan
    Write-Host "  - Keep THIS window open or the link dies (DNS error)"
    Write-Host "  - Laptop sleep / close window = page cannot be reached"
    Write-Host "  - If tunnel restarts, URL may change - read this window"
    Write-Host "  - Do NOT use an old link from chat/history/QR"
    Write-Host ""
    try {
        Set-Clipboard -Value $Url
        Write-Host " Copied to clipboard." -ForegroundColor Green
    } catch {
        Write-Host " Copy the URL above manually (clipboard unavailable)." -ForegroundColor DarkYellow
    }
    if ($Verified) {
        try {
            Start-Process $Url | Out-Null
            Write-Host " Opened in your default browser." -ForegroundColor Green
        } catch {
            Write-Host " Open the URL above in your browser." -ForegroundColor DarkYellow
        }
    } else {
        Write-Host " Browser NOT opened - wait until Status is ONLINE." -ForegroundColor Yellow
        Write-Host " Check cloudflared-live.log if this keeps failing." -ForegroundColor Yellow
    }
    Write-Host ""
}

function Save-TunnelUrl([string]$Url) {
    Set-Content -Path $PublicUrlFile -Value $Url -Encoding UTF8
    Set-Content -Path $TunnelUrlFile -Value $Url -Encoding UTF8
    $env:BEANTHENTIC_PUBLIC_URL = $Url
}

function Test-PublicTunnelUrl([string]$Url, [int]$Retries = 20) {
    if (-not $Url) { return $false }
    for ($i = 1; $i -le $Retries; $i++) {
        try {
            $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 25
            if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) {
                return $true
            }
        } catch {
            Write-Host "  Waiting for tunnel DNS/route... ($i/$Retries)" -ForegroundColor DarkYellow
            Start-Sleep -Seconds 3
        }
    }
    return $false
}

function Show-StaleUrlWarning {
    if (-not (Test-Path $TunnelUrlFile)) { return }
    $old = (Get-Content -Path $TunnelUrlFile -Raw -ErrorAction SilentlyContinue).Trim()
    if (-not $old) { return }
    Write-Host ""
    Write-Host "WARNING: tunnel-url.txt has an OLD link (dead if this window was closed):" -ForegroundColor Yellow
    Write-Host "  $old" -ForegroundColor DarkYellow
    Write-Host "  Ignore it until a NEW green URL appears below." -ForegroundColor Yellow
    Write-Host ""
}

function Test-LocalServer {
    try {
        $r = Invoke-WebRequest -Uri $LocalUrl -UseBasicParsing -TimeoutSec 5
        return $r.StatusCode -ge 200 -and $r.StatusCode -lt 500
    } catch {
        return $false
    }
}

function Resolve-PythonExe {
    if ($env:BEANTHENTIC_PYTHON -and (Test-Path $env:BEANTHENTIC_PYTHON)) {
        return $env:BEANTHENTIC_PYTHON
    }

    $candidates = @()
    foreach ($ver in @("3.12", "3.13", "3.11", "3")) {
        try {
            $exe = & py "-$ver" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) {
                $candidates += $exe.Trim()
            }
        } catch { }
    }
    try {
        $exe = & python -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe) {
            $candidates += $exe.Trim()
        }
    } catch { }

    $seen = @{}
    foreach ($exe in $candidates) {
        if ($seen.ContainsKey($exe)) { continue }
        $seen[$exe] = $true
        Write-Host "Trying Python: $exe"
        & $exe -m pip install -q -r (Join-Path $Root "requirements.txt")
        if ($LASTEXITCODE -ne 0) { continue }
        & $exe -c "import flask, qrcode, waitress"
        if ($LASTEXITCODE -eq 0) {
            return $exe
        }
    }

    throw @"
Could not find Python with required packages (Flask, qrcode, waitress).
Run manually:
  py -3.12 -m pip install -r requirements.txt
Then set BEANTHENTIC_PYTHON to your python.exe path and run this script again.
"@
}

function Stop-StaleCloudflared {
    $procs = Get-Process cloudflared -ErrorAction SilentlyContinue
    if (-not $procs) { return }
    Write-Host "Stopping $($procs.Count) old cloudflared process(es)..."
    $procs | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

function Read-TunnelUrlFromLog([string]$LogPath, [System.Diagnostics.Process]$Proc, [int]$TimeoutSec = 180) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $lastSize = 0
    while ((Get-Date) -lt $deadline -and -not $Proc.HasExited) {
        if (Test-Path $LogPath) {
            $size = (Get-Item $LogPath).Length
            if ($size -gt $lastSize) {
                $newLines = Get-Content -Path $LogPath -ErrorAction SilentlyContinue | Select-Object -Skip ([Math]::Max(0, (Get-Content $LogPath).Count - 8))
                foreach ($line in $newLines) {
                    if ($line) { Write-Host $line }
                }
                $lastSize = $size
            }
            $all = Get-Content -Path $LogPath -Raw -ErrorAction SilentlyContinue
            if ($all -match "https://[a-z0-9-]+\.trycloudflare\.com") {
                return ($Matches[0].TrimEnd("/") + "/")
            }
        }
        Start-Sleep -Milliseconds 400
    }
    return ""
}

function Start-QuickTunnelProcess {
    if (Test-Path $CfLogFile) {
        Remove-Item $CfLogFile -Force -ErrorAction SilentlyContinue
    }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $CfExe
    $psi.Arguments = "tunnel --url http://127.0.0.1:$Port --no-autoupdate --edge-ip-version 4 --protocol http2 --logfile `"$CfLogFile`""
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    $null = $proc.Start()
    return $proc
}

if (-not (Test-Path $CfExe)) {
    & (Join-Path $PSScriptRoot "install-cloudflared.bat")
    if (-not (Test-Path $CfExe)) { throw "cloudflared install failed." }
}

Stop-StaleCloudflared

Write-Host ""
Write-Host "Resolving Python and installing packages..."
$PythonExe = Resolve-PythonExe
Write-Host "Using Python: $PythonExe"

$env:BEANTHENTIC_LIVE_UPDATES = "0"
$env:BEANTHENTIC_SERVER = "waitress"
$env:BEANTHENTIC_RELOADER = "0"
$env:BEANTHENTIC_DEBUG = "0"
$env:BEANTHENTIC_HOST = "127.0.0.1"

$server = $null
$startedServer = $false
if (Test-LocalServer) {
    Write-Host ""
    Write-Host "Beanthentic already running on $LocalUrl (reusing existing server)."
} else {
    Write-Host ""
    Write-Host "Starting Beanthentic server on $LocalUrl ..."
    $server = Start-Process -FilePath $PythonExe -ArgumentList "web.py" -WorkingDirectory $Root -PassThru -WindowStyle Minimized
    $startedServer = $true
    Start-Sleep -Seconds 5
    if (-not (Test-LocalServer)) {
        Write-Host "Waiting for local server..."
        $ready = $false
        foreach ($i in 1..25) {
            Start-Sleep -Seconds 1
            if (Test-LocalServer) { $ready = $true; break }
        }
        if (-not $ready) {
            throw "Local server did not start on port $Port. Check minimized python window for errors (often missing pip packages)."
        }
    }
}

Show-StaleUrlWarning

Write-Host ""
Write-Host "Cloudflare QUICK tunnel - auto-restart if connection drops"
Write-Host "Do NOT use old trycloudflare links - always use URL below"
Write-Host ""

$lastUrl = ""
$attempt = 0

try {
    while ($true) {
        $attempt++
        if ($attempt -gt 1) {
            Write-Host ""
            Write-Host "Tunnel disconnected. Restarting in 5 seconds... (attempt $attempt)" -ForegroundColor Yellow
            Start-Sleep -Seconds 5
            if (-not (Test-LocalServer)) {
                Write-Host "Local server down - waiting..."
                Start-Sleep -Seconds 3
            }
        }

        Write-Host "Starting cloudflared (log: cloudflared-live.log)..."
        $proc = Start-QuickTunnelProcess
        $publicUrl = Read-TunnelUrlFromLog -LogPath $CfLogFile -Proc $proc -TimeoutSec 180

        if ($publicUrl) {
            $changed = ($lastUrl -and $lastUrl -ne $publicUrl)
            Write-Host "Verifying public URL is reachable..."
            $verified = Test-PublicTunnelUrl -Url $publicUrl
            if ($verified) {
                Save-TunnelUrl $publicUrl
            }
            if (-not $verified) {
                Write-Host ""
                Write-Host "Tunnel URL was created but is NOT reachable from this PC." -ForegroundColor Red
                Write-Host "Keep this window open and wait - the script will keep retrying." -ForegroundColor Yellow
                Write-Host "Tips: disable VPN, use DNS 1.1.1.1, check firewall allows cloudflared." -ForegroundColor Yellow
                Write-Host ""
            }
            Write-Banner -Url $publicUrl -Changed:$changed -Verified:$verified
            if ($verified) {
                $lastUrl = $publicUrl
            }
        } else {
            Write-Host "Could not read tunnel URL from cloudflared logs." -ForegroundColor Yellow
            if (Test-Path $CfLogFile) {
                Write-Host "--- cloudflared-live.log (tail) ---"
                Get-Content $CfLogFile -Tail 20 | ForEach-Object { Write-Host $_ }
            }
        }

        $proc.WaitForExit()
        if ($proc.ExitCode -ne 0) {
            Write-Host "cloudflared exited with code $($proc.ExitCode)" -ForegroundColor Yellow
        }
    }
}
finally {
    if ($startedServer -and $server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    }
}
