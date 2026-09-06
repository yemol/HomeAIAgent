# HomeAIAgent A4.5 Cyber Expression A2 - Flicker-Free Renderer

## Baseline

Derived directly from `HomeAIAgent_A4_5_CYBER_EXPRESSION_A1_FULL.zip`, which itself was derived from the user-provided `归档.zip`.

Preserved without functional changes:

- Gateway A4.4.18 RC1R13
- StickS3 voice/state baseline A4.4.18 RC1R9
- A3.9 gapless TTS
- Glass2 info feed, fonts, positions, schedule and display policy
- Wake-word, PTT, ASR, OpenClaw and audio state machine
- Cyber expression visual design and state mapping from A1

## Root cause fixed

A1 rendered each animation frame directly to the physical LCD and called `fillScreen()` before redrawing the cyber UI. The LCD could therefore expose the intermediate cleared frame, producing visible whole-screen flicker.

A2 changes the StickS3 cyber-expression renderer to a full-screen off-screen `M5Canvas`:

1. allocate one 16-bit RGB565 canvas, preferring PSRAM
2. clear and draw the complete frame only inside the canvas
3. keep the previous complete LCD frame visible during rendering
4. push the finished canvas to the LCD once with `pushSprite()`

The physical StickS3 LCD is no longer cleared between animation frames.

## Memory

At the production 135 x 240 StickS3 orientation, the RGB565 frame buffer is about 64.8 KB. The renderer requests PSRAM first and has one SRAM fallback for development configurations where PSRAM is unavailable.

## Frame policy retained

- Idle: 10 FPS
- Listening: ~6 FPS
- Thinking: 20 FPS
- Speaking: 8 FPS
- Success: 12.5 FPS
- Error: 10 FPS

The lower Listening/Speaking frame rates remain intentionally conservative for Mic/I2S and A3.9 gapless TTS safety.

## Expected boot log

`=== HomeAIAgent A4.5 Cyber Expression A2 Flicker-Free / base A4.4.18 RC1R9 ===`

Expected one-time canvas log:

`[UI] Cyber canvas ready: 135x240 RGB565, 64800 bytes`

## Acceptance focus

Pass when:

- Idle core still breathes, but the whole screen does not flash
- Listening animation does not expose a black cleared frame
- Thinking rings move continuously without full-screen black flashes
- Speaking waveform moves without brightness pumping of the entire LCD
- state transitions remain immediate
- wake/PTT/ASR/TTS behavior is unchanged
- Glass2 behavior is unchanged

## Files intentionally changed from A1

- `src/main.cpp`
- `START_HERE.md`
- this release note
- `docs/CYBER_EXPRESSION_ACCEPTANCE_A2_FLICKER_FREE.md`
- `SUBMISSION_MANIFEST_SHA256.txt` regenerated
