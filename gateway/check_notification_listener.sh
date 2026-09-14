#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"

source ./homeai_env.sh
source ./homeai_runtime.sh

homeai_migrate_legacy_env "./.env"
homeai_require_config
homeai_enforce_standard_voice
homeai_activate_runtime "./requirements.txt"
homeai_source_config

LOCAL_PORT="${OPENCLAW_LOCAL_PORT:-18790}"
if ! nc -z 127.0.0.1 "$LOCAL_PORT" >/dev/null 2>&1; then
  echo "[FAIL] Managed OpenClaw transport is not active on 127.0.0.1:${LOCAL_PORT}."
  echo "Start ./run_full.sh first, then run this listener check in another Terminal."
  exit 3
fi

exec python check_notification_listener.py
