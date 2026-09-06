#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIBROOT="$ROOT/.pio-local/framework-arduinoespressif32/libraries"

echo "=== HomeAIAgent A4.4.8 global Arduino CPPPATH check ==="

checks=(
  "Network/src/Network.h"
  "WiFi/src/WiFi.h"
  "NetworkClientSecure/src/NetworkClientSecure.h"
  "NetworkClientSecure/src/WiFiClientSecure.h"
  "Preferences/src/Preferences.h"
  "Wire/src/Wire.h"
  "SPI/src/SPI.h"
)

for rel in "${checks[@]}"; do
  if [[ -f "$LIBROOT/$rel" ]]; then
    echo "[OK] $rel"
  else
    echo "[FAIL] missing $LIBROOT/$rel"
    exit 2
  fi
done

echo
grep -q 'pre:scripts/expose_local_arduino_libs.py' "$ROOT/platformio.ini" \
  && echo "[OK] PRE CPPPATH script enabled" \
  || { echo "[FAIL] PRE CPPPATH script missing"; exit 3; }

grep -q 'NetworkClientSecure=symlink://' "$ROOT/platformio.ini" \
  && echo "[OK] NetworkClientSecure explicit dependency enabled" \
  || { echo "[FAIL] NetworkClientSecure dependency missing"; exit 4; }

echo
echo "[READY] Local Arduino sibling-library headers are globally exposed."
