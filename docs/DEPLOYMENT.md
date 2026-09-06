# Deployment

This document describes the current A4.5 Cyber Expression A2 deployment path.

## 1. StickS3 firmware

### Production PlatformIO environment

```text
m5stack-sticks3-wake
```

The production build uses project-local Arduino/ESP-SR dependencies under `.pio-local/`. Those machine-local packages are deliberately not committed.

Useful preparation/diagnostic helpers are available under `tools/` and `scripts/`.

### Device configuration

Real Wi-Fi and Gateway settings are stored in StickS3 NVS. `include/secrets.h` is optional and ignored by git. If no NVS configuration exists, configure the terminal with:

```bash
python tools/configure_terminal.py
```

or use the serial commands:

```text
CFG SHOW
CFG {json}
CFG RESET
```

### Build / upload / monitor

Use PlatformIO:

1. Clean
2. Upload
3. Monitor

Expected boot banner:

```text
=== HomeAIAgent A4.5 Cyber Expression A2 Flicker-Free / base A4.4.18 RC1R9 ===
```

Expected renderer line:

```text
[UI] Cyber canvas ready: 135x240 RGB565, 64800 bytes
```

`srmodels.bin` and `esp_sr_8.csv` are part of the production wake-word/ESP-SR flash flow and are included in the repository.

## 2. Gateway setup

Enter the Gateway directory:

```bash
cd gateway
./setup_mac.sh
```

The setup script creates/retains persistent configuration at:

```text
~/.config/HomeAIAgent/gateway.env
```

It also prepares the persistent Python runtime used by the Gateway.

### Daily start

When OpenClaw is reached through the supplied Air tunnel:

```bash
# Terminal 1
cd gateway
./openclaw_air_tunnel.sh
```

```bash
# Terminal 2
cd gateway
./run_full.sh
```

`run_full.sh` performs a preflight before starting `companion_gateway.py`.

## 3. Expected live behavior

A normal hands-free turn is:

```text
local wake -> Listening -> capture -> Thinking -> ASR/OpenClaw/TTS -> Speaking -> Idle
```

Button A uses the same downstream path but reports `trigger=button_a` rather than `wake_word`.

### Capture timing

- wait for speech after acknowledgement: 3.5 s
- continuous silence to finish: 3 s
- maximum utterance: 10 s
- pre-roll: 300 ms
- device capture buffer: 11 s

### TTS

Long TTS is divided into device-safe segments. StickS3 keeps two playback slots so the next segment can be queued before the current segment ends. Final `playback.done` is sent only after the final segment has completed.

## 4. Information feed

The Gateway maintains separate game/finance last-good state and sends up to 20 pre-rendered Glass2 frames to the terminal.

Scheduled refresh hours are:

```text
01:00, 09:00, 11:00, 13:00, 15:00, 17:00, 19:00, 21:00, 23:00
```

The Gateway also saves bounded diagnostic refresh snapshots under its HomeAIAgent data directory.

## 5. Night display policy

The Gateway is the wall-clock authority:

- sleep window begins: 01:05
- normal wake: 09:00
- manual button activity can temporarily wake the displays
- device reports `display.ack` only when the requested state is actually applied or pending

Audio, Wi-Fi and Gateway connectivity remain active while displays sleep.
