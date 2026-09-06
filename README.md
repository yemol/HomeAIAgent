# HomeAIAgent A4.5 Cyber Expression A3R9

Current frozen device baseline: **24 FPS Speaking + restored default MultiNet wake path**.

## Frozen behavior
- Cyber Expression Speaking steady-state: ~24 FPS (`42 ms/frame`).
- Local wake phrase: `逐光逐光`.
- A3R8 two-stage wake confirmation and explicit MultiNet threshold override are not used.
- Gapless TTS, Glass2, Info Skill/Gold, capture/VAD, display policy and Brownout protection remain unchanged.
- `.pio-local` is machine-local state and is intentionally not included in source packages or Git.

## Build / upload
1. PlatformIO **Clean**.
2. **Upload** `m5stack-sticks3-wake`.
3. **Monitor** at `115200`.

A successful local wake includes:

```text
[TRG] wake_word
```

The A3R9 pre-build ESP-SR migration only removes the withdrawn A3R8 threshold injection if it is still present in the local preserved `.pio-local`; it never deletes or replaces `.pio-local`.
