#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"

source ./homeai_env.sh
source ./homeai_runtime.sh

homeai_migrate_legacy_env "./.env"
homeai_require_config
homeai_require_complete_config
homeai_enforce_standard_voice
homeai_activate_runtime "./requirements.txt"
homeai_source_config

# Refuse to start with a stale or foreign TTS pairing.
if [ "${VOLCENGINE_TTS_RESOURCE_ID:-}" != "seed-tts-2.0" ] || \
   [ "${VOLCENGINE_TTS_VOICE:-}" != "zh_female_vv_uranus_bigtts" ]; then
  echo "[FAIL] Active Volc TTS pairing is not the validated standard voice."
  echo "[FAIL] resource=${VOLCENGINE_TTS_RESOURCE_ID:-<unset>}"
  echo "[FAIL] voice=${VOLCENGINE_TTS_VOICE:-<unset>}"
  exit 4
fi

echo "[CONFIG-GUARD] active standard voice confirmed: seed-tts-2.0 + zh_female_vv_uranus_bigtts"

TRANSPORT="${OPENCLAW_TRANSPORT:-embedded_ssh}"
LOCAL_PORT="${OPENCLAW_LOCAL_PORT:-18790}"

# companion_gateway.py owns the SSH forward. Refuse to stack a second forward.
if [ "$TRANSPORT" = "embedded_ssh" ] && nc -z 127.0.0.1 "$LOCAL_PORT" >/dev/null 2>&1; then
  echo "[FAIL] 127.0.0.1:${LOCAL_PORT} is already occupied."
  echo "[FAIL] HomeAIAgent manages the OpenClaw SSH transport in-process."
  echo "Stop any legacy manual SSH tunnel (or old HomeAIAgent process), then run ./run_full.sh again."
  exit 3
fi

# Source file must remain UTF-8; fail early with a clear message if a copy/editor changed encoding.
python - <<'PY'
from pathlib import Path
p = Path("companion_gateway.py")
p.read_bytes().decode("utf-8")
print("[SOURCE-ENCODING] companion_gateway.py UTF-8 OK")
PY

# One service launch only. companion_gateway.py now owns SSH transport,
# OpenClaw preflight, retries, device WebSocket, speech, notification and info.
exec python companion_gateway.py
