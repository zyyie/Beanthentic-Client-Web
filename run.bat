@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo ========================================
echo  Beanthentic - stable local server
echo  Open: http://127.0.0.1:5001/
echo  Refresh browser anytime — no rerun needed
echo ========================================
echo.

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
set BEANTHENTIC_AUTO_RESTART=1

python web.py
pause
endlocal
