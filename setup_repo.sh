#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTIKI_DIR="$REPO_ROOT/contiki-ng"
COOJA_DIR="$CONTIKI_DIR/tools/cooja"
EXPECTED_CONTIKI_REMOTE="https://github.com/HI-AI-Lab/contiki-ng.git"
EXPECTED_COOJA_REMOTE="https://github.com/HI-AI-Lab/cooja.git"

say() {
    echo "[setup] $1"
}

fail() {
    echo "[error] $1" >&2
    exit 1
}

require_repo() {
    command -v git >/dev/null 2>&1 || fail "git is required."
    git -C "$REPO_ROOT" rev-parse --show-toplevel >/dev/null 2>&1 \
        || fail "Run this script from a git checkout of the testbed repository."
}

enable_longpaths() {
    git -C "$REPO_ROOT" config core.longpaths true || true
}

clear_stale_locks() {
    local modules_dir="$REPO_ROOT/.git/modules"

    if [ ! -d "$modules_dir" ]; then
        return
    fi

    while IFS= read -r -d '' lock_file; do
        say "Removing stale lock ${lock_file#$REPO_ROOT/}"
        rm -f "$lock_file"
    done < <(find "$modules_dir" -type f -name index.lock -print0 2>/dev/null)
}

sync_and_update() {
    say "Syncing submodule URLs..."
    git -C "$REPO_ROOT" -c core.longpaths=true submodule sync --recursive

    say "Initializing submodules with LFS smudge disabled..."
    GIT_LFS_SKIP_SMUDGE=1 git -C "$REPO_ROOT" -c core.longpaths=true submodule update --init --recursive

    if git -C "$CONTIKI_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        git -C "$CONTIKI_DIR" config core.longpaths true || true
        say "Refreshing nested Contiki-NG submodules..."
        git -C "$CONTIKI_DIR" -c core.longpaths=true submodule sync --recursive
        GIT_LFS_SKIP_SMUDGE=1 git -C "$CONTIKI_DIR" -c core.longpaths=true submodule update --init --recursive
    fi
}

verify_remote() {
    local repo_dir="$1"
    local expected_remote="$2"
    local label="$3"
    local actual_remote

    actual_remote="$(git -C "$repo_dir" remote get-url origin 2>/dev/null || true)"
    case "$actual_remote" in
        "$expected_remote"|${expected_remote%.git})
            ;;
        *)
            fail "$label remote is '$actual_remote' but expected '$expected_remote'."
            ;;
    esac
}

verify_layout() {
    git -C "$CONTIKI_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
        || fail "Contiki-NG checkout is missing. Re-run ./setup_repo.sh"
    git -C "$COOJA_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
        || fail "Cooja checkout is missing. Re-run ./setup_repo.sh"

    verify_remote "$CONTIKI_DIR" "$EXPECTED_CONTIKI_REMOTE" "Contiki-NG"
    verify_remote "$COOJA_DIR" "$EXPECTED_COOJA_REMOTE" "Cooja"
}

require_repo
enable_longpaths
clear_stale_locks
sync_and_update
verify_layout

say "Repository dependencies are ready."
say "Next steps:"
say "  1. docker build -t cooja-headless-rl:latest ."
say "  2. ./docker_run.sh"
say "Note: setup skips optional Git LFS downloads used by non-testbed Gecko SDK content."
