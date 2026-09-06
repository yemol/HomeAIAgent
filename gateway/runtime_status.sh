#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"
source ./homeai_env.sh
source ./homeai_runtime.sh

echo "=== HomeAIAgent Persistent Runtime Status ==="
echo "Config : $HOMEAI_CONFIG_FILE"
echo "Runtime: $HOMEAI_VENV"
echo

if [ -f "$HOMEAI_CONFIG_FILE" ]; then
  echo "[OK] persistent config exists"
else
  echo "[MISSING] persistent config"
fi

if homeai_runtime_exists; then
  echo "[OK] persistent venv exists"
  "$HOMEAI_VENV/bin/python" --version
  "$HOMEAI_VENV/bin/python" -m pip --version
else
  echo "[MISSING] persistent venv"
fi

if [ -d ".venv" ]; then
  echo
  echo "[INFO] Legacy project-local .venv still exists."
  echo "       It is no longer used and may be deleted after the persistent runtime is verified."
fi
