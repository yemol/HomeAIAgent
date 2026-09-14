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

homeai_bootstrap_runtime "./requirements.txt"
homeai_migrate_legacy_env "./.env"

if [ -f "$HOMEAI_CONFIG_FILE" ]; then
  chmod 600 "$HOMEAI_CONFIG_FILE"
  echo "[OK] Existing persistent config retained."
  echo "[OK] API keys/tokens were NOT overwritten."

  MISSING="$(homeai_missing_required_config || true)"
  if [ -n "$MISSING" ]; then
    echo "[MIGRATE] Existing config predates the current embedded-SSH schema."
    echo "$MISSING" | while IFS= read -r key; do
      [ -n "$key" ] && echo "          missing: $key"
    done
    echo "[MIGRATE] Launching one-time persistent config repair..."
    ./configure_mac.sh
  fi
else
  echo "[1/4] Volcengine speech API key"
  read -s "VOLC_KEY?VOLCENGINE_API_KEY: "
  echo
  [ -n "$VOLC_KEY" ] || { echo "[FAIL] VOLCENGINE_API_KEY cannot be empty."; exit 3; }

  echo "[2/4] OpenClaw Gateway token"
  read -s "OPENCLAW_KEY?OPENCLAW_TOKEN: "
  echo
  [ -n "$OPENCLAW_KEY" ] || { echo "[FAIL] OPENCLAW_TOKEN cannot be empty."; exit 4; }

  echo "[3/4] OpenClaw SSH user"
  read "SSH_USER?SSH user: "
  [ -n "$SSH_USER" ] || { echo "[FAIL] OPENCLAW_SSH_USER cannot be empty."; exit 5; }

  echo "[4/4] OpenClaw SSH host"
  read "SSH_HOST?Tailscale IP / hostname: "
  [ -n "$SSH_HOST" ] || { echo "[FAIL] OPENCLAW_SSH_HOST cannot be empty."; exit 6; }

  mkdir -p "$HOMEAI_CONFIG_DIR"
  cp ./gateway.env.example "$HOMEAI_CONFIG_FILE"
  chmod 600 "$HOMEAI_CONFIG_FILE"
  homeai_upsert_config_value VOLCENGINE_API_KEY "$VOLC_KEY"
  homeai_upsert_config_value OPENCLAW_TOKEN "$OPENCLAW_KEY"
  homeai_upsert_config_value OPENCLAW_SSH_USER "$SSH_USER"
  homeai_upsert_config_value OPENCLAW_SSH_HOST "$SSH_HOST"
  unset VOLC_KEY OPENCLAW_KEY SSH_USER SSH_HOST
  echo "[OK] Persistent config created from gateway.env.example."
fi

homeai_enforce_standard_voice
homeai_activate_runtime "./requirements.txt"
homeai_source_config
python protocol_selftest.py

echo
echo "[READY]"
echo " Config persists at: $HOMEAI_CONFIG_FILE"
echo " Python runtime persists at: $HOMEAI_VENV"
echo " Daily start: ./run_full.sh"
