@echo off
setlocal EnableExtensions
echo.
echo ===== Same Wi-Fi checklist (NO hotspot) =====
echo.

echo [Laptop Wi-Fi]
for /f "tokens=*" %%A in ('powershell -NoProfile -Command "(Get-NetConnectionProfile | Where-Object { $_.InterfaceAlias -match 'Wi-Fi|WLAN' -and $_.IPv4Connectivity -ne 'NoTraffic' } | Select-Object -First 1).Name"') do set WIFINAME=%%A
echo   Network name: %WIFINAME%
echo.

ipconfig | findstr /i /c:"Wireless LAN adapter Wi-Fi" /c:"IPv4" /c:"Subnet"
echo.

echo [Phone - you must check manually]
echo   1. Settings - Wi-Fi - connect to: %WIFINAME%
echo   2. Tap the (i) icon next to that Wi-Fi
echo   3. IP Address should start with same numbers as laptop above
echo      Example: laptop 192.168.0.105 -^> phone should be 192.168.0.???
echo   4. Turn OFF mobile data
echo   5. Do NOT use phone Personal Hotspot
echo   6. Do NOT use laptop Mobile Hotspot
echo.

echo [Test URL on phone after run-for-phone.bat]
for /f "delims=" %%I in ('python -c "import web; ips=web._get_wifi_ipv4_addresses(); print(ips[0] if ips else '')" 2^>nul') do set IP=%%I
if not "%IP%"=="" (
    echo   http://%IP%:5001/phone-test
    echo   http://%IP%:5001/
) else (
    echo   Run ipconfig and use Wi-Fi IPv4 with :5001
)
echo.
pause
endlocal
