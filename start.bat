@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem Locate a Python 3.10+ interpreter: the py launcher first, then PATH.
set "PY="
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
    where python >nul 2>&1
    if not errorlevel 1 (
        python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
        if not errorlevel 1 set "PY=python"
    )
)
if not defined PY (
    echo Python 3.10 or newer was not found.
    echo Install it from https://www.python.org/downloads/ and tick
    echo "Add python.exe to PATH" during setup, then run this file again.
    pause
    exit /b 1
)

rem Install dependencies on first run.
if not exist ".deps_installed" (
    echo Installing dependencies...
    %PY% -m pip install -r requirements.txt --quiet
    if errorlevel 1 (
        echo Failed to install dependencies. Check your Python installation.
        pause
        exit /b 1
    )
    type nul > .deps_installed
    echo Done.
)

rem requirements.txt may have changed since .deps_installed was written.
%PY% -c "import fastapi, uvicorn, fitz, httpx, requests, multipart, docx, chardet" >nul 2>&1
if errorlevel 1 (
    echo Installing missing dependencies...
    %PY% -m pip install -r requirements.txt --quiet
    if errorlevel 1 (
        echo Failed to install dependencies. Check your Python installation.
        pause
        exit /b 1
    )
)

if "%HOST%"=="" set "HOST=127.0.0.1"
if "%PORT%"=="" set "PORT=8000"
set "PYTHONUTF8=1"

echo Starting KnowledgeTree server on http://localhost:%PORT% ...
%PY% start.py
set "RC=%ERRORLEVEL%"
if "%RC%"=="1" pause
exit /b %RC%
