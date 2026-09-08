# HomeAIAgent Gateway

The Gateway is the local service layer between StickS3 and OpenClaw.

## Responsibilities

- persistent StickS3 WebSocket connection
- Volcengine ASR/TTS by default
- OpenClaw conversational requests
- segmented/gapless TTS delivery
- game/finance Info Skill refresh
- Glass2 frame rendering and sync
- OpenClaw transient Info-session cleanup
- night display scheduling and acknowledgement
- diagnostic runtime captures

## Persistent configuration

Canonical configuration:

```text
~/.config/HomeAIAgent/gateway.env
```

Initial setup:

```bash
./setup_mac.sh
```

The script preserves existing credentials and prepares the persistent Python runtime.

## Daily startup

A1R11 requires only one service command on the Mac mini:

```bash
./run_full.sh
```

The Python process owns the SSH transport, startup preflight, retries, device WebSocket, speech, notifications, Info and Gold. Do not start `openclaw_air_tunnel.sh` during normal operation.

## Voice flow

```text
StickS3 PCM16/16k
  -> ASR
  -> OpenClaw
  -> TTS
  -> device-safe PCM segments
  -> StickS3 two-slot playback queue
```

The Gateway waits for `playback.slot_ready` to keep the next segment queued before the current segment ends. The final turn completes on `playback.done`.

## Info Skill

Game and finance are independent refresh categories with independent last-good fallback. Background OpenClaw requests:

- omit the normal conversational `user` field;
- use one unique `x-openclaw-session-key` per request;
- delete that exact transient session through OpenClaw Gateway WebSocket RPC after the request;
- never use the voice conversation session for background Info refreshes.

Diagnostic Info refresh snapshots are stored under the HomeAIAgent data directory and are bounded by the configured retention count.

## Generated debug captures

The live Gateway may generate:

```text
latest_input.wav
latest_input_normal.wav
latest_transcript.txt
latest_answer.txt
latest_tts.wav
```

These files are runtime diagnostics and are intentionally ignored by git. They are useful when isolating ASR, agent or TTS problems.

## Configuration templates

- `.env.example`: local example/migration source
- `gateway.env.example`: persistent configuration template

Do not commit real tokens or API keys.
