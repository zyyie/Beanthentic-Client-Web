@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

echo.
echo ========================================
echo  Setup beanthentic.com on Cloudflare
echo  (one-time — replaces random trycloudflare URL)
echo ========================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-cloudflare-beanthentic.ps1"
pause
endlocal
