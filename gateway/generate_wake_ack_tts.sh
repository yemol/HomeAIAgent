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

if [ "${VOLCENGINE_TTS_RESOURCE_ID:-}" != "seed-tts-2.0" ] || \
   [ "${VOLCENGINE_TTS_VOICE:-}" != "zh_female_vv_uranus_bigtts" ]; then
  echo "[FAIL] Active Volc TTS pairing is not the HomeAIAgent standard voice."
  echo "[FAIL] resource=${VOLCENGINE_TTS_RESOURCE_ID:-<unset>}"
  echo "[FAIL] voice=${VOLCENGINE_TTS_VOICE:-<unset>}"
  exit 4
fi

exec python ./generate_wake_ack_tts.py
