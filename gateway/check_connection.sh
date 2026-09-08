#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"

source ./homeai_env.sh
source ./homeai_runtime.sh

homeai_migrate_legacy_env "./.env"
homeai_require_config
homeai_apply_a46_voice_migration
homeai_activate_runtime "./requirements.txt"
homeai_source_config

LOCAL_PORT="${OPENCLAW_LOCAL_PORT:-18790}"

echo "=== HomeAIAgent managed OpenClaw connectivity check ==="
echo "[CONFIG]  $HOMEAI_CONFIG_FILE"
echo "[RUNTIME] $HOMEAI_VENV"

if nc -z 127.0.0.1 "$LOCAL_PORT" >/dev/null 2>&1; then
  echo "[INFO] 127.0.0.1:${LOCAL_PORT} is already open."
  echo "[INFO] If HomeAIAgent is already running, the embedded transport is already owned by that service."
  echo "[INFO] Stop the running service before using this standalone connectivity check."
  exit 0
fi

python companion_gateway.py --check
