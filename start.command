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

echo "Starting KnowledgeTree server..."

cleanup() {
    echo -e "\nShutting down server..."
    kill $SERVER_PID 2>/dev/null
    wait $SERVER_PID 2>/dev/null
    echo "Stopped."
}
trap cleanup INT TERM

python3 -m uvicorn knowledge-compiler.server:app --host 0.0.0.0 --port 8000 &
SERVER_PID=$!

# Wait for server to be ready
for i in $(seq 1 15); do
    if curl -s -o /dev/null http://localhost:8000 2>/dev/null; then
        break
    fi
    sleep 1
done

if ! kill -0 $SERVER_PID 2>/dev/null; then
    echo "Server failed to start. Check the output above."
    exit 1
fi

echo "Opening browser: http://localhost:8000/ui/homepage.html"
open "http://localhost:8000/ui/homepage.html"
echo "Server is running (PID: $SERVER_PID). Press Ctrl+C to stop."
wait $SERVER_PID
