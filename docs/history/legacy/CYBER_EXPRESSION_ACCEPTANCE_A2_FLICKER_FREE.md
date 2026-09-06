# Cyber Expression A2 Flicker-Free - Device Acceptance

## Primary check

Observe the StickS3 main LCD for at least 20 seconds in each animated state.

### PASS

- No whole-screen black flash between frames.
- No visible clear-then-redraw sweep.
- Only intended local elements move or change brightness.

### Intended motion that is not a fault

- Idle: gentle central-core breathing.
- Listening: side receive arcs/packets.
- Thinking: orbit and data-node rotation.
- Speaking: side waveform motion.
- Error: restrained local glitch bars.

### FAIL

- Entire LCD flashes black.
- Entire LCD brightness pulses with each animation frame.
- Old and new frames visibly build line-by-line.
- Audio develops new gaps/clicks while animations run.

## Regression checks

1. Wake phrase still enters Listening.
2. Button-A PTT still enters Listening and releases into Thinking.
3. Thinking enters Speaking when TTS begins.
4. Speaking returns to Idle after playback.
5. Gapless long-answer TTS remains gapless.
6. Glass2 text size, vertical position and information rotation remain unchanged.
