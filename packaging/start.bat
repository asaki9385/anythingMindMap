@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0runtime\python.exe" (
    echo Missing runtime\python.exe. Please extract the whole ZIP before starting.
    if /I not "%NO_BROWSER%"=="1" pause
    exit /b 1
)

if "%HOST%"=="" set "HOST=127.0.0.1"
if "%PORT%"=="" set "PORT=8000"
set "NO_BROWSER_ARG="
if /I "%NO_BROWSER%"=="1" set "NO_BROWSER_ARG=--no-browser"

"%~dp0runtime\python.exe" "%~dp0app\launcher.py" --host "%HOST%" --port "%PORT%" %NO_BROWSER_ARG%
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo AnythingMindmap failed to start. Exit code: %RC%
    if /I not "%NO_BROWSER%"=="1" pause
)

exit /b %RC%
