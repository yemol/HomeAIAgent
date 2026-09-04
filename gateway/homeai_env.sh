#!/bin/zsh
set -euo pipefail

export HOMEAI_CONFIG_DIR="${HOMEAI_CONFIG_DIR:-$HOME/.config/HomeAIAgent}"
export HOMEAI_CONFIG_FILE="${HOMEAI_CONFIG_FILE:-$HOMEAI_CONFIG_DIR/gateway.env}"

homeai_migrate_legacy_env() {
  local legacy="${1:-./.env}"
  if [ -f "$HOMEAI_CONFIG_FILE" ]; then
    return 0
  fi
  if [ -f "$legacy" ]; then
    mkdir -p "$HOMEAI_CONFIG_DIR"
    cp "$legacy" "$HOMEAI_CONFIG_FILE"
    chmod 600 "$HOMEAI_CONFIG_FILE"
    echo "[CONFIG] migrated $legacy -> $HOMEAI_CONFIG_FILE"
  fi
}

homeai_require_config() {
  if [ ! -f "$HOMEAI_CONFIG_FILE" ]; then
    echo "[FAIL] Persistent config missing:"
    echo "       $HOMEAI_CONFIG_FILE"
    echo "Run ./setup_mac.sh"
    exit 2
  fi
}

homeai_source_config() {
  homeai_require_config
  set -a
  source "$HOMEAI_CONFIG_FILE"
  set +a
}
