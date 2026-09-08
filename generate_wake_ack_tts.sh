#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")/gateway"
exec ./generate_wake_ack_tts.sh
