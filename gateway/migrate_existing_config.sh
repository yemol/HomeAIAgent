#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/homeai_env.sh"

SOURCE_GATEWAY="${1:-$PWD}"
LEGACY="$SOURCE_GATEWAY/.env"

if [ -f "$HOMEAI_CONFIG_FILE" ]; then
  chmod 600 "$HOMEAI_CONFIG_FILE"
  echo "[OK] Persistent config already exists:"
  echo "     $HOMEAI_CONFIG_FILE"
  echo "[OK] Nothing overwritten."
  exit 0
fi

if [ ! -f "$LEGACY" ]; then
  echo "[FAIL] Cannot find old config:"
  echo "       $LEGACY"
  echo
  echo "Usage:"
  echo "  ./migrate_existing_config.sh /path/to/your/current/gateway"
  exit 2
fi

mkdir -p "$HOMEAI_CONFIG_DIR"
TMP="$HOMEAI_CONFIG_FILE.tmp"
cp "$LEGACY" "$TMP"
chmod 600 "$TMP"
mv "$TMP" "$HOMEAI_CONFIG_FILE"

echo "[OK] Migrated:"
echo "     $LEGACY"
echo "  -> $HOMEAI_CONFIG_FILE"
echo
echo "[SAFE] You can now delete/replace the whole old HomeAIAgent project."
