#!/bin/bash
cd "$(dirname "$0")"

# Auto-install dependencies on first run
if [ ! -f ".deps_installed" ]; then
    echo "Installing dependencies..."
    python3 -m pip install -r requirements.txt --quiet && touch .deps_installed
    echo "Done."
fi

echo "Starting KnowledgeTree server..."
python3 -m uvicorn knowledge-compiler.server:app --host 0.0.0.0 --port 8000 &
SERVER_PID=$!
sleep 3
echo "Opening browser: http://localhost:8000"
open "http://localhost:8000"
echo "Server is running (PID: $SERVER_PID). Press Ctrl+C to stop."
wait $SERVER_PID
