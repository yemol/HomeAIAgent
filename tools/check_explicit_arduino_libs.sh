#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIBROOT="$ROOT/.pio-local/framework-arduinoespressif32/libraries"

echo "=== HomeAIAgent explicit Arduino library check ==="

checks=(
  "Network/library.properties"
  "Network/src/Network.h"
  "WiFi/library.properties"
  "WiFi/src/WiFi.h"
  "Preferences/library.properties"
  "Preferences/src/Preferences.h"
  "Wire/library.properties"
  "Wire/src/Wire.h"
  "SPI/library.properties"
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
if grep -q '^lib_extra_dirs[[:space:]]*=' "$ROOT/platformio.ini"; then
  echo "[FAIL] deprecated lib_extra_dirs directive still present"
  exit 3
else
  echo "[OK] deprecated lib_extra_dirs directive removed"
fi

echo
echo "[OK] explicit symlink dependencies:"
grep -E '^[[:space:]]+(Networking|WiFi|Preferences|Wire|SPI)=symlink://' "$ROOT/platformio.ini"

echo
echo "[READY] Arduino built-in libraries are explicit project dependencies."
