#!/usr/bin/env bash
# Build the vafi-developer image family (body + harness leaves).
#
# See docs/developer-images-DESIGN.md for the architecture.
#
# Usage:
#   ./build-developer-images.sh                 # build everything (base + all three leaves)
#   ./build-developer-images.sh base            # build only the base
#   ./build-developer-images.sh claude          # build only the claude leaf
#   ./build-developer-images.sh pi              # build only the pi leaf
#   ./build-developer-images.sh gemini          # build only the gemini leaf
#   ./build-developer-images.sh all             # same as no args
#
# Environment overrides:
#   VAFI_REGISTRY     = vafi       (registry prefix for tags; e.g. harbor.viloforge.com/vafi)
#   BASE_TAG          = <today>    (date suffix for vafi-developer-base tag; auto YYYY-MM-DD)
#   CLAUDE_VERSION    = latest     (npm version pin for Claude Code)
#   PI_VERSION        = latest     (npm version pin for Pi)
#   GEMINI_VERSION    = latest     (npm version pin for Gemini CLI)

set -euo pipefail

REGISTRY="${VAFI_REGISTRY:-vafi}"
# Where `publish` pushes. The ViloForge Harbor (project `vafi` already exists; the k8s fleet
# pushes there). Requires `docker login harbor.viloforge.com` first (laptop dev images are not
# yet built by the in-cluster Argo/kaniko pipeline — see workflow-template-build-vafi.yaml).
PUBLISH_REGISTRY="${PUBLISH_REGISTRY:-harbor.viloforge.com/vafi}"
BASE_TAG="${BASE_TAG:-$(date +%Y-%m-%d)}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
CTX="$REPO_ROOT/images/developer"

log() { echo -e "\033[36m==>\033[0m $*" >&2; }
err() { echo -e "\033[31m!!\033[0m $*" >&2; }

# Retag locally-built ${REGISTRY}/<repo>:<tag> images to ${PUBLISH_REGISTRY}/<repo>:<tag> and push.
publish() {
  local target="${1:-}"
  local repo tags
  case "$target" in
    base)               repo=vafi-developer-base; tags="${BASE_TAG} latest" ;;
    claude|pi|gemini|agy)
      repo=vafi-developer
      tags=$(docker images --format '{{.Tag}}' "${REGISTRY}/vafi-developer" | grep -E "^${target}(-|\$)" | tr '\n' ' ')
      ;;
    *) err "publish: target required — base|claude|pi|gemini|agy"; exit 2 ;;
  esac
  [ -n "${tags// }" ] || { err "publish: no local ${REGISTRY}/${repo}:${target}* images to push"; exit 2; }
  for t in $tags; do
    log "Publishing ${PUBLISH_REGISTRY}/${repo}:${t}"
    docker tag "${REGISTRY}/${repo}:${t}" "${PUBLISH_REGISTRY}/${repo}:${t}"
    docker push "${PUBLISH_REGISTRY}/${repo}:${t}"
  done
}

build_base() {
  log "Building ${REGISTRY}/vafi-developer-base:${BASE_TAG}"
  docker build \
    --build-arg "REGISTRY=${REGISTRY}" \
    -t "${REGISTRY}/vafi-developer-base:${BASE_TAG}" \
    -t "${REGISTRY}/vafi-developer-base:latest" \
    -f "${CTX}/Dockerfile.base" \
    "${CTX}"
  log "  tagged: ${BASE_TAG}, latest"
}

build_leaf() {
  local harness="$1"
  local version_var; version_var="$(echo "${harness}" | tr '[:lower:]' '[:upper:]')_VERSION"
  local version="${!version_var:-latest}"
  local dockerfile="${CTX}/Dockerfile.${harness}"

  if [ ! -f "$dockerfile" ]; then
    err "No Dockerfile for harness '$harness' at $dockerfile"; return 1
  fi

  # Probe the actual installed CLI version by running a throwaway build
  # that stops at the version-check layer, or just trust the pinned version.
  # Simplest: if user pinned, use the pin; else use today's date as a fallback label.
  local version_tag
  if [ "$version" = "latest" ]; then
    # Build first, then extract version from image for the pinned tag.
    log "Building ${REGISTRY}/vafi-developer:${harness} (probing ${harness} CLI version…)"
    docker build \
      --build-arg "REGISTRY=${REGISTRY}" \
      --build-arg "BASE_TAG=${BASE_TAG}" \
      --build-arg "${version_var}=latest" \
      -t "${REGISTRY}/vafi-developer:${harness}" \
      -f "$dockerfile" \
      "${CTX}"
    # Extract version from inside the image
    version_tag=$(docker run --rm --entrypoint="" \
                    "${REGISTRY}/vafi-developer:${harness}" \
                    "${harness}" --version 2>&1 | head -1 | awk '{print $1}')
    if [ -z "$version_tag" ]; then
      err "Could not determine ${harness} version; pinned tag skipped"
    else
      docker tag "${REGISTRY}/vafi-developer:${harness}" \
                 "${REGISTRY}/vafi-developer:${harness}-${version_tag}"
      log "  tagged: ${harness}, ${harness}-${version_tag}"
    fi
  else
    log "Building ${REGISTRY}/vafi-developer:${harness}-${version}"
    docker build \
      --build-arg "REGISTRY=${REGISTRY}" \
      --build-arg "BASE_TAG=${BASE_TAG}" \
      --build-arg "${version_var}=${version}" \
      -t "${REGISTRY}/vafi-developer:${harness}-${version}" \
      -t "${REGISTRY}/vafi-developer:${harness}" \
      -f "$dockerfile" \
      "${CTX}"
    log "  tagged: ${harness}-${version}, ${harness}"
  fi
}

main() {
  local target="${1:-all}"

  case "$target" in
    base)
      build_base
      ;;
    claude|pi|gemini|agy)
      build_leaf "$target"
      ;;
    all|"")
      build_base
      build_leaf claude
      build_leaf pi
      build_leaf gemini
      build_leaf agy
      ;;
    publish)
      # publish <base|claude|pi|gemini|agy> — push already-built images to $PUBLISH_REGISTRY
      publish "${2:-}"
      ;;
    *)
      err "Unknown target: $target"
      err "Usage: $0 [base|claude|pi|gemini|agy|all] | publish <target>"
      exit 2
      ;;
  esac

  log "Done."
  docker images "${REGISTRY}/vafi-developer*" | head -20
}

main "$@"
