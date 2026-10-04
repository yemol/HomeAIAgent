#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$HOME/.local/share/HomeAIAgent/venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3 || true)"
fi
if [[ -z "${PY:-}" ]]; then
  echo "ERROR: python3 not found" >&2
  exit 2
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ERROR: ffmpeg not found. On macOS run: brew install ffmpeg" >&2
  exit 2
fi
exec "$PY" "$ROOT/tools/fetch_ambient_assets.py" "$@"
