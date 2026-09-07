A1R10 reconnect recovery: a transient Companion WebSocket drop can no longer leave the device permanently deaf. Disconnect still pauses wake listening and enters Error, but a successful reconnect now clears only the transport-originated fault, returns to Idle, and lets the existing wake-mic canonical restart path re-arm MultiNet. The Gateway also treats `ConnectionClosed` as a recoverable transport event instead of printing a handler traceback.

A1R9 listening reliability: idle wake-word listening now uses a modest 9 dB ES8311 PGA while the validated command-capture path remains at 6 dB. The post-wake speech-start window is 5.0 s, and new WAKE-HEALTH / WAKE-HIT diagnostics expose whether audio is genuinely reaching MultiNet. The wake phrase and MultiNet default threshold remain frozen and unchanged.

A1R8 headline newline support: OpenClaw-provided explicit line breaks in Info headlines are now honored by the Gateway-rendered Glass2 frame while retaining the existing three-line 11 px layout and automatic wrapping for ordinary long headlines. No StickS3 firmware change is required.

A1R7 startup guard: the Gateway now verifies and, when necessary, repairs the persisted Volcengine TTS pairing on every start. The required standard voice is `seed-tts-2.0` + `zh_female_vv_uranus_bigtts`. If stale config somehow survives, startup aborts instead of sending a mismatched TTS request. PlatformIO uses the verified local offline platform paths; `.pio-local` remains untouched.

# HomeAIAgent A4.6 Notification A1R2

TTS restore A1R6: restored the previously device-verified standard voice pairing `seed-tts-2.0` + `zh_female_vv_uranus_bigtts`. A new one-time persistent-config migration replaces the A1R5 “调皮公主” speaker without touching API keys, OpenClaw tokens, or device firmware.

# HomeAIAgent A4.6 Notification A1 — OpenClaw Session Listener

Current feature baseline: **A4.6 Notification A1R10 WebSocket Reconnect Wake Recovery on the frozen A3R9 wake/24 FPS baseline**.

## Preserved frozen behavior

- Cyber Expression Speaking steady-state: ~24 FPS (`42 ms/frame`).
- Local wake phrase: `逐光逐光`.
- A3R8 two-stage wake confirmation and explicit MultiNet threshold override remain withdrawn.
- Gapless TTS, Glass2 Info feed, Gold, 20 s capture ceiling, VAD threshold logic, display policy, Info Skill schedule, session cleanup and Brownout protection remain unchanged.
- `.pio-local` is machine-local state and is intentionally not included in source packages or Git.

## Notification A1

- No webhook, no reverse SSH tunnel, no new inbound port.
- Reuses the existing Mac mini -> OpenClaw SSH/Tailscale tunnel at `127.0.0.1:18790`.
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

A1R10 includes a StickS3 state-machine fix and therefore **requires a firmware rebuild/upload**. Preserve `.pio-local`; never Erase Flash. Use the verified sequence **Clean -> Upload -> Monitor** for environment `m5stack-sticks3-wake`, then restart the Gateway.

```bash
cd /Volumes/yemol_HDDisk/HomeAIAgent
pio run -e m5stack-sticks3-wake -t clean
pio run -e m5stack-sticks3-wake -t upload
pio device monitor
```

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
