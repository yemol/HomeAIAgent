#!/bin/zsh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
for d in \
  "$ROOT/.pio-local/framework-arduinoespressif32" \
  "$ROOT/.pio-local/framework-arduinoespressif32-libs"
do
  [[ -f "$d/package.json" ]] || {
    echo "[FAIL] missing $d/package.json"
    exit 2
  }
  echo "[OK] $d"
  grep -E '"(name|version)"[[:space:]]*:' "$d/package.json" | head -n 4 || true
done
echo "[OK] local Arduino 3.3.7 framework payloads are ready"
