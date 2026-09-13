#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="${1:-/tmp/homeai-pio}"
LOCAL_DIR="$PROJECT_ROOT/.pio-local"

CORE_ARCHIVE="$SRC_DIR/esp32-core-3.3.7.tar.xz"
LIBS_ARCHIVE="$SRC_DIR/esp32-core-3.3.7-libs.tar.xz"

echo "=== HomeAIAgent local Arduino 3.3.7 bootstrap ==="
echo "Project: $PROJECT_ROOT"
echo "Source : $SRC_DIR"
echo

for f in "$CORE_ARCHIVE" "$LIBS_ARCHIVE"; do
  if [[ ! -f "$f" ]]; then
    echo "[FAIL] missing archive: $f"
    exit 2
  fi
done

echo "[1/5] Verify xz archives..."
xz -t "$CORE_ARCHIVE"
xz -t "$LIBS_ARCHIVE"
echo "[OK] both archives passed xz integrity check"

TMP_ROOT="$(mktemp -d /tmp/homeai-arduino337.XXXXXX)"
trap 'rm -rf "$TMP_ROOT"' EXIT

extract_and_normalize() {
  local archive="$1"
  local target="$2"
  local label="$3"
  local temp="$TMP_ROOT/$label"

  mkdir -p "$temp"
  echo "[2/5] Extract $label..."
  tar -xJf "$archive" -C "$temp"

  local manifest
  manifest="$(find "$temp" -maxdepth 4 -type f -name package.json -print -quit)"
  if [[ -z "$manifest" ]]; then
    echo "[FAIL] package.json not found after extracting $archive"
    exit 3
  fi

  local root
  root="$(dirname "$manifest")"

  rm -rf "$target"
  mkdir -p "$target"
  cp -R "$root"/. "$target"/

  if [[ ! -f "$target/package.json" ]]; then
    echo "[FAIL] normalized package missing package.json: $target"
    exit 4
  fi

  echo "[OK] $label -> $target"
  grep -E '"(name|version)"[[:space:]]*:' "$target/package.json" | head -n 4 || true
}

mkdir -p "$LOCAL_DIR"

extract_and_normalize \
  "$CORE_ARCHIVE" \
  "$LOCAL_DIR/framework-arduinoespressif32" \
  "core"

extract_and_normalize \
  "$LIBS_ARCHIVE" \
  "$LOCAL_DIR/framework-arduinoespressif32-libs" \
  "libs"

echo
echo "[3/5] Local framework folders:"
du -sh \
  "$LOCAL_DIR/framework-arduinoespressif32" \
  "$LOCAL_DIR/framework-arduinoespressif32-libs"

echo
echo "[4/5] Confirm PlatformIO override:"
grep -A4 '^platform_packages' "$PROJECT_ROOT/platformio.ini"

echo
echo "[5/5] Ready."
echo "Now run:"
echo
echo "  cd \"$PROJECT_ROOT\""
echo "  ~/.platformio/penv/bin/platformio run -e m5stack-sticks3-wake -t upload"
echo
echo "Do NOT erase flash."
