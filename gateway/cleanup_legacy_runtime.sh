#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"
source ./homeai_runtime.sh

if ! homeai_runtime_exists; then
  echo "[FAIL] Persistent runtime is not ready:"
  echo "       $HOMEAI_VENV"
  echo "Run ./setup_mac.sh first."
  exit 2
fi

if [ ! -d ".venv" ]; then
  echo "[OK] No legacy project-local .venv exists."
  exit 0
fi

echo "Persistent runtime is ready:"
echo "  $HOMEAI_VENV"
echo
read "ANSWER?Delete legacy project-local .venv now? Type DELETE: "
if [ "$ANSWER" != "DELETE" ]; then
  echo "Cancelled."
  exit 0
fi

rm -rf .venv
echo "[OK] Deleted project-local .venv."
