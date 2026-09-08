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

# A1R7 hard guard: refuse to start with a stale/foreign TTS pairing.
if [ "${VOLCENGINE_TTS_RESOURCE_ID:-}" != "seed-tts-2.0" ] || \
   [ "${VOLCENGINE_TTS_VOICE:-}" != "zh_female_vv_uranus_bigtts" ]; then
  echo "[FAIL] Active Volc TTS pairing is not the A1R7 standard voice."
  echo "[FAIL] resource=${VOLCENGINE_TTS_RESOURCE_ID:-<unset>}"
  echo "[FAIL] voice=${VOLCENGINE_TTS_VOICE:-<unset>}"
  exit 4
fi

echo "[CONFIG-GUARD] active standard voice confirmed: seed-tts-2.0 + zh_female_vv_uranus_bigtts"

TRANSPORT="${OPENCLAW_TRANSPORT:-embedded_ssh}"
LOCAL_PORT="${OPENCLAW_LOCAL_PORT:-18790}"

# A1R11 owns the SSH forward inside companion_gateway.py. Refuse to silently
# stack a second forward on top of the legacy manual tunnel.
if [ "$TRANSPORT" = "embedded_ssh" ] && nc -z 127.0.0.1 "$LOCAL_PORT" >/dev/null 2>&1; then
  echo "[FAIL] 127.0.0.1:${LOCAL_PORT} is already occupied."
  echo "[FAIL] A1R11 manages the OpenClaw SSH transport inside HomeAIAgent."
  echo "Stop the legacy ./openclaw_air_tunnel.sh terminal (or an old HomeAIAgent process), then run ./run_full.sh again."
  exit 3
fi

# One service launch only. companion_gateway.py now owns SSH transport,
# OpenClaw preflight, retries, device WebSocket, speech, notification and info.
exec python companion_gateway.py
