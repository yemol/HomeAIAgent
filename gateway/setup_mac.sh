#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"

source ./homeai_env.sh
source ./homeai_runtime.sh

echo
echo "========================================================"
echo " HomeAIAgent Gateway | Persistent Config + Runtime"
echo "========================================================"
echo " Config : $HOMEAI_CONFIG_FILE"
echo " Runtime: $HOMEAI_VENV"
echo

# 1) Persistent runtime.
homeai_bootstrap_runtime "./requirements.txt"

# 2) One-time persistent config migration / creation.
homeai_migrate_legacy_env "./.env"

if [ -f "$HOMEAI_CONFIG_FILE" ]; then
  chmod 600 "$HOMEAI_CONFIG_FILE"
  echo "[OK] Existing persistent config retained."
  echo "[OK] API keys/tokens were NOT overwritten."
else
  mkdir -p "$HOMEAI_CONFIG_DIR"

  echo
  echo "[1/2] 豆包语音 API Key"
  read -s "VOLC_KEY?VOLCENGINE_API_KEY: "
  echo
  if [ -z "$VOLC_KEY" ]; then
    echo "[FAIL] VOLCENGINE_API_KEY 不能为空。"
    exit 3
  fi

  echo
  echo "[2/2] Air OpenClaw Gateway token"
  read -s "OPENCLAW_KEY?OpenClaw token: "
  echo
  if [ -z "$OPENCLAW_KEY" ]; then
    echo "[FAIL] OPENCLAW_TOKEN 不能为空。"
    exit 4
  fi

  TMP="$HOMEAI_CONFIG_FILE.tmp"
  cat > "$TMP" <<EOF
P0_MODE=full
GATEWAY_HOST=0.0.0.0
GATEWAY_PORT=8765
GATEWAY_PATH=/companion

ASR_PROVIDER=volcengine
TTS_PROVIDER=volcengine
ASR_FALLBACK=none
TTS_FALLBACK=none

VOLCENGINE_API_KEY=${VOLC_KEY}

VOLCENGINE_ASR_ENDPOINT=wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async
VOLCENGINE_ASR_RESOURCE_ID=volc.seedasr.sauc.duration
VOLCENGINE_ASR_CHUNK_MS=200
VOLCENGINE_ASR_SEND_INTERVAL_MS=100
VOLCENGINE_ASR_ENABLE_NONSTREAM=true
VOLCENGINE_ASR_END_WINDOW_MS=800
VOLCENGINE_ASR_FORCE_TO_SPEECH_MS=1000
VOLCENGINE_ASR_CONNECT_TIMEOUT=8
VOLCENGINE_ASR_FINAL_TIMEOUT=12

VOLCENGINE_TTS_ENDPOINT=wss://openspeech.bytedance.com/api/v3/tts/unidirectional/stream
VOLCENGINE_TTS_RESOURCE_ID=seed-tts-2.0
VOLCENGINE_TTS_VOICE=zh_female_vv_uranus_bigtts
VOLCENGINE_TTS_SAMPLE_RATE=16000
VOLCENGINE_TTS_SPEECH_RATE=0
VOLCENGINE_TTS_LOUDNESS_RATE=0

DEVICE_TTS_SEGMENT_MAX_BYTES=786432
DEVICE_TTS_PLAYBACK_MARGIN_SEC=15

OPENAI_API_KEY=

OPENCLAW_BASE_URL=http://127.0.0.1:18790
OPENCLAW_TOKEN=${OPENCLAW_KEY}
OPENCLAW_MODEL=openclaw/default
OPENCLAW_USER=home-ai-agent:main
MAX_AGENT_CHARS=600

OPENCLAW_TRANSPORT=embedded_ssh
OPENCLAW_SSH_USER=yuanxiang
OPENCLAW_SSH_HOST=100.105.66.46
OPENCLAW_LOCAL_PORT=18790
OPENCLAW_REMOTE_PORT=18789
OPENCLAW_SSH_CONNECT_TIMEOUT_SEC=10
OPENCLAW_SSH_RECONNECT_MIN_SEC=2
OPENCLAW_SSH_RECONNECT_MAX_SEC=30
OPENCLAW_SSH_STARTUP_WAIT_SEC=15
EOF
  chmod 600 "$TMP"
  mv "$TMP" "$HOMEAI_CONFIG_FILE"
  unset VOLC_KEY OPENCLAW_KEY
  echo "[OK] Persistent config created."
fi

homeai_apply_a46_voice_migration

python protocol_selftest.py

echo
echo "[READY]"
echo " Config persists at:"
echo "   $HOMEAI_CONFIG_FILE"
echo " Python runtime persists at:"
echo "   $HOMEAI_VENV"
echo
echo "Normal daily start:"
echo "  ./run_full.sh"
