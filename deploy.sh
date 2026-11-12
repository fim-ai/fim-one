#!/usr/bin/env bash
# Idempotent deploy wrapper for fim-one: rebuild and recreate the application
# services of the compose project in this directory, leaving the data
# services (database, cache) running.
#
# Why not plain `docker compose up -d --build`: when compose recreates the
# application container while sandbox containers started for it still exist,
# removal of the old container can stall, the old container is left renamed
# to `<12-hex>_<name>`, and the next recreate collides on that name. This
# script makes the recreate deterministic:
#   1. remove this project's sandbox containers (named `fim-sandbox-<hex>`
#      and labelled by the sandbox proxy with `fim-one.project=<project>`),
#      and, once, `fim-sandbox-<hex>` containers left by the earlier proxy
#      (see "Unlabelled sandboxes" below);
#   2. remove leftover `<12-hex>_<project>-<service>-<n>` containers, and
#      `<12-hex>_<container_name>`, of the services that are recreated, when
#      their compose project label is this project;
#   3. remove the application containers explicitly, so `up` only creates;
#   4. `up -d --build`; on failure sweep again and retry once.
#
# Which project and which services
#   Everything is derived from compose itself, nothing is hard-coded:
#   - the project name and the compose file(s) are whatever `docker compose`
#     resolves here, so COMPOSE_PROJECT_NAME and COMPOSE_FILE (from the
#     environment or from .env) are honoured;
#   - the service list comes from `docker compose config --services`;
#   - the application services that are removed and recreated are those of
#     DEPLOY_APP_SERVICES (default: "fim-one sandbox docker-socket-proxy")
#     that the project actually has. Every other service is left running.
#   Containers are removed through compose by service. In addition the
#   names `<project>-fim-one-1`, `fim-one-fim-one-1` and `fim-one` are
#   removed if such a container exists and belongs to this project (or to
#   no compose project): leftovers of earlier layouts.
#
# Orphans
#   `--remove-orphans` deletes containers of the project whose service is
#   not in the compose file in use. With the default compose file that is
#   wanted (services that were renamed or dropped). With a non-default one
#   (COMPOSE_FILE set) it could delete containers that file simply does not
#   list, so it is not passed. DEPLOY_REMOVE_ORPHANS=1 forces it, =0 never.
#
# Unlabelled sandboxes
#   The proxy before this release did not label its sandboxes. Containers
#   named `fim-sandbox-<hex>` that carry neither `fim-one.project` nor
#   `fim-one.sandbox` are its; they cannot be told apart by project, so every
#   such container on the host is removed. That is done on the first run in
#   this directory only: after it succeeds, the file .deploy-swept-unlabelled
#   is written next to this script and later runs skip it.
#   DEPLOY_SWEEP_UNLABELLED=1 does it again whatever the file says, =0 never.
#
# DEPLOY_DRY_RUN=1 prints what would be removed and run, and changes nothing.
#
# Safe to run repeatedly. Run as: sudo ./deploy.sh [service ...]

set -euo pipefail

cd "$(dirname "$0")"

COMPOSE=(docker compose)
DRY_RUN="${DEPLOY_DRY_RUN:-0}"

log() { printf '\033[1;34m[deploy]\033[0m %s\n' "$*"; }

run() {
  if [[ "${DRY_RUN}" == "1" ]]; then
    printf '  would run: %s\n' "$*"
  else
    "$@"
  fi
}

# For removals whose failure is not an error (nothing to remove).
run_quiet() {
  if [[ "${DRY_RUN}" == "1" ]]; then
    printf '  would run: %s\n' "$*"
  else
    "$@" >/dev/null 2>&1 || true
  fi
}

# --- What compose resolves here -------------------------------------------
if ! CONFIG="$("${COMPOSE[@]}" config 2>/dev/null)"; then
  echo "deploy: 'docker compose config' failed in $(pwd); nothing was changed. Its error:" >&2
  "${COMPOSE[@]}" config >/dev/null || true
  exit 1
fi
PROJECT="$(printf '%s\n' "${CONFIG}" | sed -n 's/^name: *//p' | head -n 1)"
if [[ -z "${PROJECT}" ]]; then
  echo "deploy: 'docker compose config' did not resolve a project here (missing .env or compose file?)" >&2
  exit 1
