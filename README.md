## A1R21 Gateway Voice-Turn Ordering Fix

This release keeps the A1R20 Submission Clean firmware and frozen local TTS wake ACK unchanged, and fixes one Gateway-side ordering defect: OpenClaw progress messages emitted during a synchronous voice turn are no longer replayed afterward as asynchronous notifications.

No StickS3 flash is required for this release. Replace the project/Gateway files and restart `gateway/run_full.sh`.

## A1R20 Submission Clean — Frozen local TTS wake ACK

This submission freezes the user-approved local TTS wake acknowledgement `assets/wake_ack_zaide_tts.wav` and its matching firmware array `include/wake_ack_voice_pcm.h`. **No TTS generation step is required before compiling.** Runtime wake playback remains fully local and consumes no cloud TTS request.

Normal device workflow:

```bash
pio run -e m5stack-sticks3-wake -t clean
pio run -e m5stack-sticks3-wake -t upload
pio device monitor
```

Do not Erase Flash. `generate_wake_ack_tts.sh` is retained only as an optional maintenance tool if the approved wake voice is intentionally replaced in a future version. Ordinary builds should not run it.

Current approved wake asset:

- Text: `在的。`
- Voice: `zh_female_vv_uranus_bigtts`
- Resource: `seed-tts-2.0`
- 16 kHz / mono / PCM16
- 9360 samples / 585 ms
- Wake ACK playback: dedicated MAG6
- Normal assistant TTS playback: MAG5

> A1R18 audio note: local wake acknowledgement `在的` uses the full clean ~632 ms waveform at dedicated MAG6. Normal assistant TTS remains MAG5. A1R17 hard compression/tail trimming has been removed.
# A1R15 wake acknowledgement

A successful local wake (`逐光逐光`) now answers immediately with an embedded local voice prompt, `在的`, before reopening the command microphone. The prompt is stored in StickS3 firmware and does not use Gateway/OpenClaw/cloud TTS or tokens. If local PCM playback fails, firmware emits a short fallback tone instead of leaving the wake silent.

A1R15 is a StickS3 firmware change and requires **Clean -> Upload -> Monitor**. Do not Erase Flash. All A1R14 Gateway behavior, embedded SSH transport, A1R12 cache-only Info startup, 15 s Info hold, standard Volcengine TTS voice, and frozen wake sensitivity settings remain unchanged.

# A1R14 build note

This package fixes the A1R13 PlatformIO package-resolution error. The Mac mini already downloaded and installed `toolchain-xtensa-esp-elf 14.2.0+20251107` and `tool-esptoolpy 5.1.2` during the preceding A1R12 upload attempt. A1R14 deliberately does not override those tools through the registry; PlatformIO should reuse the installed global package cache.

## A1R13 PlatformIO tool pin

The production wake environment keeps the frozen local pioarduino platform and now explicitly pins the two tool packages already installed on the Mac mini: `toolchain-xtensa-esp-elf@14.2.0+20251107` and `tool-esptoolpy@5.1.2`. This avoids re-resolving the pioarduino GitHub registry URLs during normal rebuilds on this machine. `.pio-local` is neither deleted nor overwritten.

A1R10 reconnect recovery: a transient Companion WebSocket drop can no longer leave the device permanently deaf. Disconnect still pauses wake listening and enters Error, but a successful reconnect now clears only the transport-originated fault, returns to Idle, and lets the existing wake-mic canonical restart path re-arm MultiNet. The Gateway also treats `ConnectionClosed` as a recoverable transport event instead of printing a handler traceback.

A1R9 listening reliability: idle wake-word listening now uses a modest 9 dB ES8311 PGA while the validated command-capture path remains at 6 dB. The post-wake speech-start window is 5.0 s, and new WAKE-HEALTH / WAKE-HIT diagnostics expose whether audio is genuinely reaching MultiNet. The wake phrase and MultiNet default threshold remain frozen and unchanged.

A1R8 headline newline support: OpenClaw-provided explicit line breaks in Info headlines are now honored by the Gateway-rendered Glass2 frame while retaining the existing three-line 11 px layout and automatic wrapping for ordinary long headlines. No StickS3 firmware change is required.

A1R7 startup guard: the Gateway now verifies and, when necessary, repairs the persisted Volcengine TTS pairing on every start. The required standard voice is `seed-tts-2.0` + `zh_female_vv_uranus_bigtts`. If stale config somehow survives, startup aborts instead of sending a mismatched TTS request. PlatformIO uses the verified local offline platform paths; `.pio-local` remains untouched.

# HomeAIAgent A4.6 Notification A1R2

