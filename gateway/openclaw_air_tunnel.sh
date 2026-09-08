#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"

if [ "${HOMEAI_ALLOW_LEGACY_TUNNEL:-0}" != "1" ]; then
  echo "[DEPRECATED] A1R11 manages the OpenClaw SSH transport inside ./run_full.sh."
  echo "Start only: ./run_full.sh"
  echo "For emergency manual fallback only: HOMEAI_ALLOW_LEGACY_TUNNEL=1 ./openclaw_air_tunnel.sh"
  exit 2
fi
source ./homeai_env.sh
homeai_migrate_legacy_env "./.env"
homeai_source_config

AIR_USER="${OPENCLAW_SSH_USER:-yuanxiang}"
AIR_TAILSCALE_IP="${OPENCLAW_SSH_HOST:-100.105.66.46}"
LOCAL_PORT="${OPENCLAW_LOCAL_PORT:-18790}"
REMOTE_PORT="${OPENCLAW_REMOTE_PORT:-18789}"

echo
echo "========================================================"
echo " HomeAIAgent | Persistent OpenClaw SSH tunnel"
echo "========================================================"
echo " Config: $HOMEAI_CONFIG_FILE"
echo
echo " Mini 127.0.0.1:${LOCAL_PORT}"
echo "   -> SSH/Tailscale -> ${AIR_USER}@${AIR_TAILSCALE_IP}"
echo "   -> Air 127.0.0.1:${REMOTE_PORT}"
echo

if command -v tailscale >/dev/null 2>&1; then
  tailscale ping -c 1 "${AIR_TAILSCALE_IP}" || {
    echo "[FAIL] Cannot reach Air over Tailscale."
    exit 2
  }
fi

if nc -z 127.0.0.1 "${LOCAL_PORT}" >/dev/null 2>&1; then
  echo "[OK] Local port ${LOCAL_PORT} is already open."
  exit 0
fi

exec ssh \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes \
  -N \
  -L "${LOCAL_PORT}:127.0.0.1:${REMOTE_PORT}" \
  "${AIR_USER}@${AIR_TAILSCALE_IP}"
