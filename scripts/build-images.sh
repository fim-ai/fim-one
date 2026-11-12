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
# once with:  docker run --privileged --rm tonistiigi/binfmt --install all
#
# Each architecture is built and loaded separately (`--load` cannot load a
# multi-platform image into the classic image store).

set -euo pipefail

cd "$(dirname "$0")/.."

platform=""
tag="latest"
save_dir=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform) platform="$2"; shift 2 ;;
    --tag) tag="$2"; shift 2 ;;
    --save) save_dir="$2"; shift 2 ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

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
