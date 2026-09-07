#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="${1:-/tmp/homeai-pio}"
LOCAL_DIR="$PROJECT_ROOT/.pio-local"
TARGET="$LOCAL_DIR/ESP-SR-For-M5Unified"

ZIP_FILE="$SRC_DIR/ESP-SR-For-M5Unified-95903511.zip"
ZIP_FALLBACK="$SRC_DIR/ESP-SR-For-M5Unified.zip"
DIR_FALLBACK="$SRC_DIR/ESP-SR-For-M5Unified"

echo "=== HomeAIAgent A4.4.4 local ESP-SR bootstrap ==="
echo "Project: $PROJECT_ROOT"
echo "Source : $SRC_DIR"
echo

rm -rf "$TARGET"
mkdir -p "$LOCAL_DIR"

normalize_from_dir() {
  local src="$1"
  if [[ ! -f "$src/library.json" && ! -f "$src/library.properties" ]]; then
    echo "[FAIL] ESP-SR source folder does not contain library.json/library.properties:"
    echo "       $src"
    exit 3
  fi
  mkdir -p "$TARGET"
  cp -R "$src"/. "$TARGET"/
}

if [[ -d "$DIR_FALLBACK" ]]; then
  echo "[1/3] Import local ESP-SR directory..."
  normalize_from_dir "$DIR_FALLBACK"
elif [[ -f "$ZIP_FILE" || -f "$ZIP_FALLBACK" ]]; then
  ARCHIVE="$ZIP_FILE"
  [[ -f "$ARCHIVE" ]] || ARCHIVE="$ZIP_FALLBACK"

  echo "[1/3] Verify ESP-SR ZIP..."
  unzip -tq "$ARCHIVE" >/dev/null
  echo "[OK] ZIP integrity"

  TMP="$(mktemp -d /tmp/homeai-espsr.XXXXXX)"
  trap 'rm -rf "$TMP"' EXIT

  echo "[2/3] Extract ESP-SR..."
  unzip -q "$ARCHIVE" -d "$TMP"

  ROOT="$(find "$TMP" -maxdepth 3 -type f \( -name library.json -o -name library.properties \) -print -quit | xargs dirname)"
  if [[ -z "$ROOT" || ! -d "$ROOT" ]]; then
    echo "[FAIL] Could not locate ESP-SR library root after extraction"
    exit 4
  fi

  normalize_from_dir "$ROOT"
else
  echo "[FAIL] ESP-SR source not found."
  echo
  echo "Expected one of:"
  echo "  $ZIP_FILE"
  echo "  $ZIP_FALLBACK"
  echo "  $DIR_FALLBACK"
  echo
  echo "Download the pinned snapshot first:"
  echo
  echo "curl -4 --http1.1 -L --fail --retry 5 --retry-all-errors \\"
  echo "  --connect-timeout 15 --speed-time 20 --speed-limit 1024 \\"
  echo "  -o \"$ZIP_FILE\" \\"
  echo "  https://codeload.github.com/74th/ESP-SR-For-M5Unified/zip/95903511e4c011b778a4469ffe05be58ea2350b1"
  exit 2
fi

echo
echo "[3/3] Verify imported library..."
if [[ -f "$TARGET/library.json" ]]; then
  echo "[OK] library.json"
  grep -E '"(name|version)"[[:space:]]*:' "$TARGET/library.json" | head -n 4 || true
elif [[ -f "$TARGET/library.properties" ]]; then
  echo "[OK] library.properties"
  grep -E '^(name|version)=' "$TARGET/library.properties" | head -n 4 || true
else
  echo "[FAIL] imported library metadata missing"
  exit 5
fi

echo
du -sh "$TARGET"
echo
echo "[READY] ESP-SR is now project-local."
echo
echo "Run:"
echo "  cd \"$PROJECT_ROOT\""
echo "  ~/.platformio/penv/bin/platformio run -e m5stack-sticks3-wake -t upload"
