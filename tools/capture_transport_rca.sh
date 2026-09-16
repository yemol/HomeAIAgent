#!/bin/zsh
set -u

SPEAKER_IP="${1:-192.168.71.250}"
PORT="${2:-8765}"
STAMP="$(date '+%Y%m%d-%H%M%S')"
OUT="${HOME}/Desktop/HomeAIAgent_Transport_RCA_${STAMP}"
mkdir -p "$OUT"

IFACE="$(route get "$SPEAKER_IP" 2>/dev/null | awk '/interface:/{print $2; exit}')"
if [[ -z "${IFACE}" ]]; then
  echo "[RCA] cannot resolve interface for ${SPEAKER_IP}"
  exit 2
fi

echo "[RCA] output=$OUT"
echo "[RCA] speaker=$SPEAKER_IP port=$PORT interface=$IFACE"
route get "$SPEAKER_IP" > "$OUT/route.txt" 2>&1 || true
arp -an > "$OUT/arp_initial.txt" 2>&1 || true
netstat -rn > "$OUT/routes_initial.txt" 2>&1 || true
ifconfig "$IFACE" > "$OUT/interface_initial.txt" 2>&1 || true

cleanup() {
  echo "\n[RCA] stopping capture..."
  [[ -n "${ARP_PID:-}" ]] && kill "$ARP_PID" 2>/dev/null || true
  [[ -n "${PING_PID:-}" ]] && kill "$PING_PID" 2>/dev/null || true
  [[ -n "${NET_PID:-}" ]] && kill "$NET_PID" 2>/dev/null || true
  [[ -n "${TCPDUMP_PID:-}" ]] && sudo kill "$TCPDUMP_PID" 2>/dev/null || true
  wait 2>/dev/null || true
  echo "[RCA] saved: $OUT"
}
trap cleanup INT TERM EXIT

(
  while true; do
    echo "=== $(date '+%F %T') ==="
    arp -an | grep "(${SPEAKER_IP})" || true
    sleep 5
  done
) > "$OUT/arp_watch.log" 2>&1 &
ARP_PID=$!

(
  while true; do
    echo "=== $(date '+%F %T') ==="
    ping -c 1 -W 1000 "$SPEAKER_IP" || true
    sleep 4
  done
) > "$OUT/ping_watch.log" 2>&1 &
PING_PID=$!

(
  while true; do
    echo "=== $(date '+%F %T') ==="
    netstat -ib -I "$IFACE" 2>/dev/null | head -n 6 || true
    sleep 10
  done
) > "$OUT/interface_watch.log" 2>&1 &
NET_PID=$!

echo "[RCA] sudo is required only for tcpdump."
sudo -v
sudo tcpdump -i "$IFACE" -nn -s 0 "host ${SPEAKER_IP} and tcp port ${PORT}" \
  -w "$OUT/networkspeaker_8765.pcap" > "$OUT/tcpdump.log" 2>&1 &
TCPDUMP_PID=$!

echo "[RCA] capture running. Keep this terminal open overnight."
echo "[RCA] in another terminal run Gateway with:"
echo "      ./run_full.sh 2>&1 | tee '$OUT/gateway.log'"
echo "[RCA] and keep NetworkSpeaker serial monitor logging to:"
echo "      $OUT/networkspeaker_serial.log"
echo "[RCA] Ctrl+C here to stop capture."

while true; do sleep 3600; done
