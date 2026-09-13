# A1R25A.1 Wake Evidence

This build is diagnostics-only. It does not change wake acceptance behavior.

## What changed

- Candidate metrics are extracted from the current/recent speech segment, not the full two-second history.
- Short pauses up to 240 ms stay inside the same observed speech segment.
- Each MultiNet command event gets an event ID in `[WAKE-EVENT]`; `[WAKE-OBS]` and `[WAKE-HIT]` carry the same ID.
- If A-button manual PTT follows recent speech that did not produce a wake event, the firmware prints `[WAKE-MISS-SUSPECT]`. The A button still performs normal manual PTT.
- Literal `\n` logging artifacts are removed.

## Unchanged

- Wake phrase: `你好逐光`
- MultiNet default threshold
- Wake PGA: 9 dB
- Capture PGA: 6 dB
- Local `在的` acknowledgement
- Wake acceptance logic
- PTT/VAD behavior
- Gateway/OpenClaw/TTS/Glass2/Wi-Fi logic

## Useful log lines

```text
[WAKE-MN]
[WAKE-EVENT]
[WAKE-OBS]
[WAKE-OBS-SPEECH]
[WAKE-HIT]
[WAKE-MISS-SUSPECT]
```

Use the device normally. If wake misses, immediately use the A button as normal; the diagnostic marker is added without changing the button behavior.
