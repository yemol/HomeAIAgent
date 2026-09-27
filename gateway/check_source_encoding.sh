#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PY="$HOME/.local/share/HomeAIAgent/venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3 || command -v python || true)"
fi
if [[ -z "$PY" ]]; then
  echo "[SOURCE-ENCODING] no Python interpreter found"
  exit 2
fi

"$PY" - <<'PY'
from pathlib import Path
p = Path('companion_gateway.py')
data = p.read_bytes()
data.decode('utf-8')
print('[SOURCE-ENCODING] UTF-8 OK:', p.resolve())
PY

PYTHONDONTWRITEBYTECODE=1 "$PY" -m py_compile companion_gateway.py
find . -type d -name '__pycache__' -prune -exec rm -rf {} +
find . -type f -name '*.pyc' -delete

echo '[SOURCE-SYNTAX] py_compile OK'
