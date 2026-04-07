#!/bin/bash
set -euo pipefail

# ==============================================================================
# Script to manage the Cooja-RL Docker container.
# 1. If a container named 'cooja-rl' is already running, attach to it.
# 2. Otherwise, validate the frozen Cooja submodule and start a container.
# 3. If the image does not exist yet, build it first from this repo root.
# ==============================================================================

IMAGE_NAME="cooja-headless-rl:latest"
CONTAINER_NAME="cooja-rl"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
SETUP_SCRIPT="./setup_repo.sh"
CONTIKI_SUBMODULE="contiki-ng"
CONTIKI_DIR="$REPO_ROOT/$CONTIKI_SUBMODULE"
COOJA_SUBMODULE="contiki-ng/tools/cooja"
COOJA_DIR="$REPO_ROOT/$COOJA_SUBMODULE"
EXPECTED_CONTIKI_REMOTE="https://github.com/HI-AI-Lab/contiki-ng.git"
EXPECTED_REMOTE="https://github.com/HI-AI-Lab/cooja.git"

fail() {
    echo "[ERROR] $1" >&2
    exit 1
}

ensure_cooja_ready() {
    if ! git -C "$REPO_ROOT" rev-parse --show-toplevel >/dev/null 2>&1; then
        fail "Run this script from a git checkout of the testbed repository."
    fi

    local status
    status="$(git -C "$REPO_ROOT" submodule status -- "$CONTIKI_SUBMODULE" 2>/dev/null || true)"

    if [ -z "$status" ]; then
        fail "Missing submodule metadata for $CONTIKI_SUBMODULE."
    fi

    case "$status" in
        -*)
            fail "Contiki-NG is not initialized. Run: $SETUP_SCRIPT"
            ;;
        U*)
            fail "The Contiki-NG submodule is conflicted. Resolve it before starting Docker."
            ;;
    esac

    if ! git -C "$CONTIKI_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        fail "Contiki-NG checkout is missing at $CONTIKI_DIR. Run: $SETUP_SCRIPT"
    fi

    local actual_contiki_remote
    actual_contiki_remote="$(git -C "$CONTIKI_DIR" remote get-url origin 2>/dev/null || true)"

    case "$actual_contiki_remote" in
        *HI-AI-Lab/contiki-ng.git|*HI-AI-Lab/contiki-ng)
            ;;
        *)
            fail "Unexpected Contiki-NG remote '$actual_contiki_remote'. Expected $EXPECTED_CONTIKI_REMOTE."
            ;;
    esac

    if ! git -C "$COOJA_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        fail "Frozen Cooja checkout is missing at $COOJA_DIR. Run: $SETUP_SCRIPT"
    fi

    local actual_remote
    actual_remote="$(git -C "$COOJA_DIR" remote get-url origin 2>/dev/null || true)"

    case "$actual_remote" in
        *HI-AI-Lab/cooja.git|*HI-AI-Lab/cooja)
            ;;
        *)
            fail "Unexpected Cooja remote '$actual_remote'. Expected $EXPECTED_REMOTE."
            ;;
    esac
}

start_container() {
    docker run -it --rm --name "$CONTAINER_NAME" \
        -v "$REPO_ROOT:/workspace" \
        -v cooja_gradle_cache:/root/.gradle \
        -w /workspace "$IMAGE_NAME" bash
}

RUNNING_CONTAINER="$(docker ps -q -f name="$CONTAINER_NAME")"

if [ -n "$RUNNING_CONTAINER" ]; then
    echo "Found a running container '$CONTAINER_NAME'. Attaching to it..."
    docker exec -it "$CONTAINER_NAME" /bin/bash
    exit 0
fi

ensure_cooja_ready

IMAGE_EXISTS="$(docker images -q "$IMAGE_NAME")"

if [ -n "$IMAGE_EXISTS" ]; then
    echo "Docker image '$IMAGE_NAME' exists. Starting a new container..."
    start_container
    exit 0
fi

echo "Docker image '$IMAGE_NAME' not found. Building the image..."
docker build -t "$IMAGE_NAME" "$REPO_ROOT"
echo "Image build successful. Starting a new container..."
start_container

