#!/bin/zsh
set -euo pipefail

export HOMEAI_DATA_DIR="${HOMEAI_DATA_DIR:-$HOME/.local/share/HomeAIAgent}"
export HOMEAI_VENV="${HOMEAI_VENV:-$HOMEAI_DATA_DIR/venv}"
export HOMEAI_REQUIREMENTS_STAMP="${HOMEAI_REQUIREMENTS_STAMP:-$HOMEAI_DATA_DIR/requirements.sha256}"

homeai_runtime_exists() {
  [ -x "$HOMEAI_VENV/bin/python" ] && [ -f "$HOMEAI_VENV/bin/activate" ]
}

homeai_requirements_hash() {
  local requirements="${1:-./requirements.txt}"
  if [ ! -f "$requirements" ]; then
    echo "none"
    return
  fi
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$requirements" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$requirements" | awk '{print $1}'
  else
    python3 - "$requirements" <<'PY'
import hashlib, sys
print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())
PY
  fi
}

homeai_sync_dependencies() {
  local requirements="${1:-./requirements.txt}"
  [ -f "$requirements" ] || return 0
  mkdir -p "$HOMEAI_DATA_DIR"

  local new_hash
  new_hash="$(homeai_requirements_hash "$requirements")"
  local old_hash=""
  [ -f "$HOMEAI_REQUIREMENTS_STAMP" ] && old_hash="$(cat "$HOMEAI_REQUIREMENTS_STAMP")"

  if [ "$new_hash" != "$old_hash" ]; then
    echo "[RUNTIME] requirements changed; syncing persistent environment..."
    python -m pip install -q -r "$requirements"
    printf "%s" "$new_hash" > "$HOMEAI_REQUIREMENTS_STAMP"
    echo "[RUNTIME] dependencies are current."
  fi
}

homeai_bootstrap_runtime() {
  local requirements="${1:-./requirements.txt}"
  if ! homeai_runtime_exists; then
    echo "[RUNTIME] Creating persistent Python environment:"
    echo "          $HOMEAI_VENV"
    mkdir -p "$HOMEAI_DATA_DIR"
    python3 -m venv "$HOMEAI_VENV"
    source "$HOMEAI_VENV/bin/activate"
    python -m pip install -q --upgrade pip
  else
    echo "[RUNTIME] Reusing persistent Python environment:"
    echo "          $HOMEAI_VENV"
    source "$HOMEAI_VENV/bin/activate"
  fi
  homeai_sync_dependencies "$requirements"
}

homeai_require_runtime() {
  if ! homeai_runtime_exists; then
    homeai_bootstrap_runtime "${1:-./requirements.txt}"
  fi
}

homeai_activate_runtime() {
  local requirements="${1:-./requirements.txt}"
  homeai_require_runtime "$requirements"
  source "$HOMEAI_VENV/bin/activate"
  homeai_sync_dependencies "$requirements"
}
