# HomeAIAgent Notification A1 — OpenClaw voice-session listener

## Goal

Deliver OpenClaw reminders/background assistant outputs to the HomeAIAgent device without a reverse SSH tunnel, webhook port, or second inbound network service.

## Transport

HomeAIAgent reaches OpenClaw through the A1R11 managed outbound transport:

`Mac mini 127.0.0.1:18790 -> SSH/Tailscale -> OpenClaw 127.0.0.1:18789`

Notification A1 opens a second outbound WebSocket through that same local endpoint. It never asks OpenClaw to connect back to the Mac mini.

## Voice session key

HomeAIAgent voice requests continue to use the existing OpenAI-compatible request body:

`user=home-ai-agent:main`

OpenClaw's current OpenAI-compatible resolver maps a stable user to:

`agent:<agentId>:openai-user:<user>`

The default HomeAIAgent key is therefore:

`agent:main:openai-user:home-ai-agent:main`

No voice-session migration is performed.

## Listener lifecycle

1. Connect to the OpenClaw Gateway WebSocket with `operator.read`.
2. Subscribe with `sessions.messages.subscribe` for the exact voice session.
3. Use `session.message` / `sessions.changed` only as invalidation signals.
4. Re-read bounded authoritative `chat.history` after an invalidation.
5. Persist transcript identities in `~/.local/share/HomeAIAgent/openclaw_voice_listener_state.json`.
6. On first deployment, baseline the existing transcript and do not replay old assistant messages.
7. On reconnect, reconcile history so events missed during a network outage are recovered.
8. New asynchronous assistant messages enter the durable local notification queue.
9. Existing HomeAIAgent TTS + device playback sends the notification when StickS3 is available.

## Duplicate protection

Normal voice replies are already returned synchronously by `/v1/chat/completions`. The listener therefore gates reconciliation while a voice OpenClaw request is in flight and registers the exact synchronous assistant reply as a short-lived suppression fingerprint. When the same transcript row later appears in `chat.history`, it is marked seen and is not spoken twice.

Each asynchronous transcript message also has a durable identity key based on OpenClaw transcript metadata plus text digest. The queue and delivered history provide a second deduplication layer.

## Offline behavior

If StickS3 is offline or busy, the notification remains in:

`~/.local/share/HomeAIAgent/notification_queue.json`

The listener state and queue are outside the project tree, so direct-overwrite project updates do not erase them.

## Frozen areas

This change is Gateway-only. It does not modify:

- `src/main.cpp`
- `platformio.ini`
- StickS3 wake-word path
- 24 FPS expression rendering
- Glass2 fonts/layout
- Gapless TTS device pipeline
- Info Skill schedule/session cleanup
- Gold refresh
- night display policy

## Validation

With the A1R11 HomeAIAgent service running:

```bash
cd /Volumes/yemol_HDDisk/HomeAIAgent/gateway
./check_notification_listener.sh
```

This is read-only: it subscribes to the exact voice session and reads a bounded `chat.history` sample without modifying the transcript.

Offline reconciliation regression:

```bash
python test_notification_listener_offline.py
```
