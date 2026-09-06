# Companion Protocol P0-A2

One persistent WebSocket connection between StickS3 and the local Companion Gateway.

## Audio format

### Device -> Gateway microphone
- WebSocket Binary frames
- signed PCM16 little-endian
- mono
- 16,000 Hz
- 320 samples / frame (20 ms, 640 bytes)

### Gateway -> Device TTS
The Gateway first sends:

```json
{"type":"tts.start","sample_rate":24000,"bytes":123456}
```

Then one or more Binary PCM16 frames, followed by:

```json
{"type":"tts.end"}
```

The device buffers TTS in PSRAM, plays it, then sends:

```json
{"type":"playback.done"}
```

## Device -> Gateway JSON

### `device.hello`
Carries protocol version and the current info context.

### `ptt.start`
Freezes Glass2 and sends:
- current item
- previous item
- next item

This is what lets OpenClaw resolve “这个 / 上一条 / 下一条”.

### Binary microphone frames
All binary frames between `ptt.start` and `ptt.stop` are microphone PCM.

### `ptt.stop`
Includes the total microphone audio byte count.

## Gateway -> Device JSON

### `assistant.state`
States: `idle`, `listening`, `thinking`, `speaking`, `success`, `error`.

### `tts.start` / Binary / `tts.end`
See TTS transport above.

### `assistant.error`
Optional short diagnostics text for serial logs.

## Half-duplex audio rule
StickS3 uses the ES8311 audio path for both microphone and speaker. P0-A2 explicitly:
- ends Speaker before Mic.begin();
- ends Mic before Speaker.begin();
- never records and plays at the same time.
