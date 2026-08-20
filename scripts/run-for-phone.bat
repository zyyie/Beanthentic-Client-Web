@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

echo.
echo ========================================
echo  Beanthentic - same HOME Wi-Fi only
echo  (NOT phone hotspot, NOT laptop hotspot)
echo ========================================
echo.

REM Try to set Wi-Fi to Private (helps Windows allow LAN)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p = Get-NetConnectionProfile | Where-Object { $_.InterfaceAlias -match 'Wi-Fi|WLAN' -and $_.IPv4Connectivity -ne 'NoTraffic' } | Select-Object -First 1; if ($p -and $p.NetworkCategory -eq 'Public') { try { Set-NetConnectionProfile -InterfaceIndex $p.InterfaceIndex -NetworkCategory Private; Write-Host ('Wi-Fi set to Private: ' + $p.Name) } catch { Write-Host 'Could not set Private - run allow-lan-access.bat as Admin' } } elseif ($p) { Write-Host ('Wi-Fi: ' + $p.Name + ' (' + $p.NetworkCategory + ')') }"

echo.
echo Installing/updating packages...
python -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo pip install failed.
    pause
    exit /b 1
)

set BEANTHENTIC_LIVE_UPDATES=1
set BEANTHENTIC_SERVER=waitress
set BEANTHENTIC_RELOADER=0
set BEANTHENTIC_DEBUG=0

echo.
echo Starting server (stops any old copy on port 5001)...
echo After editing files: close this window and run again, OR refresh browser for HTML/CSS.
echo Use run-dev.bat on laptop for auto-reload while coding.
echo.
python web.py
pause
endlocal