fi
# Fixed container_names, as "<service><TAB><container_name>". Read from the
# service level of the normalized config only (two-space indent, as compose
# writes it): a key of the same name deeper down, such as an environment
# variable called container_name, is not one.
FIXED_BY_SERVICE="$(printf '%s\n' "${CONFIG}" | awk '
  /^[^ ]/ { top = $0; service = ""; next }
  top == "services:" && /^  [^ ]/ { service = $1; sub(/:$/, "", service); next }
  top == "services:" && service != "" && /^    container_name:/ {
    value = $0
    sub(/^    container_name:[ ]*/, "", value)
    gsub(/["\047]/, "", value)
    if (value != "") print service "\t" value
  }')"
SERVICES=()
while IFS= read -r service; do
  [[ -n "${service}" ]] && SERVICES+=("${service}")
done < <("${COMPOSE[@]}" config --services)

APP_SERVICES=()
for wanted in ${DEPLOY_APP_SERVICES:-fim-one sandbox docker-socket-proxy}; do
  for service in "${SERVICES[@]}"; do
    [[ "${service}" == "${wanted}" ]] && APP_SERVICES+=("${service}")
  done
done

# Fixed container_names of the services that are recreated, and only those:
# the data services' names (database, cache) are never swept by name.
FIXED_NAMES=()
for service in "${APP_SERVICES[@]+"${APP_SERVICES[@]}"}"; do
  while IFS=$'\t' read -r owner_service name; do
    [[ "${owner_service}" == "${service}" && -n "${name}" ]] && FIXED_NAMES+=("${name}")
  done <<<"${FIXED_BY_SERVICE}"
done

compose_file_set=0
if [[ -n "${COMPOSE_FILE:-}" ]] || { [[ -f .env ]] && grep -Eq '^[[:space:]]*COMPOSE_FILE=' .env; }; then
  compose_file_set=1
fi
UP_FLAGS=(-d --build)
case "${DEPLOY_REMOVE_ORPHANS:-auto}" in
  1) UP_FLAGS+=(--remove-orphans) ;;
  0) ;;
  *) [[ "${compose_file_set}" == "0" ]] && UP_FLAGS+=(--remove-orphans) ;;
esac

log "project: ${PROJECT}; services: ${SERVICES[*]}; recreating: ${APP_SERVICES[*]:-(none)}"

compose_project_of() {
  docker container inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' "$1" 2>/dev/null
}

# A container may be removed by name only if it is this project's, or no
# compose project's at all (containers of layouts before compose).
ours_by_name() {
  local owner
  owner="$(compose_project_of "$1")" || return 1
  [[ -z "${owner}" || "${owner}" == "${PROJECT}" ]]
}

# A renamed leftover is compose's, so it carries the project label: it is
# removed only when that label is this project.
ours_strictly() {
  local owner
  owner="$(compose_project_of "$1")" || return 1
  [[ -n "${owner}" && "${owner}" == "${PROJECT}" ]]
}

regex_escape() { printf '%s' "$1" | sed 's/[][\.*^$+?(){}|/]/\\&/g'; }

UNLABELLED_MARKER=".deploy-swept-unlabelled"

sweep_unlabelled_wanted() {
  case "${DEPLOY_SWEEP_UNLABELLED:-auto}" in
    1) return 0 ;;
    0) return 1 ;;
    *) [[ ! -e "${UNLABELLED_MARKER}" ]] ;;
  esac
}

# Remove the containers named on stdin, one per line; $1 says what they are.
# Says "removed" only for those `docker rm` did remove; fails if one was not.
remove_containers() {
  local kind="$1" name failed=0
  while IFS= read -r name; do
    [[ -z "${name}" ]] && continue
    if [[ "${DRY_RUN}" == "1" ]]; then
      printf '  would remove %s: %s\n' "${kind}" "${name}"
    elif docker rm -f "${name}" >/dev/null 2>&1; then
      printf '  removed %s: %s\n' "${kind}" "${name}"
    else
      printf '  could not remove %s: %s\n' "${kind}" "${name}" >&2
      failed=1
    fi
  done
  return "${failed}"
}

