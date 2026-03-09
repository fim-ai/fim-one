#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

usage() {
  cat <<EOF
Usage: ./start.sh [command]

Commands:
  portal    Start the Next.js portal + API backend (default)
  api       Start only the FastAPI backend (no frontend)
  dev       Start portal in dev mode (Python hot-reload + Next.js HMR)
  help      Show this message

EOF
  exit 0
}

# Load .env if present
[ -f .env ] && set -a && source .env && set +a

# Ensure nvm-managed node/pnpm is on PATH (needed for terminals like Ghostty
# that may not source ~/.zshrc / ~/.nvm/nvm.sh automatically)
if ! command -v pnpm &>/dev/null; then
  NVM_BIN="$HOME/.nvm/versions/node/$(ls "$HOME/.nvm/versions/node/" 2>/dev/null | sort --version-sort | tail -1)/bin"
  [ -d "$NVM_BIN" ] && export PATH="$NVM_BIN:$PATH"
fi

# Kill any leftover processes on our ports
free_port() {
  local port=$1
  local pid
  pid=$(lsof -ti :"$port" 2>/dev/null || true)
  if [ -n "$pid" ]; then
    echo "  Killing old process on port $port (pid $pid)"
    kill $pid 2>/dev/null || true
    sleep 0.5
  fi
}

# Normalise LOG_LEVEL to lowercase for uvicorn --log-level
UVICORN_LOG_LEVEL="${LOG_LEVEL:-info}"
UVICORN_LOG_LEVEL="$(echo "$UVICORN_LOG_LEVEL" | tr '[:upper:]' '[:lower:]')"

CMD="${1:-portal}"
# Worker count for production modes (portal / api); dev always uses 1 worker.
# Set WORKERS=4 (or higher) when Redis is configured for cross-worker state.
WORKERS="${WORKERS:-1}"

case "$CMD" in
  portal)
    free_port 8000
    free_port 3000
    echo "Starting FIM Agent Portal..."
    WORKER_INFO=""; [ "$WORKERS" -gt 1 ] 2>/dev/null && WORKER_INFO=" (${WORKERS} workers)"
    echo "  API backend  → http://localhost:8000${WORKER_INFO}"
    echo "  Next.js app  → http://localhost:3000"
    # Start API in background, Next.js in foreground
    uv run uvicorn fim_agent.web:create_app --factory --host 0.0.0.0 --port 8000 --workers "$WORKERS" --log-level "$UVICORN_LOG_LEVEL" &
    API_PID=$!
    trap "kill $API_PID 2>/dev/null" EXIT
    cd frontend && pnpm dev
    ;;
  dev)
    free_port 8000
    free_port 3000
    echo "Starting FIM Agent Portal (dev mode — hot reload)..."
    echo "  API backend  → http://localhost:8000 (--reload)"
    echo "  Next.js app  → http://localhost:3000 (HMR)"
    rm -rf frontend/.next
    mkdir -p frontend/.next/static/development
    # Prevent macOS Spotlight from indexing .next
    touch frontend/.next/.metadata_never_index
    touch frontend/.next/static/.metadata_never_index
    touch frontend/.next/static/development/.metadata_never_index
    mdutil -i off frontend/.next &>/dev/null || true
    uv run uvicorn fim_agent.web:create_app --factory --host 0.0.0.0 --port 8000 --reload --reload-dir src --log-level "$UVICORN_LOG_LEVEL" &
    API_PID=$!
    trap "kill $API_PID 2>/dev/null" EXIT
    cd frontend
    # Auto-restart on crash (e.g. Turbopack tmp file race condition)
    while true; do
      find .next -name '*.tmp.*' -delete 2>/dev/null || true
      pnpm dev
      EXIT_CODE=$?
      # Ctrl+C (SIGINT=130) → stop for real
      [ $EXIT_CODE -eq 0 ] || [ $EXIT_CODE -eq 130 ] && break
      echo ""
      echo "  Next.js dev server crashed (exit $EXIT_CODE), restarting in 2s..."
      sleep 2
    done
    ;;
  api)
    free_port 8000
    WORKER_INFO=""; [ "$WORKERS" -gt 1 ] 2>/dev/null && WORKER_INFO=" (${WORKERS} workers)"
    echo "Starting FIM Agent API at http://localhost:8000${WORKER_INFO}"
    uv run uvicorn fim_agent.web:create_app --factory --host 0.0.0.0 --port 8000 --workers "$WORKERS" --log-level "$UVICORN_LOG_LEVEL"
    ;;
  help|--help|-h)
    usage
    ;;
  *)
    echo "Unknown command: $CMD"
    usage
    ;;
esac
