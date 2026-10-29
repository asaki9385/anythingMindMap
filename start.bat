@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

if not exist "%~dp0scripts\bootstrap_windows.ps1" (
    echo Missing scripts\bootstrap_windows.ps1.
    echo Extract the whole project folder and try again.
    if /I not "%NO_BROWSER%"=="1" pause
    exit /b 1
)

rem Windows PowerShell 5.1 is present on supported Windows installations.
set "PS="
if exist "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" (
    set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
)
if not defined PS (
    where powershell.exe >nul 2>&1
    if not errorlevel 1 set "PS=powershell.exe"
)
if not defined PS (
    where pwsh.exe >nul 2>&1
    if not errorlevel 1 set "PS=pwsh.exe"
)
if not defined PS (
    echo PowerShell was not found. Windows 10 or newer is required.
    if /I not "%NO_BROWSER%"=="1" pause
    exit /b 1
)

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

"%PS%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap_windows.ps1" -Root "%~dp0."
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    if /I not "%NO_BROWSER%"=="1" pause
)

exit /b %RC%
