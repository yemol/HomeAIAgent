# A1R25B0 Wake Front-End A/B

## Purpose

Measure whether lowering wake-listening analog PGA from 9 dB to 6 dB improves `你好逐光` recall by reducing waveform clipping.

## Controlled change

- Wake PGA: 9 dB -> 6 dB
- Capture PGA: unchanged at 6 dB
- MultiNet model: unchanged (`mn5q8_cn`)
- MultiNet default threshold: unchanged
- Wake acceptance logic: unchanged
- VAD: unchanged
- Gateway / OpenClaw / TTS / Glass2 / NetworkSpeaker: unchanged

## Test

From the same normal speaking position, say `你好逐光` around 20 times at a natural pace. Record:

- number of successful wake acknowledgements
- `[WAKE-OBS-SPEECH] ... wakeHit=0` misses
- successful `[WAKE-OBS] ... clip=...` values
- any spontaneous false wakes

The first target is materially better recall than the roughly 50% A1R25A.1 trial while reducing clipping from the previously observed ~2-6% range toward near-zero.

## Interpretation

If clipping drops strongly and recall rises, keep the lower PGA as the new front-end baseline. If clipping drops but recall stays near 50%, the next optimization should focus on MultiNet candidate/phrase behavior rather than microphone gain.
