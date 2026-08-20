@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

echo.
echo ========================================
echo  Beanthentic Client Web
echo  https://beanthentic.com/
echo ========================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-beanthentic-cloudflare.ps1"
pause
endlocal
