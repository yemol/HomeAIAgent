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
if ! nc -z 127.0.0.1 "$LOCAL_PORT" >/dev/null 2>&1; then
  echo "[FAIL] OpenClaw SSH tunnel is not running on 127.0.0.1:${LOCAL_PORT}."
  echo "Start ./openclaw_air_tunnel.sh first."
  exit 3
fi

exec python check_notification_listener.py
