#!/bin/sh
set -e

cd /app

# ---------------------------------------------------------------------------
# Privileges
#
# Started as root (the image default): fix the ownership of the mounted
# volumes once, then run the API as `fim` and the frontend as `fimweb`.
# Nothing that serves requests runs as root, and the two do not share a user.
#
# Started as another user (`user:` in compose, `docker run --user`): run
# everything as that user; the volumes must already be writable by it, and
# the keys must be in the environment (no key store can be read).
# ---------------------------------------------------------------------------
mkdir -p data uploads 2>/dev/null || true

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

if [ "$(id -u)" = "0" ]; then
  APP_UID="$(id -u fim)"
  for dir in data uploads; do
    # Only when the top of the tree is not ours yet (first start after an
    # upgrade from an image that ran as root): a recursive chown of a large
    # volume on every start would be slow.
    if [ "$(stat -c %u "$dir")" != "$APP_UID" ]; then
      echo "Handing /app/$dir over to the unprivileged user fim (uid $APP_UID)..."
      chown -R fim:fim "$dir"
    fi
  done
  # PYTHONNOUSERSITE: /home/fim is the application user's (npm and uv keep
  # their caches there for stdio MCP servers), so the Python of every step
  # run as that user ignores a site-packages directory under it.
  as_app="setpriv --reuid=fim --regid=fim --init-groups --inh-caps=-all --no-new-privs env HOME=/home/fim PYTHONNOUSERSITE=1"
  as_web="setpriv --reuid=fimweb --regid=fimweb --init-groups --inh-caps=-all --no-new-privs"
  WEB_HOME=/home/fimweb

  # ---------------------------------------------------------------------
  # Keys: JWT_SECRET_KEY and CREDENTIAL_ENCRYPTION_KEY.
  #
  # From the environment, else from the key store: a directory of root's,
  # outside data/ (which belongs to the API's user), in a volume of its
  # own. Read here as root and handed to the API in its environment only.
  # Never deleted, moved or overwritten by this script. A key that is in
  # neither place is generated on a first start only; with data already in
  # the database the container stops and says what to supply.
  # See src/fim_one/core/security/container_keys.py.
  # ---------------------------------------------------------------------
  keys="$(python /app/container_keys.py load)" \
    || fail "the key store could not be read; nothing was changed. See the message above."
  eval "$keys"

  if [ -n "$FIM_MISSING_KEYS" ]; then
    db_state="$($as_app python /app/container_keys.py db-state)" \
      || fail "cannot tell whether the database already holds data, so no key is generated for: $FIM_MISSING_KEYS. Set them in the environment (.env)."
    if [ "$db_state" != "empty" ] && [ "${FIM_ALLOW_NEW_KEYS:-}" != "true" ]; then
      fail "the database already holds data, and these keys are neither in the environment nor in the key store: $FIM_MISSING_KEYS.
       Supply the keys this instance was using (JWT_SECRET_KEY, CREDENTIAL_ENCRYPTION_KEY in .env). Stored
       credentials can only be read with the CREDENTIAL_ENCRYPTION_KEY they were written with.
       To start with new keys anyway and give up what was encrypted with the old ones, set FIM_ALLOW_NEW_KEYS=true once."
    fi
    # Written and synced to disk first, handed out after.
    keys="$(python /app/container_keys.py generate)" \
      || fail "a new key could not be stored; the application is not started without a persisted key."
    eval "$keys"
  fi
  unset keys FIM_MISSING_KEYS
  [ -n "${JWT_SECRET_KEY:-}" ] && [ -n "${CREDENTIAL_ENCRYPTION_KEY:-}" ] \
    || fail "JWT_SECRET_KEY and CREDENTIAL_ENCRYPTION_KEY are not both available."

  # The sandbox proxy's token: written by the docker-socket-proxy service
  # into a volume only root can read. Without it the proxy answers 401.
  if [ "${CODE_EXEC_BACKEND:-local}" = "docker" ] && [ -z "${SANDBOX_PROXY_TOKEN:-}" ]; then
    TOKEN_FILE="${SANDBOX_PROXY_TOKEN_FILE:-/run/fim-sandbox-proxy/token}"
    waited=0
    while [ ! -s "$TOKEN_FILE" ] && [ "$waited" -lt 30 ]; do
      sleep 1
      waited=$((waited + 1))
    done
    if [ -s "$TOKEN_FILE" ]; then
      SANDBOX_PROXY_TOKEN="$(cat "$TOKEN_FILE")"
      export SANDBOX_PROXY_TOKEN
    else
      echo "WARNING: no sandbox proxy token at $TOKEN_FILE; code execution will be refused by the proxy." >&2
    fi
  fi
else
  as_app="env PYTHONNOUSERSITE=1"
  as_web=""
  WEB_HOME="${HOME:-/tmp}"
  # No key store can be read as this user, and a key the API made up for
  # itself would be gone at the next start.
  [ -n "${JWT_SECRET_KEY:-}" ] && [ -n "${CREDENTIAL_ENCRYPTION_KEY:-}" ] \
    || fail "running as uid $(id -u): set JWT_SECRET_KEY and CREDENTIAL_ENCRYPTION_KEY in the environment."
fi

# Run database migrations
echo "Running database migrations..."
$as_app alembic upgrade head

# Normalise LOG_LEVEL to lowercase for uvicorn
LOG_LEVEL=$(echo "${LOG_LEVEL:-info}" | tr '[:upper:]' '[:lower:]')

# Number of uvicorn worker processes (default: 1)
WORKERS="${WORKERS:-1}"

# Start API backend in background. Launched through Python so that the
# process is marked non-dumpable before it serves anything: its
# /proc/<pid>/environ (JWT secret, encryption key, database URL) is then
# unreadable to the commands it starts (core/security/host_access.py).
echo "Starting API backend on :8000 (workers=$WORKERS)..."
$as_app python -c "
from fim_one.core.security.host_access import harden_process
harden_process()
import uvicorn
uvicorn.main()
" fim_one.web:create_app \
  --factory \
  --host 0.0.0.0 \
  --port 8000 \
  --workers "$WORKERS" \
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

# Start Next.js frontend in foreground, with an environment of its own: only
# what a Next.js server reads. It is PID 1 and serves no API: it gets no key,
# no database URL, no token. The names passed on: NEXT_*, NODE_ENV,
# NODE_OPTIONS, PORT, HOSTNAME, KEEP_ALIVE_TIMEOUT, TZ, LANG, LC_ALL, PATH.
echo "Starting frontend on :3000..."
cd /app/frontend
set --
set -f   # a name is not a pattern
for name in $(awk 'BEGIN { for (name in ENVIRON) print name }'); do
  # Only a plain shell name reaches the eval below.
  case "$name" in
    ''|[!A-Za-z_]*|*[!A-Za-z0-9_]*) continue ;;
  esac
  case "$name" in
    NEXT_*|NODE_ENV|NODE_OPTIONS|PORT|HOSTNAME|KEEP_ALIVE_TIMEOUT|TZ|LANG|LC_ALL|PATH)
      eval "value=\${$name}"
      set -- "$@" "$name=$value"
      ;;
  esac
done
set +f
exec $as_web env -i HOME="$WEB_HOME" "$@" node server.js
