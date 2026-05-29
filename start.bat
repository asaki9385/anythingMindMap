@echo off
cd /d "%~dp0"

:: Auto-install dependencies on first run
if not exist ".deps_installed" (
    echo Installing dependencies...
    python -m pip install -r requirements.txt --quiet
    if %errorlevel% equ 0 (
        type nul > .deps_installed
        echo Done.
    ) else (
        echo Failed to install dependencies. Check your Python installation.
        pause
        exit /b 1
    )
)

echo Starting KnowledgeTree server...
start /B python -m uvicorn knowledge-compiler.server:app --host 0.0.0.0 --port 8000
timeout /t 3 /nobreak >nul
echo Opening browser: http://localhost:8000
start "" http://localhost:8000/ui/homepage.html
echo Server is running. Press Ctrl+C to stop.
pause
