#!/bin/bash
cd "$(dirname "$0")"

# Auto-install dependencies on first run
if [ ! -f ".deps_installed" ]; then
    echo "Installing dependencies..."
    python3 -m pip install -r requirements.txt --quiet && touch .deps_installed
    if [ $? -ne 0 ]; then
        echo "Failed to install dependencies. Check your Python installation."
        exit 1
    fi
    echo "Done."
fi

# requirements 变更后 .deps_installed 仍可能存在；每次启动做一次轻量导入检查。
if ! python3 -c "import fastapi, uvicorn, pymupdf, httpx, requests, multipart, docx, chardet" 2>/dev/null; then
    echo "Installing missing dependencies..."
    python3 -m pip install -r requirements.txt --quiet
fi

echo "Starting KnowledgeTree server..."

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
export HOST PORT

exec python3 start.py
