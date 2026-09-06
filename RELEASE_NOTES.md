# Release Notes: A4.5 Cyber Expression A2 / Flicker-Free

**Date:** 2026-09-06

## Scope

This release keeps the validated HomeAIAgent voice, Gateway and Glass2 behavior intact and finalizes the new cyber-expression main screen.

Preserved functional baselines:

- StickS3 voice/state path: A4.4.18 RC1R9
- Gateway: A4.4.18 RC1R13
- A3.9 gapless segmented TTS
- Glass2 feed, fonts, positions, schedule and night-display behavior
- wake-word, PTT, ASR, OpenClaw and audio state machine

## Cyber expression A2

The previous direct-to-LCD animation path exposed an intermediate cleared frame and produced visible whole-screen flicker. A2 replaces it with a full-screen off-screen `M5Canvas`:

1. allocate a 16-bit RGB565 135×240 canvas, preferring PSRAM;
2. render the complete cyber-expression frame into the canvas;
3. keep the previous complete LCD frame visible while drawing;
4. transfer the completed frame to the LCD once with `pushSprite()`.

Real-device verification confirmed that the whole-screen flicker is gone.

## State refresh policy

- Idle: 10 FPS
- Listening: ~6 FPS
- Thinking: 20 FPS
- Speaking: 8 FPS
- Success: 12.5 FPS
- Error: 10 FPS

Listening and Speaking remain deliberately conservative to protect Mic/I2S and gapless TTS timing.

## Submission cleanup

This submission also cleans documentation and comments without changing runtime behavior:

- version-history breadcrumbs removed from normal source comments;
- current docs consolidated under `docs/`;
- historical release notes moved to `docs/history/`;
- outdated Gateway documentation replaced;
- generated `gateway/latest_*.wav` / `gateway/latest_*.txt` artifacts removed;
- `.gitignore` added for local toolchains, secrets and runtime outputs;
- README corrected to reflect that `srmodels.bin` is a required included asset.
