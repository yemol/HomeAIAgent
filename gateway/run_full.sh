#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"

source ./homeai_env.sh
source ./homeai_runtime.sh

homeai_migrate_legacy_env "./.env"
homeai_require_config
homeai_apply_a46_voice_migration
homeai_activate_runtime "./requirements.txt"

# Read tunnel port from persistent config.
homeai_source_config

# A1R7 hard guard: refuse to start with a stale/foreign TTS pairing.
if [ "${VOLCENGINE_TTS_RESOURCE_ID:-}" != "seed-tts-2.0" ] ||    [ "${VOLCENGINE_TTS_VOICE:-}" != "zh_female_vv_uranus_bigtts" ]; then
  echo "[FAIL] Active Volc TTS pairing is not the A1R7 standard voice."
  echo "[FAIL] resource=${VOLCENGINE_TTS_RESOURCE_ID:-<unset>}"
  echo "[FAIL] voice=${VOLCENGINE_TTS_VOICE:-<unset>}"
  exit 4
fi

echo "[CONFIG-GUARD] active standard voice confirmed: seed-tts-2.0 + zh_female_vv_uranus_bigtts"
LOCAL_PORT="${OPENCLAW_LOCAL_PORT:-18790}"

if ! nc -z 127.0.0.1 "$LOCAL_PORT" >/dev/null 2>&1; then
  echo "[FAIL] OpenClaw SSH tunnel is not running on 127.0.0.1:${LOCAL_PORT}."
  echo "Open another Terminal and run:"
  echo "  ./openclaw_air_tunnel.sh"
  exit 3
fi

python companion_gateway.py --check
exec python companion_gateway.py
