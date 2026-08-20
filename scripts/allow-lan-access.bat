@echo off
setlocal EnableExtensions EnableDelayedExpansion

set PORT=5001
if not "%BEANTHENTIC_PORT%"=="" set PORT=%BEANTHENTIC_PORT%
set RULE=BeanthenticClientWeb_%PORT%

net session >nul 2>&1
if not "%errorlevel%"=="0" (
    echo.
    echo Administrator permission required. Click YES on the prompt.
    echo.
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs -Wait"
    if errorlevel 1 (
        echo Cancelled. Run again and click Yes.
        pause
    )
    exit /b
)

echo ========================================
echo  Beanthentic - phone access setup
echo  Administrator: OK
echo ========================================
echo.

REM 1) Wi-Fi: Public blocks some LAN access on Windows
echo [1/3] Setting Wi-Fi to Private network...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p = Get-NetConnectionProfile | Where-Object { $_.InterfaceAlias -match 'Wi-Fi|WLAN' -and $_.IPv4Connectivity -ne 'NoTraffic' } | Select-Object -First 1; if ($p) { Set-NetConnectionProfile -InterfaceIndex $p.InterfaceIndex -NetworkCategory Private; Write-Host ('  Wi-Fi set to Private: ' + $p.Name) } else { Write-Host '  No active Wi-Fi profile found (skip)' }"
echo.

REM 2) TCP port
echo [2/3] Firewall: allow TCP port %PORT% ...
netsh advfirewall firewall show rule name="%RULE%" >nul 2>&1
if "%errorlevel%"=="0" (
    echo   Rule exists: %RULE%
) else (
    netsh advfirewall firewall add rule name="%RULE%" dir=in action=allow protocol=TCP localport=%PORT% profile=any
    if not "%errorlevel%"=="0" (
        echo   FAILED port rule.
        pause
        exit /b 1
    )
    echo   Created: %RULE%
)
echo.

REM 3) Python.exe (Windows often blocks app even when port is open)
echo [3/3] Firewall: allow Python for inbound LAN...
set PY_RULE=BeanthenticPython_LAN
set PY_ADDED=0
for /f "delims=" %%P in ('where python 2^>nul') do (
    if !PY_ADDED! equ 0 (
        echo %%P | findstr /i "WindowsApps" >nul
        if errorlevel 1 (
            netsh advfirewall firewall add rule name="%PY_RULE%" dir=in action=allow program="%%P" enable=yes profile=any >nul 2>&1
            if not errorlevel 1 echo   Allowed: %%P
            set PY_ADDED=1
        )
    )
)
if "%PY_ADDED%"=="0" echo   Could not find python.exe - port rule above should still work.
echo.

echo ========================================
echo  DONE
echo ========================================
echo.
echo On laptop run:  scripts\run-for-phone.bat
echo.
echo On phone (same Wi-Fi, mobile data OFF):
echo   Use the http://192.168.x.x:%PORT%/ URL from the terminal
echo   Test first:     http://192.168.x.x:%PORT%/api/lan-ping
echo.
echo If still fails on TP-LINK router:
echo   Disable "AP Isolation" / "Client Isolation" in router settings
echo.
pause
endlocal
