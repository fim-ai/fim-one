#!/usr/bin/env bash
# Idempotent deploy wrapper for fim-one.
#
# Fixes the recurring "Conflict. The container name ... is already in use"
# error on `docker compose up -d`.
#
# Root cause: the `fim-one` service runs in Docker-outside-of-Docker (DooD)
# mode — it bind-mounts /var/run/docker.sock and spawns `fim-sandbox:python`
# child containers to run user code. When compose recreates `fim-one` while
# it is live, it does a "rename old -> create new -> remove old" dance; if
# removal of the old container stalls (children attached / daemon busy), the
# old container is left renamed to `<12-hex>_fim-one-fim-one-1` and the NEXT
# recreate (or an internal retry) collides on that name.
#
# Fix strategy — make recreation deterministic instead of relying on the
# implicit recreate:
#   1. Kill the DooD child containers so nothing references fim-one.
#   2. Remove any leftover hash-prefixed zombie containers.
#   3. EXPLICITLY remove the old fim-one (and sandbox) containers BEFORE up,
#      so `up` only ever does a clean create — never the rename dance.
#   4. `up`; if it still trips the zombie conflict, re-sweep and retry once.
#
# Safe to run repeatedly. Run as: sudo ./deploy.sh [service ...]

set -euo pipefail

cd "$(dirname "$0")"

COMPOSE="docker compose"

log() { printf '\033[1;34m[deploy]\033[0m %s\n' "$*"; }

# --- DooD child + zombie sweep -------------------------------------------
# Factored out so we can run it both before `up` and again on a retry.
sweep() {
  # Kill DooD child containers spawned by the sandbox. They mount the host
  # ./data dir and hold the docker socket; clearing them lets the old
  # fim-one container be removed cleanly.
  docker ps -aq --filter "ancestor=fim-sandbox:python" 2>/dev/null \
    | xargs -r docker rm -f >/dev/null 2>&1 || true

  # Remove hash-prefixed zombies. Docker renames containers it cannot delete
  # to `<12-hex>_<original-name>`; match only our project's services so we
  # never touch unrelated containers.
  local zombies
  zombies=$(docker ps -a --format '{{.Names}}' \
    | grep -E '^[0-9a-f]{12}_fim-one-(sandbox|fim-one|postgres|redis)-[0-9]+$' \
    || true)
  if [[ -n "${zombies}" ]]; then
    echo "${zombies}" | xargs -r docker rm -f >/dev/null 2>&1 || true
    echo "${zombies}" | sed 's/^/  removed zombie: /'
  fi
}

# --- 1+2. Pre-up sweep ----------------------------------------------------
log "Sweeping DooD children + hash-prefixed zombies..."
sweep

# --- 3. Explicitly remove the app + sandbox containers before up ----------
# This is the key fix: deleting the old fim-one container up front means
# `up` performs a fresh create instead of the rename-and-recreate dance
# that produces zombies. postgres/redis are left running (data services).
log "Removing old fim-one + sandbox containers before recreate..."
$COMPOSE rm -f -s -v fim-one sandbox >/dev/null 2>&1 || true
# Catch the old container even if compose can't (e.g. already orphaned).
docker rm -f fim-one-fim-one-1 >/dev/null 2>&1 || true

# --- 4. Bring the stack up (build), with one self-healing retry -----------
log "Building and starting services..."
if ! $COMPOSE up -d --build --remove-orphans "$@"; then
  log "up failed — re-sweeping zombies and retrying once..."
  sweep
  docker rm -f fim-one-fim-one-1 >/dev/null 2>&1 || true
  $COMPOSE up -d --build --remove-orphans "$@"
fi

log "Done."
$COMPOSE ps
