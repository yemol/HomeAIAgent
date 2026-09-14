#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"

source ./homeai_env.sh
source ./homeai_runtime.sh

homeai_migrate_legacy_env "./.env"
homeai_require_config
homeai_enforce_standard_voice
homeai_activate_runtime "./requirements.txt"

echo "=== HomeAIAgent ASR2 + TTS protocol ==="
python protocol_selftest.py

echo
echo "=== Gateway/OpenClaw preflight ==="
python companion_gateway.py --check

echo
echo "[RUNTIME] $HOMEAI_VENV"
echo "[CONFIG]  $HOMEAI_CONFIG_FILE"
