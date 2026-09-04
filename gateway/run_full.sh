#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"

source ./homeai_env.sh
source ./homeai_runtime.sh

homeai_migrate_legacy_env "./.env"
homeai_require_config
homeai_activate_runtime "./requirements.txt"

# Read tunnel port from persistent config.
homeai_source_config
LOCAL_PORT="${OPENCLAW_LOCAL_PORT:-18790}"

if ! nc -z 127.0.0.1 "$LOCAL_PORT" >/dev/null 2>&1; then
  echo "[FAIL] OpenClaw SSH tunnel is not running on 127.0.0.1:${LOCAL_PORT}."
  echo "Open another Terminal and run:"
  echo "  ./openclaw_air_tunnel.sh"
  exit 3
fi

python companion_gateway.py --check
exec python companion_gateway.py
