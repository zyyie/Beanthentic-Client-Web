@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

echo.
echo ========================================
echo  Beanthentic + Cloudflare QUICK Tunnel
echo  (trycloudflare.com — FREE)
echo ========================================
echo.
echo IMPORTANT:
echo   - URL works ONLY while this window is OPEN
echo   - "Page cannot be reached" = you used an OLD dead link
echo   - Old links DIE when you close/restart this window
echo   - Wait for the GREEN "LIVE TUNNEL URL" box, then use THAT link
echo   - tunnel-url.txt is updated only while this script is running
echo   - Tunnel auto-restarts if connection drops
echo.
echo Tip: disable laptop Sleep while demo/testing.
echo.
echo If server fails to start, run once:
echo   py -3.12 -m pip install -r requirements.txt
echo.
echo Wait for green "Status: ONLINE" before using the link.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-cloudflare-tunnel.ps1"
pause
endlocal
