# Cyber Expression UI

## Design language

HomeAI uses a restrained cyber-terminal identity rather than a cute digital-pet face. Every persistent state shares four anchors:

1. luminous central AI core;
2. segmented horizontal VISOR;
3. incomplete mechanical/data orbits;
4. fixed cardinal sensor marks.

A3 keeps the same identity and changes motion direction, rhythm and hierarchy so states are easier to read from the edge of a monitor.

## A3 state language

### Idle

Very slow orbit drift, low-amplitude core breathing and one sparse sentinel scan. Idle is intentionally the least animated state.

### Wake transition

Wake is not a new protocol/runtime state. When `Idle -> Listening` occurs, the first 560 ms are rendered as core ignition, VISOR expansion and orbit acquisition. After that the normal Listening renderer takes over.

### Listening

All motion points inward. Paired chevrons converge from the left/right edges toward the core while receiver brackets close around it.

### Thinking

The signature computational state. Three incomplete compute lanes rotate at different rates and directions, sparse nodes orbit independently, and a scan needle sweeps the field. The VISOR becomes shorter so the orbital system owns the composition.

### Speaking

The central CORE remains the only visible acoustic source. A3R9 retains A3R7's renderer and reads the PCM buffer already being played by the gapless TTS queue and estimates short-term voice energy from a small trailing window at display-frame cadence. The latest energy is injected at the CORE and recent energy is retained as a short history that is mapped outward along the left/right wave traces. New syllables therefore appear near the CORE and propagate toward the edges while older energy moves outward. Pauses collapse the source wave naturally; emphasis produces visibly larger wave peaks. No microphone loopback is used. Speaking steady-state rendering is ~24 FPS (`42 ms`).

### Success

Unchanged from A2: orbit geometry aligns into a synchronized mechanical completion state.

### Error

Unchanged from A2: red accent, broken alignment and short deterministic glitch bars without full-screen strobing.

## Flicker-free renderer

The production StickS3 orientation is 135×240. Rendering still uses one full RGB565 off-screen `M5Canvas` (about 64.8 KB), preferring PSRAM.

Per frame:

1. clear the off-screen canvas;
2. render the complete expression into the canvas;
3. transfer the completed frame once with `pushSprite()`.

The previous complete frame remains visible while the next frame is composed. A3 does not change this A2 renderer.

## Frame caps

Steady state:

- Idle: ~6 FPS
- Listening: ~6 FPS
- Thinking: 20 FPS
- Speaking: ~24 FPS
- Success: 12.5 FPS
- Error: 10 FPS

A3R9 keeps the existing short transition cadence boost:

- Idle → Listening Wake: ~24 FPS for 560 ms;
- ordinary transitions: approximately 20-24 FPS for up to 220 ms.

After the transition window, each state immediately returns to its steady-state cap. Sustained Listening remains at the conservative capture cadence, while Speaking now uses ~24 FPS for the PCM-synchronized waveform.

## Smooth state morph

During ordinary state changes the shared CORE, VISOR and orbit geometry interpolates over 220 ms using smoothstep. Outgoing state motion fades while incoming directional/rotational motion begins during the same morph, and time-driven bridge arcs preserve orbital momentum through the handoff. The persistent outer field arcs keep a fixed radius across `setState()` instead of collapsing and re-expanding. Together these changes remove the calibration-like pause without changing the final state artwork.

## Acceptance status

A2 flicker-free behavior is already real-device verified. A3R1 transition refinements received positive real-device feedback. A3R9 keeps the accepted CORE-origin PCM synchronization, A3R6 state-handoff behavior, and ~24 FPS Speaking cadence unchanged. The failed A3R8 wake-confirm experiment is removed; wake behavior is restored to the A3R7 path.
