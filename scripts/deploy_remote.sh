#!/usr/bin/env bash
# Transactional deploy executed on the Linux VM.
# It stages and tests a commit-specific venv while the current bot stays online,
# then atomically switches the runtime and rolls code + venv back on failure.
set -Eeuo pipefail
IFS=$'\n\t'
umask 027

REPO_DIR="/home/juanl/bot"
SERVICE="superbot.service"
COMMANDS_SERVICE="comandosbot.service"
BRANCH="main"
TARGET=""
HEALTH_TIMEOUT=45
STABILITY_SECONDS=12
RETAIN_VENVS=4
SKIP_DEPENDENCIES=0
SKIP_TESTS=0
FORCE_RESTART=0

usage() {
    cat <<'EOF'
Usage: deploy_remote.sh [options]
  --repo PATH                 Repository path (default: /home/juanl/bot)
  --service NAME              systemd unit (default: superbot.service)
  --commands-service NAME     Secondary commands unit (default: comandosbot.service)
  --branch NAME               Remote branch (default: main)
  --target SHA                Already-fetched commit to deploy
  --health-timeout SECONDS    Overall service health timeout (default: 45)
  --stability-seconds SECONDS Required stable process window (default: 12)
  --retain-venvs COUNT        Number of successful release venvs to retain
  --skip-dependencies         Explicitly reuse the current runtime
  --skip-tests                Skip the candidate unit-test suite
  --force-restart             Redeploy/restart even when HEAD is unchanged
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo) REPO_DIR=${2:?missing value for --repo}; shift 2 ;;
        --service) SERVICE=${2:?missing value for --service}; shift 2 ;;
        --commands-service) COMMANDS_SERVICE=${2:?missing value for --commands-service}; shift 2 ;;
        --branch) BRANCH=${2:?missing value for --branch}; shift 2 ;;
        --target) TARGET=${2:?missing value for --target}; shift 2 ;;
        --health-timeout) HEALTH_TIMEOUT=${2:?missing value for --health-timeout}; shift 2 ;;
        --stability-seconds) STABILITY_SECONDS=${2:?missing value for --stability-seconds}; shift 2 ;;
        --retain-venvs) RETAIN_VENVS=${2:?missing value for --retain-venvs}; shift 2 ;;
        --skip-dependencies) SKIP_DEPENDENCIES=1; shift ;;
        --skip-tests) SKIP_TESTS=1; shift ;;
        --force-restart) FORCE_RESTART=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 64 ;;
    esac
done

