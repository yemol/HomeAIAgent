#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."

echo "=== HomeAIAgent A4.4.2 PlatformIO source check ==="
echo
grep -E '^default_envs' platformio.ini || true
echo
echo "Pinned platform:"
grep -A4 '^\[env:m5stack-sticks3-wake\]' platformio.ini | grep '^platform = ' || true
echo
if grep -q 'sourceforge' platformio.ini; then
  echo "[FAIL] SourceForge override still present"
  exit 2
else
  echo "[OK] no SourceForge framework override"
fi
echo
echo "[INFO] pioarduino 55.03.37 resolves its own Arduino 3.3.7 framework packages."
