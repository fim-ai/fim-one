#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

usage() {
  cat <<EOF
Usage: ./start.sh [command]

Commands:
  portal    Start the Next.js portal + API backend (default)
  api       Start only the FastAPI backend (no frontend)
  help      Show this message

EOF
  exit 0
}

# Load .env if present
[ -f .env ] && set -a && source .env && set +a

CMD="${1:-portal}"

case "$CMD" in
  portal)
    echo "Starting FIM Agent Portal..."
    echo "  API backend  → http://localhost:8000"
    echo "  Next.js app  → http://localhost:3000"
    # Start API in background, Next.js in foreground
    uv run uvicorn fim_agent.web:create_app --factory --host 0.0.0.0 --port 8000 &
    API_PID=$!
    trap "kill $API_PID 2>/dev/null" EXIT
    cd frontend && pnpm dev
    ;;
  api)
    echo "Starting FIM Agent API at http://localhost:8000"
    uv run uvicorn fim_agent.web:create_app --factory --host 0.0.0.0 --port 8000
    ;;
  help|--help|-h)
    usage
    ;;
  *)
    echo "Unknown command: $CMD"
    usage
    ;;
esac
