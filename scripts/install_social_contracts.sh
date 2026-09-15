#!/usr/bin/env bash
# Instala o teste real periodico usando o caminho efetivo do repositorio.
set -Eeuo pipefail
IFS=$'\n\t'

REPO_DIR="${REPO_DIR:-/home/juanl/bot}"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"
RUN_NOW=0

usage() {
    cat <<'EOF'
Usage: install_social_contracts.sh [--repo PATH] [--user USER] [--run-now]
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo) REPO_DIR=${2:?missing value for --repo}; shift 2 ;;
        --user) SERVICE_USER=${2:?missing value for --user}; shift 2 ;;
        --run-now) RUN_NOW=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 64 ;;
    esac
done

[[ "$REPO_DIR" == /* && "$REPO_DIR" != "/" ]] \
    || { echo "--repo must be a safe absolute path" >&2; exit 64; }
[[ "$REPO_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]] \
    || { echo "--repo contains unsupported characters" >&2; exit 64; }
[[ "$SERVICE_USER" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]] \
    || { echo "--user is invalid" >&2; exit 64; }
[[ -f "$REPO_DIR/apps/telegram_bot/.env" ]] \
    || { echo "bot .env is missing" >&2; exit 1; }
[[ -x "$REPO_DIR/.deploy/current-venv/bin/python" ]] \
    || { echo "managed Python runtime is missing" >&2; exit 1; }

service_tmp=$(mktemp /tmp/social-contracts.service.XXXXXX)
cleanup() { rm -f -- "$service_tmp"; }
trap cleanup EXIT

sed \
    -e "s|@REPO_DIR@|$REPO_DIR|g" \
    -e "s|User=juanl|User=$SERVICE_USER|g" \
    "$REPO_DIR/scripts/social-contracts.service" >"$service_tmp"

sudo install -m 0644 "$service_tmp" /etc/systemd/system/social-contracts.service
sudo install -m 0644 \
    "$REPO_DIR/scripts/social-contracts.timer" \
    /etc/systemd/system/social-contracts.timer
sudo systemctl daemon-reload
sudo systemctl enable --now social-contracts.timer

if [[ "$RUN_NOW" -eq 1 ]]; then
    sudo systemctl start social-contracts.service
fi

systemctl --no-pager status social-contracts.timer
