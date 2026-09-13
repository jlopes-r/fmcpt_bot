#!/usr/bin/env bash
# Safe on-VM updater. The deploy implementation itself is loaded from the
# fetched target commit, so running this file never mutates its own execution.
set -Eeuo pipefail
IFS=$'\n\t'

REPO_DIR="${REPO_DIR:-/home/juanl/bot}"
BRANCH="${DEPLOY_BRANCH:-main}"
SERVICE="${SUPERBOT_SERVICE:-superbot.service}"

usage() {
    cat <<'EOF'
Usage: ./scripts/update.sh [deploy options]

Supported options:
  --skip-dependencies
  --skip-tests
  --force-restart
  --health-timeout SECONDS
  --stability-seconds SECONDS
  --retain-venvs COUNT

REPO_DIR, DEPLOY_BRANCH and SUPERBOT_SERVICE may be set in the environment.
EOF
}

EXTRA_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-dependencies|--skip-tests|--force-restart)
            EXTRA_ARGS+=("$1")
            shift
            ;;
        --health-timeout|--stability-seconds|--retain-venvs)
            [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 64; }
            EXTRA_ARGS+=("$1" "$2")
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 64
            ;;
    esac
done

[[ "$REPO_DIR" == /* && "$REPO_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]] \
    || { echo "Invalid REPO_DIR: $REPO_DIR" >&2; exit 64; }
[[ "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]] \
    || { echo "Invalid DEPLOY_BRANCH: $BRANCH" >&2; exit 64; }
[[ "$SERVICE" =~ ^[A-Za-z0-9_.@-]+\.service$ ]] \
    || { echo "Invalid SUPERBOT_SERVICE: $SERVICE" >&2; exit 64; }

cd "$REPO_DIR"
[[ -d .git ]] || { echo "$REPO_DIR is not a Git worktree" >&2; exit 1; }

echo "[update] Fetching origin/$BRANCH (the bot remains online)..."
git fetch --prune origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
TARGET=$(git rev-parse --verify "refs/remotes/origin/$BRANCH^{commit}")
git cat-file -e "${TARGET}:scripts/deploy_remote.sh"

echo "[update] Starting transactional deploy of $TARGET..."
git show "${TARGET}:scripts/deploy_remote.sh" | bash -s -- \
    --repo "$REPO_DIR" \
    --service "$SERVICE" \
    --branch "$BRANCH" \
    --target "$TARGET" \
    "${EXTRA_ARGS[@]}"
