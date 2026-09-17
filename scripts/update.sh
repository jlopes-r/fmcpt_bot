#!/usr/bin/env bash
# Safe on-VM updater. The deploy implementation itself is loaded from the
# fetched target commit, so running this file never mutates its own execution.
set -Eeuo pipefail
IFS=$'\n\t'
umask 027

REPO_DIR="${REPO_DIR:-/home/juanl/bot}"
BRANCH="${DEPLOY_BRANCH:-main}"
SERVICE="${SUPERBOT_SERVICE:-superbot.service}"
DEPLOY_LOG_KEEP="${DEPLOY_LOG_KEEP:-10}"

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
Detailed output is saved under data/logs/deploy. Set DEPLOY_LOG_KEEP to change
the number of update logs retained (default: 10).
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
[[ "$DEPLOY_LOG_KEEP" =~ ^[0-9]+$ && "$DEPLOY_LOG_KEEP" -ge 1 ]] \
    || { echo "Invalid DEPLOY_LOG_KEEP: $DEPLOY_LOG_KEEP" >&2; exit 64; }

cd "$REPO_DIR"
[[ -d .git ]] || { echo "$REPO_DIR is not a Git worktree" >&2; exit 1; }

LOG_DIR="$REPO_DIR/data/logs/deploy"
mkdir -p "$LOG_DIR"
chmod 0750 "$LOG_DIR"
LOG_FILE="$LOG_DIR/update-$(date -u +%Y%m%dT%H%M%SZ)-$$.log"

prune_update_logs() {
    local index stale stale_name
    local -a update_logs=()
    mapfile -t update_logs < <(
        find "$LOG_DIR" -mindepth 1 -maxdepth 1 -type f \
            -name 'update-*.log' -printf '%T@ %p\n' \
            | sort -nr | cut -d' ' -f2-
    )
    for ((index=DEPLOY_LOG_KEEP; index<${#update_logs[@]}; index++)); do
        stale=${update_logs[$index]}
        stale_name=$(basename "$stale")
        if [[ "$stale" == "$LOG_DIR/"* && "$stale_name" == update-*.log ]]; then
            rm -f -- "$stale"
        fi
    done
}

echo "[update] Fetching origin/$BRANCH (the bot remains online)..."
git fetch --prune origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH" \
    2>&1 | tee -a "$LOG_FILE"
TARGET=$(git rev-parse --verify "refs/remotes/origin/$BRANCH^{commit}")
git cat-file -e "${TARGET}:scripts/deploy_remote.sh"

echo "[update] Starting transactional deploy of $TARGET..."
echo "[update] Detailed log: $LOG_FILE"

set +e
git show "${TARGET}:scripts/deploy_remote.sh" \
    | bash -s -- \
        --repo "$REPO_DIR" \
        --service "$SERVICE" \
        --branch "$BRANCH" \
        --target "$TARGET" \
        "${EXTRA_ARGS[@]}" \
        2>&1 \
    | tee -a "$LOG_FILE" \
    | awk '
        /^\[deploy\]/ ||
        /^OK - / ||
        /^Ran [0-9]+ tests/ ||
        /^OK$/ ||
        /^FAILED/ ||
        /^ERROR/ {
            print
            fflush()
        }
    '
pipeline_status=("${PIPESTATUS[@]}")
set -e

git_show_status=${pipeline_status[0]:-1}
deploy_status=${pipeline_status[1]:-1}
tee_status=${pipeline_status[2]:-1}
summary_status=${pipeline_status[3]:-1}

if [[ "$git_show_status" -ne 0 || "$deploy_status" -ne 0 \
    || "$tee_status" -ne 0 || "$summary_status" -ne 0 ]]; then
    printf '[update] ERROR: deploy failed; last details from %s:\n' "$LOG_FILE" >&2
    tail -n 80 "$LOG_FILE" >&2 || true
    prune_update_logs
    if [[ "$deploy_status" -ne 0 ]]; then
        exit "$deploy_status"
    fi
    exit 1
fi

prune_update_logs
echo "[update] Completed successfully. Details: $LOG_FILE"
