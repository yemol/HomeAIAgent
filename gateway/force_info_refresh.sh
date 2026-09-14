#!/bin/zsh
set -e

cd "$(dirname "$0")"
source ./homeai_env.sh
homeai_source_config

PYTHON_BIN="$HOME/.local/share/HomeAIAgent/venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[FORCE-INFO-FAIL] Python venv not found: $PYTHON_BIN"
  exit 3
fi

exec "$PYTHON_BIN" ./force_info_refresh.py