# --- Sandbox + zombie sweep ------------------------------------------------
# Factored out so we can run it both before `up` and again on a retry.
sweep() {
  # This project's sandbox containers (Python, Node.js and shell alike,
  # whatever image they run): the sandbox proxy labels each one it creates.
  # Sandboxes of other projects on this host are left alone.
  docker ps -a \
    --filter "label=fim-one.sandbox=1" --filter "label=fim-one.project=${PROJECT}" \
    --format '{{.Names}}' | grep -E '^fim-sandbox-[0-9a-f]+$' \
    | remove_containers sandbox || true

  # Sandboxes of the earlier proxy, which labelled none: see "Unlabelled
  # sandboxes" above. A container with either label is never one of them.
  if sweep_unlabelled_wanted; then
    if docker ps -a --filter "name=fim-sandbox-" \
      --format '{{.Names}}|{{.Label "fim-one.project"}}|{{.Label "fim-one.sandbox"}}' \
      | { grep -E '^fim-sandbox-[0-9a-f]+\|\|$' || true; } | cut -d'|' -f1 \
      | remove_containers "unlabelled sandbox"; then
      if [[ "${DRY_RUN}" == "1" ]]; then
        printf '  would write %s\n' "${UNLABELLED_MARKER}"
      else
        : >"${UNLABELLED_MARKER}"
      fi
    fi
  fi

  # Docker renames containers it cannot delete to `<12-hex>_<original-name>`.
  # Only the services that are recreated are matched, and a match is removed
  # only when its compose project label is this project: data services and
  # other projects' containers are never touched.
  # Compose's own names (`<project>-<service>-<n>`) first, then the fixed
  # container_names of those services.
  local patterns=() service fixed name zombies=""
  local project_escaped
  project_escaped="$(regex_escape "${PROJECT}")"
  for service in "${APP_SERVICES[@]+"${APP_SERVICES[@]}"}"; do
    patterns+=("^[0-9a-f]{12}_${project_escaped}-$(regex_escape "${service}")-[0-9]+\$")
  done
  for fixed in "${FIXED_NAMES[@]+"${FIXED_NAMES[@]}"}"; do
    patterns+=("^[0-9a-f]{12}_$(regex_escape "${fixed}")\$")
  done
  if [[ "${#patterns[@]}" -gt 0 ]]; then
    local grep_args=() pattern
    for pattern in "${patterns[@]}"; do
      grep_args+=(-e "${pattern}")
    done
    while IFS= read -r name; do
      if [[ -n "${name}" ]] && ours_strictly "${name}"; then
        zombies="${zombies:+${zombies}
}${name}"
      fi
    done < <(docker ps -a --format '{{.Names}}' | grep -E "${grep_args[@]}" || true)
  fi
  if [[ -n "${zombies}" ]]; then
    printf '%s\n' "${zombies}" | remove_containers zombie || true
  fi
}

remove_app_containers() {
  if [[ "${#APP_SERVICES[@]}" -gt 0 ]]; then
    run_quiet "${COMPOSE[@]}" rm -f -s -v "${APP_SERVICES[@]}"
  fi
  # Leftovers compose no longer knows by service (earlier layouts).
  local name
  for name in "${PROJECT}-fim-one-1" fim-one-fim-one-1 fim-one; do
    if ours_by_name "${name}"; then
      run_quiet docker rm -f "${name}"
    fi
  done
}

# --- 1+2. Pre-up sweep ----------------------------------------------------
log "Sweeping sandbox containers + hash-prefixed zombies..."
sweep

# --- 3. Explicitly remove the application containers before up ------------
# Deleting the old containers up front means `up` performs a fresh create
# instead of the rename-and-recreate that produces zombies. Data services
# are left running.
log "Removing the application containers before recreate..."
remove_app_containers

# --- 4. Bring the stack up (build), with one self-healing retry -----------
log "Building and starting services (${UP_FLAGS[*]})..."
if ! run "${COMPOSE[@]}" up "${UP_FLAGS[@]}" "$@"; then
  log "up failed — sweeping again and retrying once..."
  sweep
  remove_app_containers
  run "${COMPOSE[@]}" up "${UP_FLAGS[@]}" "$@"
fi

log "Done."
run "${COMPOSE[@]}" ps
