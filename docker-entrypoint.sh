#!/bin/sh
set -e

cd /app

# Ensure data directories exist
mkdir -p data uploads

# Run database migrations
echo "Running database migrations..."
alembic upgrade head

# Normalise LOG_LEVEL to lowercase for uvicorn
LOG_LEVEL=$(echo "${LOG_LEVEL:-info}" | tr '[:upper:]' '[:lower:]')

# Start API backend in background
echo "Starting API backend on :8000..."
uvicorn fim_agent.web:create_app \
  --factory \
  --host 0.0.0.0 \
  --port 8000 \
  --log-level "$LOG_LEVEL" &
API_PID=$!

# Wait for API to be ready before starting frontend (prevents SSR race condition)
echo "Waiting for API to be ready..."
python -c "
import socket, time
while True:
    try:
        socket.create_connection(('127.0.0.1', 8000), timeout=1)
        break
    except OSError:
        time.sleep(0.2)
"
echo "API ready."

# Start Next.js frontend in foreground
echo "Starting frontend on :3000..."
cd /app/frontend
exec node server.js
