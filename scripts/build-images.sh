#!/usr/bin/env bash
# Build the FIM One app image and the code-sandbox image for one or more CPU
# architectures (linux/amd64, linux/arm64) with docker buildx.
#
# `./deploy.sh` builds on the deploy host itself, so on an ARM64 server
# (e.g. Kunpeng) it already produces ARM64 images and needs no changes.
# Use this script when the images are built on a different machine than the
# one that runs them, e.g. building ARM64 images on an x86 workstation for an
# offline ARM64 server, or checking that both architectures still build.
#
# Usage:
#   scripts/build-images.sh [--platform amd64|arm64|all] [--tag TAG] [--save DIR]
#
#   --platform  architecture(s) to build (default: the Docker host's own)
#   --tag       tag suffix (default: latest). Images are tagged
#                 fim-one:<TAG>-<arch>  and  fim-sandbox:python-<arch>
#   --save DIR  also write each architecture's two images to
#                 DIR/fim-one-<TAG>-<arch>.tar  (load with `docker load -i`)
#
# Building a non-native architecture runs under QEMU emulation and takes
# several times longer. Docker Desktop ships QEMU; on a Linux host register it
# once with:  docker run --privileged --rm tonistiigi/binfmt --install arm64
# (or amd64). That container is privileged because it registers the emulator
# in the host kernel's binfmt_misc.
#
# Each architecture is built and loaded separately (`--load` cannot load a
# multi-platform image into the classic image store).

set -euo pipefail

# Resolved before the cd below: both may be relative to the caller's directory.
script="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
caller_dir="$PWD"

platform=""
tag="latest"
save_dir=""

usage() { sed -n '2,27p' "${script}"; }

need_value() {
  if [[ $# -lt 2 || -z "$2" ]]; then
    echo "$1 needs a value" >&2
    usage >&2
    exit 2
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform) need_value "$@"; platform="$2"; shift 2 ;;
    --tag) need_value "$@"; tag="$2"; shift 2 ;;
    --save) need_value "$@"; save_dir="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# A relative --save is relative to where the script was called from.
if [[ -n "${save_dir}" && "${save_dir}" != /* ]]; then
  save_dir="${caller_dir}/${save_dir}"
fi

cd "$(dirname "${script}")/.."

if [[ -z "${platform}" ]]; then
  platform=$(docker version --format '{{.Server.Arch}}')
fi

case "${platform}" in
  all) arches=(amd64 arm64) ;;
  amd64|x86_64) arches=(amd64) ;;
  arm64|aarch64) arches=(arm64) ;;
  *) echo "unsupported platform: ${platform} (use amd64, arm64 or all)" >&2; exit 2 ;;
esac

log() { printf '\033[1;34m[build-images]\033[0m %s\n' "$*"; }

for arch in "${arches[@]}"; do
  app_image="fim-one:${tag}-${arch}"
  sandbox_image="fim-sandbox:python-${arch}"

  log "Building ${app_image} (linux/${arch})..."
  docker buildx build --platform "linux/${arch}" --load -t "${app_image}" .

  log "Building ${sandbox_image} (linux/${arch})..."
  docker buildx build --platform "linux/${arch}" --load \
    -f Dockerfile.sandbox -t "${sandbox_image}" .

  if [[ -n "${save_dir}" ]]; then
    mkdir -p "${save_dir}"
    out="${save_dir}/fim-one-${tag}-${arch}.tar"
    log "Saving ${app_image} + ${sandbox_image} to ${out}..."
    docker save -o "${out}" "${app_image}" "${sandbox_image}"
  fi
done

log "Done."
docker image ls --format '{{.Repository}}:{{.Tag}}\t{{.Size}}' \
  | grep -E "^(fim-one:${tag}|fim-sandbox:python)-(amd64|arm64)" || true
