#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[CLEAN] HomeAIAgent submission artifacts"
echo "[CLEAN] root=$ROOT"
echo "[CLEAN] persistent runtime outside the project is NOT touched"
echo "[CLEAN] .pio-local is NOT touched"

# Generated build/cache directories. Never delete .pio-local.
rm -rf -- .pio __pycache__ .pytest_cache gateway/__pycache__ tools/__pycache__ scripts/__pycache__

# Project-local debug captures and accidental runtime-state copies only.
find . -type f \
  ! -path './.git/*' \
  ! -path './.pio-local/*' \
  \( \
    -name '*.log' -o \
    -name '*.wav' -o \
    -name '*.pcm' -o \
    -name '*.tmp' -o \
    -name '*.bak' -o \
    -name '*.orig' -o \
    -name '*.trace' -o \
    -name '*.pid' -o \
    -name '.DS_Store' -o \
    -name 'latest_transcript.txt' -o \
    -name 'latest_answer.txt' -o \
    -name 'notification_queue.json' -o \
    -name 'openclaw_voice_listener_state.json' -o \
    -name 'gold_quote_cache.json' -o \
    -name 'info_skill_feed_cache.json' -o \
    -name 'subscriptions.json' \
  \) -print -exec rm -f -- {} +

# Remove accidentally copied project-local snapshot directories, but never the real
# ~/.local/share/HomeAIAgent runtime state.
while IFS= read -r -d '' d; do
  echo "$d"
  rm -rf -- "$d"
done < <(find . -type d \
  ! -path './.git/*' \
  ! -path './.pio-local/*' \
  -name 'info_skill_startup_snapshots' -print0)

echo "[CLEAN] complete"