log() { printf '[deploy] %s\n' "$*"; }
fail() { printf '[deploy] ERROR: %s\n' "$*" >&2; return 1; }
require_command() { command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"; }

[[ "$REPO_DIR" == /* && "$REPO_DIR" != "/" ]] || fail "--repo must be a safe absolute path"
[[ "$REPO_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]] || fail "--repo contains unsupported characters"
[[ "$SERVICE" =~ ^[A-Za-z0-9_.@-]+\.service$ ]] || fail "invalid systemd service name"
[[ "$COMMANDS_SERVICE" =~ ^[A-Za-z0-9_.@-]+\.service$ ]] || fail "invalid commands service name"
[[ "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]] || fail "invalid branch name"
[[ "$HEALTH_TIMEOUT" =~ ^[0-9]+$ && "$HEALTH_TIMEOUT" -ge 10 ]] || fail "invalid health timeout"
[[ "$STABILITY_SECONDS" =~ ^[0-9]+$ && "$STABILITY_SECONDS" -ge 3 ]] || fail "invalid stability window"
[[ "$RETAIN_VENVS" =~ ^[0-9]+$ && "$RETAIN_VENVS" -ge 2 ]] || fail "retain count must be at least 2"

for command_name in git python3 tar flock systemctl; do
    require_command "$command_name"
done

cd "$REPO_DIR"
[[ -d .git ]] || fail "$REPO_DIR is not a Git worktree"
[[ -f apps/telegram_bot/requirements.txt ]] || fail "requirements file is missing"
[[ -f apps/telegram_bot/.env && -r apps/telegram_bot/.env ]] || fail "bot .env is missing or unreadable"
[[ -x venv/bin/python || -L .deploy/current-venv ]] || fail "legacy venv and managed runtime are both missing"

DEPLOY_DIR="$REPO_DIR/.deploy"
VENV_ROOT="$DEPLOY_DIR/venvs"
CURRENT_LINK="$DEPLOY_DIR/current-venv"
STATE_FILE="$DEPLOY_DIR/last-successful"
HEALTH_RUNNER="$DEPLOY_DIR/health_check.py"
DROPIN_DIR="/etc/systemd/system/${SERVICE}.d"
DROPIN_FILE="$DROPIN_DIR/20-superbot-release.conf"
CHANGELOG_FILE="$REPO_DIR/data/update_superbot.json"
COMMANDS_DROPIN_DIR="/etc/systemd/system/${COMMANDS_SERVICE}.d"
COMMANDS_DROPIN_FILE="$COMMANDS_DROPIN_DIR/20-superbot-release.conf"
COMMANDS_CHANGELOG_FILE="$REPO_DIR/data/update_comandos.json"
mkdir -p "$VENV_ROOT"

exec 9>"$DEPLOY_DIR/deploy.lock"
flock -n 9 || fail "another deploy is already running"

sudo -v
systemctl cat "$SERVICE" >/dev/null 2>&1 || fail "systemd unit not found: $SERVICE"
COMMANDS_INSTALLED=0
if systemctl cat "$COMMANDS_SERVICE" >/dev/null 2>&1; then
    COMMANDS_INSTALLED=1
else
    log "$COMMANDS_SERVICE is not installed; secondary bot deploy will be skipped."
fi

if [[ -z "$TARGET" ]]; then
    log "Fetching origin/$BRANCH..."
    git fetch --prune origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
    TARGET=$(git rev-parse "refs/remotes/origin/$BRANCH")
fi
TARGET=$(git rev-parse --verify "${TARGET}^{commit}")
[[ "$TARGET" =~ ^[0-9a-f]{40,64}$ ]] || fail "invalid target revision"

OLD_HEAD=$(git rev-parse --verify HEAD)
CURRENT_BRANCH=$(git symbolic-ref --quiet --short HEAD || true)
[[ "$CURRENT_BRANCH" == "$BRANCH" ]] || fail "expected branch '$BRANCH', found '${CURRENT_BRANCH:-detached HEAD}'"
git diff --quiet || fail "tracked files have unstaged changes; deploy aborted"
git diff --cached --quiet || fail "tracked files have staged changes; deploy aborted"
git merge-base --is-ancestor "$OLD_HEAD" "$TARGET" || fail "origin/$BRANCH is not a fast-forward of $OLD_HEAD"

COMMANDS_AFFECTED=0
if [[ "$COMMANDS_INSTALLED" -eq 1 ]]; then
    if [[ "$FORCE_RESTART" -eq 1 ]]; then
        COMMANDS_AFFECTED=1
    elif git diff --name-only "$OLD_HEAD" "$TARGET" | grep -qE \
        '^(apps/comandos/|packages/|assets/|apps/telegram_bot/requirements\.txt$)'; then
        COMMANDS_AFFECTED=1
    fi
fi

# Always use the candidate's health checker, including when rolling back to a
# commit that predates this deployment implementation.
git show "${TARGET}:scripts/health_check.py" >"${HEALTH_RUNNER}.new"
python3 -m py_compile "${HEALTH_RUNNER}.new"
mv -f "${HEALTH_RUNNER}.new" "$HEALTH_RUNNER"

if [[ "$OLD_HEAD" == "$TARGET" && "$FORCE_RESTART" -eq 0 && -L "$CURRENT_LINK" ]] \
    && sudo test -f "$DROPIN_FILE"; then
    log "No new commit. Verifying the running release without restarting it."
    python3 "$HEALTH_RUNNER" \
        --repo "$REPO_DIR" \
        --service "$SERVICE" \
        --expected-python "$CURRENT_LINK/bin/python" \
        --timeout "$HEALTH_TIMEOUT" \
        --stability-seconds "$STABILITY_SECONDS"
    if [[ "$COMMANDS_INSTALLED" -eq 1 ]]; then
        python3 "$HEALTH_RUNNER" \
            --repo "$REPO_DIR" \
            --service "$COMMANDS_SERVICE" \
            --entrypoint "comandos_bot.py" \
            --token-env "BOT_TOKEN_COMANDOS" \
            --timeout "$HEALTH_TIMEOUT" \
            --stability-seconds "$STABILITY_SECONDS"
    fi
    exit 0
fi

PREVIEW_DIR=$(mktemp -d /tmp/superbot-preflight.XXXXXX)
LINK_TEMP="${CURRENT_LINK}.next.$$"
DROPIN_TEMP="$DEPLOY_DIR/20-superbot-release.conf.new"
DROPIN_BACKUP="$DEPLOY_DIR/20-superbot-release.conf.previous"
CHANGELOG_BACKUP="$DEPLOY_DIR/update_superbot.json.previous"
COMMANDS_DROPIN_TEMP="$DEPLOY_DIR/20-comandosbot-release.conf.new"
COMMANDS_DROPIN_BACKUP="$DEPLOY_DIR/20-comandosbot-release.conf.previous"
COMMANDS_CHANGELOG_BACKUP="$DEPLOY_DIR/update_comandos.json.previous"
HAD_CURRENT_LINK=0
HAD_DROPIN=0
HAD_CHANGELOG=0
HAD_COMMANDS_DROPIN=0
HAD_COMMANDS_CHANGELOG=0
ROLLBACK_ARMED=0

cleanup() {
    rm -f -- "$LINK_TEMP" "${HEALTH_RUNNER}.new" "$DROPIN_TEMP" "$COMMANDS_DROPIN_TEMP"
    if [[ -n "${PREVIEW_DIR:-}" && -d "$PREVIEW_DIR" ]]; then
        rm -rf -- "$PREVIEW_DIR"
    fi
}

restore_symlink() {
    rm -f -- "$LINK_TEMP"
    if [[ "$HAD_CURRENT_LINK" -eq 1 ]]; then
        ln -s "$PREVIOUS_LINK_TARGET" "$LINK_TEMP"
        mv -Tf "$LINK_TEMP" "$CURRENT_LINK"
    else
        rm -f -- "$CURRENT_LINK"
    fi
}

restore_dropin() {
    if [[ "$HAD_DROPIN" -eq 1 ]]; then
        sudo install -d -m 0755 "$DROPIN_DIR"
        sudo install -m 0644 "$DROPIN_BACKUP" "${DROPIN_FILE}.rollback"
        sudo mv -f "${DROPIN_FILE}.rollback" "$DROPIN_FILE"
    else
        sudo rm -f -- "$DROPIN_FILE"
    fi
    if [[ "$COMMANDS_AFFECTED" -eq 1 ]]; then
        if [[ "$HAD_COMMANDS_DROPIN" -eq 1 ]]; then
            sudo install -d -m 0755 "$COMMANDS_DROPIN_DIR"
            sudo install -m 0644 "$COMMANDS_DROPIN_BACKUP" "${COMMANDS_DROPIN_FILE}.rollback"
            sudo mv -f "${COMMANDS_DROPIN_FILE}.rollback" "$COMMANDS_DROPIN_FILE"
        else
            sudo rm -f -- "$COMMANDS_DROPIN_FILE"
        fi
    fi
    sudo systemctl daemon-reload
}

restore_changelog() {
    if [[ "$HAD_CHANGELOG" -eq 1 ]]; then
        install -m 0640 "$CHANGELOG_BACKUP" "${CHANGELOG_FILE}.rollback"
        mv -f "${CHANGELOG_FILE}.rollback" "$CHANGELOG_FILE"
    else
        rm -f -- "$CHANGELOG_FILE"
    fi
    if [[ "$COMMANDS_AFFECTED" -eq 1 ]]; then
        if [[ "$HAD_COMMANDS_CHANGELOG" -eq 1 ]]; then
            install -m 0640 "$COMMANDS_CHANGELOG_BACKUP" "${COMMANDS_CHANGELOG_FILE}.rollback"
            mv -f "${COMMANDS_CHANGELOG_FILE}.rollback" "$COMMANDS_CHANGELOG_FILE"
        else
            rm -f -- "$COMMANDS_CHANGELOG_FILE"
        fi
    fi
}

rollback() {
    local rollback_failed=0
    log "Health check failed; rolling back code and runtime to $OLD_HEAD..."
    if [[ "$COMMANDS_AFFECTED" -eq 1 ]]; then
        sudo systemctl stop "$COMMANDS_SERVICE" || rollback_failed=1
    fi
    sudo systemctl stop "$SERVICE" || rollback_failed=1
    git reset --hard "$OLD_HEAD" || rollback_failed=1
    restore_symlink || rollback_failed=1
    restore_dropin || rollback_failed=1
    restore_changelog || rollback_failed=1
    sudo systemctl start "$SERVICE" || rollback_failed=1
    if [[ "$COMMANDS_AFFECTED" -eq 1 ]]; then
        sudo systemctl start "$COMMANDS_SERVICE" || rollback_failed=1
    fi
    if [[ "$rollback_failed" -eq 0 ]]; then
        python3 "$HEALTH_RUNNER" \
            --repo "$REPO_DIR" \
            --service "$SERVICE" \
            --expected-python "$PREVIOUS_RUNTIME/bin/python" \
            --timeout "$HEALTH_TIMEOUT" \
            --stability-seconds "$STABILITY_SECONDS" || rollback_failed=1
    fi
    if [[ "$rollback_failed" -eq 0 && "$COMMANDS_AFFECTED" -eq 1 ]]; then
        python3 "$HEALTH_RUNNER" \
            --repo "$REPO_DIR" \
            --service "$COMMANDS_SERVICE" \
            --entrypoint "comandos_bot.py" \
            --token-env "BOT_TOKEN_COMANDOS" \
            --expected-python "$PREVIOUS_RUNTIME/bin/python" \
            --timeout "$HEALTH_TIMEOUT" \
            --stability-seconds "$STABILITY_SECONDS" || rollback_failed=1
    fi
    if [[ "$rollback_failed" -eq 0 ]]; then
        log "Rollback completed; the previous release is healthy."
        return 0
    fi
    printf '[deploy] CRITICAL: automatic rollback did not restore a healthy service.\n' >&2
    sudo journalctl -u "$SERVICE" --no-pager -n 100 >&2 || true
    if [[ "$COMMANDS_AFFECTED" -eq 1 ]]; then
        sudo journalctl -u "$COMMANDS_SERVICE" --no-pager -n 100 >&2 || true
    fi
    return 1
}

handle_failure() {
    local status=${1:-1}
    local line=${2:-unknown}
    trap - ERR INT TERM EXIT
    set +e
    printf '[deploy] Deployment failed near line %s (exit=%s).\n' "$line" "$status" >&2
    sudo journalctl -u "$SERVICE" --no-pager -n 60 >&2 || true
    if [[ "$COMMANDS_AFFECTED" -eq 1 ]]; then
        sudo journalctl -u "$COMMANDS_SERVICE" --no-pager -n 60 >&2 || true
    fi
    if [[ "$ROLLBACK_ARMED" -eq 1 ]]; then
        rollback || status=2
    fi
    cleanup
    exit "$status"
}

trap 'handle_failure $? ${BASH_LINENO[0]:-unknown}' ERR
trap 'handle_failure 130 ${LINENO}' INT
trap 'handle_failure 143 ${LINENO}' TERM
trap cleanup EXIT

log "Extracting candidate $TARGET for offline pre-validation..."
git archive "$TARGET" | tar -x -C "$PREVIEW_DIR"
python3 "$HEALTH_RUNNER" --repo "$REPO_DIR" --config-only

if [[ -L "$CURRENT_LINK" ]]; then
    HAD_CURRENT_LINK=1
    PREVIOUS_LINK_TARGET=$(readlink "$CURRENT_LINK")
    PREVIOUS_RUNTIME=$(readlink -f "$CURRENT_LINK")
else
    [[ ! -e "$CURRENT_LINK" ]] || fail "$CURRENT_LINK exists but is not a symlink"
    PREVIOUS_LINK_TARGET=""
    PREVIOUS_RUNTIME="$REPO_DIR/venv"
fi
[[ -x "$PREVIOUS_RUNTIME/bin/python" ]] || fail "current Python runtime is invalid: $PREVIOUS_RUNTIME"

NEW_VENV="$VENV_ROOT/$TARGET"
NEW_RUNTIME="$NEW_VENV"
if [[ "$SKIP_DEPENDENCIES" -eq 1 ]]; then
    log "Dependency staging explicitly skipped; reusing $PREVIOUS_RUNTIME."
    NEW_RUNTIME="$PREVIOUS_RUNTIME"
elif [[ -f "$NEW_VENV/.superbot-ready" && -x "$NEW_VENV/bin/python" ]]; then
    log "Reusing the already validated venv for $TARGET."
else
    if [[ -e "$NEW_VENV" ]]; then
        [[ "$NEW_VENV" == "$VENV_ROOT/$TARGET" ]] || fail "unsafe venv cleanup target"
        rm -rf -- "$NEW_VENV"
    fi
    log "Creating the staged release venv (the current bot remains online)..."
    python3 -m venv "$NEW_VENV"
    "$NEW_VENV/bin/python" -m pip install --disable-pip-version-check \
        -r "$PREVIEW_DIR/apps/telegram_bot/requirements.txt"
    "$NEW_VENV/bin/python" -m pip check
fi

log "Compiling the candidate source..."
"$NEW_RUNTIME/bin/python" -m compileall -q \
    "$PREVIEW_DIR/apps" "$PREVIEW_DIR/packages" "$PREVIEW_DIR/scripts"

if [[ "$SKIP_TESTS" -eq 0 ]]; then
    log "Running the candidate unit tests before any restart..."
    (
        cd "$PREVIEW_DIR"
        PYTHONPATH="$PREVIEW_DIR" "$NEW_RUNTIME/bin/python" \
            -m unittest discover -s tests -p 'test_*.py'
    )
fi

if [[ "$SKIP_DEPENDENCIES" -eq 0 ]]; then
    printf '%s\n' "$TARGET" >"$NEW_VENV/.superbot-ready"
fi

if sudo test -f "$DROPIN_FILE"; then
    HAD_DROPIN=1
    sudo cat "$DROPIN_FILE" >"$DROPIN_BACKUP"
fi
if [[ -f "$CHANGELOG_FILE" ]]; then
    HAD_CHANGELOG=1
    cp -p "$CHANGELOG_FILE" "$CHANGELOG_BACKUP"
fi
if [[ "$COMMANDS_AFFECTED" -eq 1 ]] && sudo test -f "$COMMANDS_DROPIN_FILE"; then
    HAD_COMMANDS_DROPIN=1
    sudo cat "$COMMANDS_DROPIN_FILE" >"$COMMANDS_DROPIN_BACKUP"
fi
if [[ "$COMMANDS_AFFECTED" -eq 1 && -f "$COMMANDS_CHANGELOG_FILE" ]]; then
    HAD_COMMANDS_CHANGELOG=1
    cp -p "$COMMANDS_CHANGELOG_FILE" "$COMMANDS_CHANGELOG_BACKUP"
fi

cat >"$DROPIN_TEMP" <<EOF
[Service]
ExecStart=
ExecStart=$CURRENT_LINK/bin/python $REPO_DIR/apps/telegram_bot/super_bot.py
Environment=SUPERBOT_DEPLOY_MANAGED=1
EOF

if [[ "$COMMANDS_AFFECTED" -eq 1 ]]; then
    cat >"$COMMANDS_DROPIN_TEMP" <<EOF
[Service]
ExecStart=
ExecStart=$CURRENT_LINK/bin/python $REPO_DIR/apps/comandos/comandos_bot.py
Environment=SUPERBOT_DEPLOY_MANAGED=1
EOF
fi

ROLLBACK_ARMED=1
log "Fast-forwarding the working tree while the old process remains online..."
git merge --ff-only "$TARGET"

if [[ "$OLD_HEAD" != "$TARGET" ]]; then
    python3 "$REPO_DIR/scripts/write_update_changelog.py" \
        --repo "$REPO_DIR" --old "$OLD_HEAD" --new "$TARGET" \
        --output "$CHANGELOG_FILE"
    if [[ "$COMMANDS_AFFECTED" -eq 1 ]]; then
        python3 "$REPO_DIR/scripts/write_update_changelog.py" \
            --repo "$REPO_DIR" --old "$OLD_HEAD" --new "$TARGET" \
            --output "$COMMANDS_CHANGELOG_FILE"
    fi
fi

log "Installing the systemd runtime override..."
sudo install -d -m 0755 "$DROPIN_DIR"
sudo install -m 0644 "$DROPIN_TEMP" "${DROPIN_FILE}.new"
sudo mv -f "${DROPIN_FILE}.new" "$DROPIN_FILE"
if [[ "$COMMANDS_AFFECTED" -eq 1 ]]; then
    sudo install -d -m 0755 "$COMMANDS_DROPIN_DIR"
    sudo install -m 0644 "$COMMANDS_DROPIN_TEMP" "${COMMANDS_DROPIN_FILE}.new"
    sudo mv -f "${COMMANDS_DROPIN_FILE}.new" "$COMMANDS_DROPIN_FILE"
fi
sudo systemctl daemon-reload

log "Switching to the staged venv and restarting $SERVICE..."
ln -s "$NEW_RUNTIME" "$LINK_TEMP"
mv -Tf "$LINK_TEMP" "$CURRENT_LINK"
sudo systemctl restart "$SERVICE"
if [[ "$COMMANDS_AFFECTED" -eq 1 ]]; then
    sudo systemctl restart "$COMMANDS_SERVICE"
fi

python3 "$HEALTH_RUNNER" \
    --repo "$REPO_DIR" \
    --service "$SERVICE" \
    --expected-python "$CURRENT_LINK/bin/python" \
    --timeout "$HEALTH_TIMEOUT" \
    --stability-seconds "$STABILITY_SECONDS"

if [[ "$COMMANDS_AFFECTED" -eq 1 ]]; then
    python3 "$HEALTH_RUNNER" \
        --repo "$REPO_DIR" \
        --service "$COMMANDS_SERVICE" \
        --entrypoint "comandos_bot.py" \
        --token-env "BOT_TOKEN_COMANDOS" \
        --expected-python "$CURRENT_LINK/bin/python" \
        --timeout "$HEALTH_TIMEOUT" \
        --stability-seconds "$STABILITY_SECONDS"
fi

printf 'commit=%s\nruntime=%s\ncompleted_at=%s\n' \
    "$TARGET" "$NEW_RUNTIME" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"${STATE_FILE}.new"
mv -f "${STATE_FILE}.new" "$STATE_FILE"
ROLLBACK_ARMED=0

# Keep a small rollback window without ever deleting the active runtime.
if [[ "$SKIP_DEPENDENCIES" -eq 0 ]]; then
    mapfile -t RELEASE_VENVS < <(
        find "$VENV_ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
            | sort -nr | cut -d' ' -f2-
    )
    for ((index=RETAIN_VENVS; index<${#RELEASE_VENVS[@]}; index++)); do
        stale=${RELEASE_VENVS[$index]}
        stale_name=$(basename "$stale")
        if [[ "$stale" != "$NEW_RUNTIME" && "$stale" != "$PREVIOUS_RUNTIME" \
            && "$stale" == "$VENV_ROOT/"* && "$stale_name" =~ ^[0-9a-f]{40,64}$ ]]; then
            rm -rf -- "$stale"
        fi
    done
fi

rm -f -- "$DROPIN_BACKUP" "$CHANGELOG_BACKUP" \
    "$COMMANDS_DROPIN_BACKUP" "$COMMANDS_CHANGELOG_BACKUP" || true
log "SUCCESS - release $TARGET is healthy."
sudo journalctl -u "$SERVICE" --no-pager -n 20 || true
if [[ "$COMMANDS_AFFECTED" -eq 1 ]]; then
    sudo journalctl -u "$COMMANDS_SERVICE" --no-pager -n 20 || true
fi
