# Architecture

## Components

### StickS3 terminal

Responsibilities:

- local Chinese wake-word detection (`逐光逐光`)
- Button-A PTT and hands-free capture
- ES8311 half-duplex microphone/speaker control
- PSRAM capture buffering
- two-slot gapless TTS playback
- cyber-expression state rendering
- Glass2 frame cache/display
- persistent NVS network/Gateway configuration
- night display execution and acknowledgement

### HomeAIAgent Server

Responsibilities:

- persistent WebSocket session with StickS3
- in-process managed SSH/Tailscale transport to remote OpenClaw while developing on the Mac mini
- ASR/TTS service integration
- OpenClaw conversational request path
- game/finance Info Skill refresh and cache
- OpenClaw transient Info-session cleanup
- Glass2 frame rendering
- wall-clock display policy
- diagnostics and runtime captures

### OpenClaw

OpenClaw remains the agent/tool authority. Normal voice conversations retain one conversational identity; background Info Skill requests use isolated transient sessions that are deleted through the OpenClaw Gateway control-plane RPC after use.

## Voice data flow

```text
Wake/Button A
  -> StickS3 Mic (PCM16/16 kHz)
  -> HomeAIAgent Gateway
  -> ASR
  -> OpenClaw
  -> TTS
  -> segmented PCM
  -> StickS3 two-slot speaker queue
```

The StickS3 audio path is intentionally half-duplex. Microphone capture ends before RF-heavy upload/processing and speaker playback.

## Display data flow

### StickS3 LCD

The local LCD renders the HomeAI cyber expression from the assistant state. A full 135×240 RGB565 frame is composed off-screen and pushed once to avoid flicker.

### Glass2

The Gateway pre-renders each 128×64 monochrome information frame. StickS3 receives a complete revision using a two-phase sync and does not replace the active feed until the incoming set is complete.

## Reliability principles

- do not modify audio-critical scheduling solely for UI effects;
- keep application WebSocket traffic outside the active Mic/I2S capture window;
- preserve per-category last-good information data;
- require explicit display-state acknowledgement;
- bound background OpenClaw session lifetime;
- keep secrets and machine-local dependencies out of source control.


## A1R11 managed OpenClaw transport

During development HomeAIAgent remains on the Mac mini. The Python service owns the outbound SSH connection and local forward to the OpenClaw host using AsyncSSH. There is no separately launched `ssh -N -L` service.

```text
StickS3 -> Mac mini HomeAIAgent Server :8765
                         |
                         +-> embedded AsyncSSH
                               -> OpenClaw host 127.0.0.1:18789
```

If the SSH path drops, only OpenClaw-dependent operations are temporarily unavailable. The HomeAIAgent process and StickS3-facing WebSocket stay online while the transport reconnects. A future same-host deployment can set `OPENCLAW_TRANSPORT=direct`.