TTS restore A1R6: restored the previously device-verified standard voice pairing `seed-tts-2.0` + `zh_female_vv_uranus_bigtts`. A new one-time persistent-config migration replaces the A1R5 “调皮公主” speaker without touching API keys, OpenClaw tokens, or device firmware.

# HomeAIAgent A4.6 Notification A1 — OpenClaw Session Listener

Current feature baseline: **A4.6 Notification A1R11 Managed OpenClaw Transport on the frozen A1R10/A3R9 device baseline**.

## Preserved frozen behavior

- Cyber Expression Speaking steady-state: ~24 FPS (`42 ms/frame`).
- Local wake phrase: `逐光逐光`.
- A3R8 two-stage wake confirmation and explicit MultiNet threshold override remain withdrawn.
- Gapless TTS, Glass2 Info feed, Gold, 20 s capture ceiling, VAD threshold logic, display policy, Info Skill schedule, session cleanup and Brownout protection remain unchanged.
- `.pio-local` is machine-local state and is intentionally not included in source packages or Git.

## Notification A1

- No webhook, no reverse SSH tunnel, no new inbound port.
- A1R11 owns the Mac mini -> OpenClaw SSH/Tailscale local forward inside the HomeAIAgent Python service; no separately started tunnel process is required.
- Gateway opens an outbound OpenClaw Gateway WebSocket and subscribes to the exact stable voice session derived from `OPENCLAW_USER`.
- `session.message` / `sessions.changed` are treated as invalidation signals; bounded `chat.history` is the authoritative source.
- First deployment establishes a transcript baseline and never speaks old assistant messages.
- Reconnect history reconciliation catches notifications that arrived while the listener was disconnected.
- Normal synchronous voice replies are fingerprint-suppressed so the listener does not speak them twice.
- Async assistant outputs enter a durable local queue at `~/.local/share/HomeAIAgent/notification_queue.json`.
- If StickS3 is offline or busy, delivery waits until the existing device/TTS path becomes available.
- Existing Info frame transport temporarily renders the notification on Glass2 while the existing Gapless TTS path speaks it.

See `docs/NOTIFICATION_SESSION_LISTENER_A1.md`.

## Deployment

A1R11 is **server-side only**. StickS3 firmware is byte-identical to the A1R10 submission baseline, so no firmware rebuild/upload is required. Stop the legacy `openclaw_air_tunnel.sh` terminal once, then start only:

```bash
cd /Volumes/yemol_HDDisk/HomeAIAgent/gateway
./run_full.sh
```

`run_full.sh` synchronizes the persistent Python runtime, starts the embedded AsyncSSH transport, verifies OpenClaw when available, and then keeps the HomeAIAgent device server alive. SSH loss degrades only OpenClaw-dependent functions; the StickS3 WebSocket/server process remains alive and the transport reconnects automatically.

In a second terminal, restart the Gateway:

```bash
cd /Volumes/yemol_HDDisk/HomeAIAgent/gateway
chmod +x *.sh
./run_full.sh
```

Optional read-only listener contract check:

```bash
./check_notification_listener.sh
```

Expected startup logs include:

```text
[CFG] notification_listener enabled=True session=agent:main:openai-user:home-ai-agent:main transport=gateway-ws-outbound
[NOTIFY] OpenClaw listener connected ...
[NOTIFY] subscribed session=agent:main:openai-user:home-ai-agent:main
```

On the very first run, one additional line is expected:

```text
[NOTIFY] baseline established ... old history will not be spoken
```

## Build / upload

A1R10 requires a device rebuild because the reconnect recovery state machine is in `src/main.cpp`. Use **Clean -> Upload -> Monitor**, never Erase Flash, and preserve `.pio-local`.
## A1R12 restart/cache and display timing

- Gateway restart no longer forces a game + finance Info Skill refresh. It serves the existing last-good cache immediately and waits for the fixed wall-clock slots `09,11,13,15,17,19,21,23,01` for the next real refresh.
- Glass2 information-card hold time is now 15 seconds per item.
- This release changes StickS3 firmware timing, so deploy with Clean -> Upload -> Monitor. Do not Erase Flash and do not modify `.pio-local`.


## A1R17 audio level

Assistant TTS and the local wake acknowledgement share the same StickS3 speaker setting (master 255 / channel 255 / MAG5). The local `在的` clip is normalized in Flash so wake feedback is not quieter than ordinary answers.


### A1R17 wake acknowledgement level

The local `在的` wake acknowledgement uses a dedicated MAG6 path and a loudness-mastered embedded PCM. Normal assistant TTS remains on MAG5.
