# Companion Protocol P0

P0 keeps one WebSocket connection between StickS3 and the local Companion Gateway.

## JSON control frames

### Device -> Gateway

`device.hello`
```json
{"type":"device.hello","item_id":"game_001","headline":"..."}
```

`ptt.start`
```json
{"type":"ptt.start","item_id":"game_001","headline":"..."}
```

`ptt.stop`
```json
{"type":"ptt.stop","item_id":"game_001","headline":"..."}
```

`item_id` is deliberately attached to the PTT event. This is the foundation for
natural references such as “这个讲讲”, because the backend knows exactly which
headline was on Glass2 when the user started speaking.

### Gateway -> Device

`assistant.state`
```json
{"type":"assistant.state","state":"thinking"}
```

Allowed P0 states:
- `idle`
- `listening`
- `thinking`
- `speaking`
- `success`
- `error`

## Reserved P0.2 frames

The following are reserved but not implemented in P0-A1 firmware yet:
- `info.replace`
- `info.current`
- `home.result`
- `audio.begin`
- binary microphone PCM frames
- binary TTS PCM frames

## Audio target for P0.2

Initial target:
- mono
- signed PCM16 little-endian
- 16 kHz microphone stream
- server is responsible for ASR / Agent / TTS routing
