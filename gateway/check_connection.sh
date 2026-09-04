#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"

source ./homeai_env.sh
source ./homeai_runtime.sh

homeai_migrate_legacy_env "./.env"
homeai_require_config
homeai_activate_runtime "./requirements.txt"
homeai_source_config

LOCAL_PORT="${OPENCLAW_LOCAL_PORT:-18790}"

echo "=== HomeAIAgent persistent runtime connectivity check ==="
echo "[CONFIG]  $HOMEAI_CONFIG_FILE"
echo "[RUNTIME] $HOMEAI_VENV"

if ! nc -z 127.0.0.1 "$LOCAL_PORT" >/dev/null 2>&1; then
  echo "[FAIL] 127.0.0.1:${LOCAL_PORT} is closed."
  echo "Start ./openclaw_air_tunnel.sh in another Terminal."
  exit 3
fi
echo "[OK] SSH tunnel endpoint 127.0.0.1:${LOCAL_PORT} is open."

python companion_gateway.py --check
