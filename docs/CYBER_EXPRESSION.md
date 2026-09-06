# Cyber Expression UI

## Design language

HomeAI uses a restrained cyber-terminal identity rather than a cute digital-pet face. Every state shares four visual anchors:

1. central luminous AI core;
2. segmented horizontal VISOR;
3. incomplete mechanical/data orbits;
4. fixed cardinal sensor marks.

The shared structure keeps the Agent recognizable while state-specific motion changes only part of the visual grammar.

## States

### Idle

Low-amplitude core breathing, stable VISOR and restrained orbit movement.

### Listening

Signal packets and receiver brackets converge toward the central core.

### Thinking

Counter-rotating orbit layers, scanning motion and sparse data nodes make this the most computational state.

### Speaking

Energy/wave clusters propagate outward from the central core rather than using a conventional equalizer bar.

### Success

Orbit geometry briefly aligns into a synchronized mechanical completion state. No software-style checkmark is used.

### Error

The same HomeAI identity becomes misaligned: red accent, broken VISOR geometry and short deterministic glitch bars. The screen does not strobe.

## Flicker-free renderer

The production StickS3 orientation is 135×240. A2 allocates one full RGB565 off-screen `M5Canvas` (about 64.8 KB), preferring PSRAM.

Per frame:

1. clear the canvas, not the physical LCD;
2. render the full cyber expression into the canvas;
3. transfer the completed frame once with `pushSprite()`.

The previous complete frame remains visible while the next frame is being composed. This removes the clear/redraw flash visible in A1.

## Frame caps

- Idle: 10 FPS
- Listening: ~6 FPS
- Thinking: 20 FPS
- Speaking: 8 FPS
- Success: 12.5 FPS
- Error: 10 FPS

Listening and Speaking intentionally run slower than Thinking so UI animation does not compete with the audio-critical path.

## Acceptance status

Real-device result for A2:

- full-screen flicker: **PASS, removed**
- Idle breathing retained
- Thinking orbit motion retained
- Listening/Speaking motion retained
- state transitions retained
- Glass2 unchanged
- gapless TTS unchanged

The earlier visual concept reference is retained at `docs/assets/cyber_expression_reference_a1.png` for design history only.
