# Companion Protocol

HomeAIAgent uses one persistent WebSocket connection between StickS3 and the local Gateway.

## Audio format

### Device -> Gateway microphone

- binary WebSocket frames
- signed PCM16 little-endian
- mono
- 16,000 Hz

The device buffers the utterance in PSRAM during Mic/I2S capture and uploads after capture stops.

### Gateway -> Device TTS

Each device-safe segment begins with:

```json
{
  "type": "tts.start",
  "sample_rate": 16000,
  "format": "pcm_s16le",
  "channels": 1,
  "bytes": 123456,
  "segment_index": 1,
  "segment_total": 3
}
```

Then binary PCM frames are sent, followed by:

```json
{
  "type": "tts.end",
  "segment_index": 1,
  "segment_total": 3
}
```

StickS3 maintains two playback slots. When the queued next slot becomes available, it sends:

```json
{"type":"playback.slot_ready"}
```

After the final segment finishes:

```json
{"type":"playback.done"}
```

A playback failure is reported with `playback.error`.

## Device -> Gateway JSON events

### `device.hello`

Announces one Gateway client.

Companion terminal example:

```json
{
  "type": "device.hello",
  "device_id": "homeai-mini-bedroom-01",
  "device_role": "companion",
  "capabilities": {
    "ptt": true,
    "speaker_pcm16": true
  }
}
```

`device_role` is optional; omitted means `companion`. For backward compatibility,
the current StickS3 may omit `device_id`; Gateway assigns it the configured
`HOMEAI_PRIMARY_DEVICE_ID`.

Optional companion capabilities are protocol-specific:

```json
{
  "capabilities": {
    "display": "135x240",
    "display_policy": false,
    "info_feed": false
  }
}
```

`display` only describes the physical screen. It does **not** mean the terminal
implements `display.sleep` / `display.wake` / `display.ack`.

`display_policy=true` explicitly opts into that sleep/wake handshake.

`info_feed=true` explicitly opts into the Glass2-style `info.begin` /
`info.item` / `info.end` feed.

The current primary HomeAIAgent remains backward-compatible and is treated as
supporting both services even without capability flags. HomeAIAgent Mini does
not currently opt into either service.

NetworkSpeaker example:

```json
{
  "type": "device.hello",
  "device_id": "speaker-mini-dock-01",
  "device_role": "speaker",
  "parent_device_id": "homeai-mini-bedroom-01",
  "audio_priority": 100,
  "capabilities": {
    "speaker_pcm16": true
  }
}
```

A speaker owns no OpenClaw conversation. `parent_device_id` binds it to exactly
one companion. Multiple speakers can coexist when each binds to a different
parent. If more than one live speaker binds to the same parent, the highest
`audio_priority` wins; equal priority uses the newest connection.

The dedicated Mini charging-dock speaker should bind to:

```text
parent_device_id = homeai-mini-bedroom-01
```

### `ptt.start`

Starts one utterance. `trigger` identifies the source:

- `wake_word`
- `button_a`

The message carries current/previous/next information context so OpenClaw can resolve phrases such as “这个”, “上一条” and “下一条”.

### Binary microphone frames

All microphone audio belongs to the active PTT turn.

### `ptt.stop`

Ends capture and includes the declared audio format/byte count and trigger source.

### `ptt.abort`

Cancels the local turn without invoking ASR/OpenClaw. Known reasons include:

- `no_speech`
- `buffer_overflow`
- `empty`
- `tx_failed`

### `info.ack`

Acknowledges successful commit of a complete information revision.

### `display.ack`

Reports display-policy execution with:

- `command_id`
- requested state
- `status` (`pending` or `applied`)
- actual `sleeping` state
- busy/manual-wake context

## Gateway -> Device JSON events

### `gateway.ready`

Sent after `device.hello`.

For a companion it confirms the resolved device identity and whether its
conversation is isolated from the primary HomeAIAgent session.

For a speaker it includes `parent_device_id` and:

```text
audio_protocol = homeai-tts-pcm16/1
```

Speaker clients consume the existing `tts.start` -> binary PCM16 -> `tts.end`
stream and return the existing `playback.slot_ready`, `playback.done` and
`playback.error` acknowledgements.

### `assistant.state`

States:

- `idle`
- `listening`
- `thinking`
- `speaking`
- `success`
- `error`

These states drive the StickS3 cyber-expression UI.

### `asr.result`

ASR transcript for diagnostics/UI.

### `assistant.text`

Final text answer from the agent path.

### `assistant.error`

Short error diagnostics.

### Information sync

A full feed revision is transferred as:

1. `info.begin` with revision and count;
2. one `info.item` per entry containing metadata and a pre-rendered 128×64 monochrome `frame_hex`;
3. `info.end`.

StickS3 stages the incoming revision and commits only after the complete set is valid.

### Display policy

Gateway sends `display.sleep` or `display.wake` with a unique `command_id`. Device acknowledgement is matched to that identifier.

## Half-duplex rule

The StickS3 ES8311 path is half-duplex by design. Voice capture, network-heavy upload and TTS playback are sequenced rather than overlapped.

## Reminder A1

Reminder A1 adds no new StickS3 JSON message type. The Mac mini Gateway reuses two already-frozen transports:

1. Existing Info sync (`info.begin`, one `info.item`, `info.end`) temporarily carries a pre-rendered 128x64 reminder frame.
2. Existing TTS (`tts.start`, binary PCM, `tts.end`) speaks the reminder, and existing `playback.done` is the final device delivery acknowledgement.

After `playback.done`, the Gateway sends the canonical Info revision again so Glass2 returns to the normal feed. If the device is offline/busy or delivery fails, the persistent Gateway reminder queue retries later.
