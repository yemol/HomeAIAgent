#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"
source ./homeai_env.sh

mkdir -p "$HOMEAI_CONFIG_DIR"
if [ ! -f "$HOMEAI_CONFIG_FILE" ]; then
  cp ./gateway.env.example "$HOMEAI_CONFIG_FILE"
  chmod 600 "$HOMEAI_CONFIG_FILE"
  echo "[INFO] Created config from gateway.env.example"
else
  STAMP="$(date +%Y%m%d_%H%M%S)"
  cp "$HOMEAI_CONFIG_FILE" "$HOMEAI_CONFIG_FILE.bak_$STAMP"
  chmod 600 "$HOMEAI_CONFIG_FILE.bak_$STAMP"
  echo "[BACKUP] $HOMEAI_CONFIG_FILE.bak_$STAMP"
fi

echo "Reconfigure HomeAIAgent persistent connection settings."
echo "Press Enter for secret fields to keep the existing value."

read -s "VOLC_KEY?VOLCENGINE_API_KEY (Enter=keep): "
echo
read -s "OPENCLAW_KEY?OPENCLAW_TOKEN (Enter=keep): "
echo

CURRENT_USER="$(homeai_config_get OPENCLAW_SSH_USER)"
CURRENT_HOST="$(homeai_config_get OPENCLAW_SSH_HOST)"
CURRENT_LOCAL_PORT="$(homeai_config_get OPENCLAW_LOCAL_PORT)"
CURRENT_REMOTE_PORT="$(homeai_config_get OPENCLAW_REMOTE_PORT)"
CURRENT_LOCAL_PORT="${CURRENT_LOCAL_PORT:-18790}"
CURRENT_REMOTE_PORT="${CURRENT_REMOTE_PORT:-18789}"

read "SSH_USER?OpenClaw SSH user [${CURRENT_USER:-required}]: "
SSH_USER="${SSH_USER:-$CURRENT_USER}"
[ -n "$SSH_USER" ] || { echo "[FAIL] SSH user cannot be empty."; exit 3; }

read "SSH_HOST?OpenClaw Tailscale IP / hostname [${CURRENT_HOST:-required}]: "
SSH_HOST="${SSH_HOST:-$CURRENT_HOST}"
[ -n "$SSH_HOST" ] || { echo "[FAIL] SSH host cannot be empty."; exit 3; }

read "LOCAL_PORT?Local tunnel port [$CURRENT_LOCAL_PORT]: "
LOCAL_PORT="${LOCAL_PORT:-$CURRENT_LOCAL_PORT}"
read "REMOTE_PORT?Remote OpenClaw port [$CURRENT_REMOTE_PORT]: "
REMOTE_PORT="${REMOTE_PORT:-$CURRENT_REMOTE_PORT}"

[ -z "$VOLC_KEY" ] || homeai_upsert_config_value VOLCENGINE_API_KEY "$VOLC_KEY"
[ -z "$OPENCLAW_KEY" ] || homeai_upsert_config_value OPENCLAW_TOKEN "$OPENCLAW_KEY"
homeai_upsert_config_value OPENCLAW_SSH_USER "$SSH_USER"
homeai_upsert_config_value OPENCLAW_SSH_HOST "$SSH_HOST"
homeai_upsert_config_value OPENCLAW_LOCAL_PORT "$LOCAL_PORT"
homeai_upsert_config_value OPENCLAW_REMOTE_PORT "$REMOTE_PORT"
homeai_upsert_config_value OPENCLAW_BASE_URL "http://127.0.0.1:${LOCAL_PORT}"
chmod 600 "$HOMEAI_CONFIG_FILE"
unset VOLC_KEY OPENCLAW_KEY SSH_USER SSH_HOST

echo "[OK] Updated connection settings without replacing unrelated config keys:"
echo "     $HOMEAI_CONFIG_FILE"
