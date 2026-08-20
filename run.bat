@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo ========================================
echo  Beanthentic - local development
echo  Open: http://127.0.0.1:5001/
echo ========================================
echo.

python -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo pip install failed.
    pause
    exit /b 1
)

set BEANTHENTIC_LIVE_UPDATES=1
set BEANTHENTIC_SERVER=flask
set BEANTHENTIC_RELOADER=1
set BEANTHENTIC_DEBUG=1

python web.py
pause
endlocal
