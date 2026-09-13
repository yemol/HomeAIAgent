# A1R25A Wake Observatory test

This build changes diagnostics only. Do not tune thresholds while collecting the first sample set.

## Deploy

Preserve the existing `.pio-local` directory. Do not erase flash.

```bash
pio run -e m5stack-sticks3-wake -t clean
pio run -e m5stack-sticks3-wake -t upload
pio device monitor
```

On boot confirm:

```text
=== HomeAIAgent A1R25A / Wake Observatory / Wake=你好逐光 ===
[WAKE] local keyword engine ready: 你好逐光
[WAKE-OBS] A1R25A observe-only enabled; wake acceptance behavior unchanged
```

## First sample set

1. 0.5-1 m: say `你好，逐光` 10 times in a normal voice.
2. About 2 m: 10 times.
3. About 3 m: 10 times.
4. Leave normal conversation / TV audio running long enough to catch false triggers if they occur.

For a successful or false trigger, save the nearby lines containing:

```text
[WAKE-MN]
[WAKE-OBS]
[WAKE-HIT]
```

For a spoken phrase that fails to wake, save the nearest:

```text
[WAKE-OBS-SPEECH]
```

`wakeHit=0` means the acoustic observer saw a speech-like burst but the existing MultiNet wake path did not accept it. This is diagnostic only and does not create a wake event.

Do not change PGA, threshold, VAD, or phrase during this collection.
