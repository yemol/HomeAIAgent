# HomeAIAgent

HomeAIAgent is a desk-side AI terminal built around an M5Stack StickS3 and Glass2 display. The StickS3 handles local wake-word detection, voice interaction, the cyber-expression UI and device state; the local Gateway handles speech services, OpenClaw integration, information feeds and display scheduling.

## Current baseline

**Release:** A4.5 Cyber Expression A2 / Flicker-Free  
**Date:** 2026-09-06

Validated integration baseline:

- StickS3 voice/state core: A4.4.18 RC1R9
- Gateway: A4.4.18 RC1R13
- Cyber expression renderer: A4.5 A2
- Gapless segmented TTS: A3.9
- Glass2 information feed: 10 game + 10 finance items
- Local wake phrase: `逐光逐光`

The A2 main-screen renderer was verified on real hardware with the full-screen flicker removed.

## What is included

- Local Chinese wake-word detection on StickS3
- Button-A PTT and hands-free wake flow
- 16 kHz mono PCM microphone capture
- Volcengine ASR/TTS default Gateway path
- OpenClaw agent/tool integration
- Two-slot gapless segmented TTS playback
- Flicker-free cyber expression UI with six states
- Glass2 pre-rendered 128×64 information frames
- Independent game/finance refresh and last-good fallback
- OpenClaw transient Info-session cleanup through Gateway WebSocket RPC
- Night display policy with device acknowledgement
- Persistent StickS3 configuration in NVS
- Persistent Gateway configuration under `~/.config/HomeAIAgent/`

## Cyber expression states

| State | Visual behavior |
| --- | --- |
| Idle | restrained breathing core and stable VISOR |
| Listening | inward signal packets and receiver brackets |
| Thinking | counter-rotating calculation orbits and data nodes |
| Speaking | outward directional waveform clusters |
| Success | mechanical orbit alignment |
| Error | restrained red misalignment and glitch geometry |

Rendering uses one 135×240 RGB565 `M5Canvas`, preferably in PSRAM. A complete frame is drawn off-screen and transferred to the LCD once, preventing visible clear/redraw flicker.

## Repository layout

```text
src/                 StickS3 firmware
include/             firmware configuration and Glass2 font assets
gateway/             local HomeAIAgent Gateway
scripts/             PlatformIO/ESP-SR preparation helpers
tools/               setup and environment diagnostics
docs/                current technical documentation
docs/history/        historical release/acceptance records
srmodels.bin         wake-word model partition image
esp_sr_8.csv         ESP-SR partition layout
platformio.ini       PlatformIO environments
```

`srmodels.bin` is intentionally included because the production upload flow references it through the ESP-SR preparation/flash scripts. Machine-local PlatformIO toolchains and secrets are not included.

## Quick start

### StickS3 firmware

The production environment is `m5stack-sticks3-wake`.

1. Keep the existing development-machine `.pio-local/` dependencies in place, or prepare the equivalent local dependencies with the supplied tools.
2. Configure the device with `tools/configure_terminal.py` or the serial `CFG` command if it is not already provisioned in NVS.
3. In PlatformIO: **Clean → Upload → Monitor**.

Expected boot banner:

```text
=== HomeAIAgent A4.5 Cyber Expression A2 Flicker-Free / base A4.4.18 RC1R9 ===
```

Expected renderer initialization:

```text
[UI] Cyber canvas ready: 135x240 RGB565, 64800 bytes
```

### Gateway

From `gateway/`:

```bash
./setup_mac.sh
```

Daily start when OpenClaw is reached through the supplied tunnel:

```bash
# Terminal 1
./openclaw_air_tunnel.sh

# Terminal 2
./run_full.sh
```

Gateway configuration is persisted at:

```text
~/.config/HomeAIAgent/gateway.env
```

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) and [gateway/README.md](gateway/README.md) for the complete deployment flow.

## Important runtime rules

- StickS3 ES8311 microphone and speaker are treated as half-duplex.
- Application WebSocket traffic is not serviced during the critical Mic/I2S capture window.
- Hands-free capture waits up to 3.5 s for speech, ends after 3 s of continuous silence and has a 10 s maximum utterance duration.
- The capture buffer reserves 11 s in PSRAM for queue-drain headroom.
- Listening/Speaking UI refresh is deliberately capped below Thinking refresh to protect the audio path.
- Glass2 fonts/layout and the A3.9 gapless TTS pipeline are frozen in this baseline.

## Documentation

- [Deployment](docs/DEPLOYMENT.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Hardware](docs/HARDWARE.md)
- [Companion protocol](docs/PROTOCOL.md)
- [Cyber expression UI](docs/CYBER_EXPRESSION.md)
- [Info Skill interface](docs/INFO_SKILL_INTERFACE.md)
- [Changelog](CHANGELOG.md)
- [Current release notes](RELEASE_NOTES.md)

Historical stage notes are retained under `docs/history/` and are not authoritative for the current baseline.

## Submission hygiene

The repository intentionally excludes:

- `.pio/`
- `.pio-local/`
- local secrets (`include/secrets.h`)
- Gateway `.env` and virtual environments
- runtime audio/text captures (`gateway/latest_*`)
- logs and caches

The checked-in `include/secrets.example.h` contains compile-time placeholders only. Provisioned devices read their real connection settings from NVS.
