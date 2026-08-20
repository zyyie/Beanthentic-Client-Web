@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set CF_EXE=%~dp0cloudflared.exe
set CF_URL=https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe

if exist "%CF_EXE%" (
  echo cloudflared already installed: %CF_EXE%
  "%CF_EXE%" --version
  exit /b 0
)

echo Downloading Cloudflare Tunnel (cloudflared)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { Invoke-WebRequest -Uri '%CF_URL%' -OutFile '%CF_EXE%' -UseBasicParsing; Write-Host 'Download complete.' } catch { Write-Host 'Download failed:' $_.Exception.Message; exit 1 }"

if not exist "%CF_EXE%" (
  echo Failed to download cloudflared.
  pause
  exit /b 1
)

echo.
"%CF_EXE%" --version
echo.
echo Installed: %CF_EXE%
endlocal
