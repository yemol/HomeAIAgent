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

Announces the terminal and includes the current/previous/next information context.

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
