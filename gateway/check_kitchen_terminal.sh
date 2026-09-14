#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"
PORT="${GATEWAY_PORT:-8765}"

echo "=== KitchenTerminal check ==="
echo "[1/2] HTTP health"
curl -fsS "http://127.0.0.1:${PORT}/kitchen/health"
echo
echo "[2/2] Gateway control status"
python kitchen_send.py --port "$PORT" status
