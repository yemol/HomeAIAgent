#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"
python - <<'PY'
from pathlib import Path
p = Path('companion_gateway.py')
data = p.read_bytes()
data.decode('utf-8')
print('[SOURCE-ENCODING] UTF-8 OK:', p.resolve())
PY
python -m py_compile companion_gateway.py
echo '[SOURCE-SYNTAX] py_compile OK'
