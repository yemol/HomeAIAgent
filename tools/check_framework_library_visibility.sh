#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIBROOT="$ROOT/.pio-local/framework-arduinoespressif32/libraries"

echo "=== HomeAIAgent framework-library visibility check ==="

[[ -d "$LIBROOT" ]] || {
  echo "[FAIL] missing framework libraries directory:"
  echo "       $LIBROOT"
  exit 2
}

required=(
  "WiFi/src/WiFi.h"
  "Preferences/src/Preferences.h"
  "Wire/src/Wire.h"
  "SPI/src/SPI.h"
)

for rel in "${required[@]}"; do
  if [[ -f "$LIBROOT/$rel" ]]; then
    echo "[OK] $rel"
  else
    echo "[FAIL] missing $rel"
    exit 3
  fi
done

echo
echo "[OK] platformio.ini library storage:"
grep -A2 '^lib_extra_dirs' "$ROOT/platformio.ini" || true

echo
echo "[READY] built-in Arduino libraries are present and exposed to LDF."
