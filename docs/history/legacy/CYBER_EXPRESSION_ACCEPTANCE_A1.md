# Cyber Expression A1 - Device Acceptance

## Flash target

Use the same PlatformIO environment and provisioning flow as the current HomeAIAgent baseline. This package does not change Gateway configuration or NVS schema.

## Visual checks

1. Boot reaches the new blue cyber core instead of the old cute face.
2. Idle shows a stable segmented VISOR with slow restrained breathing.
3. Hold / trigger voice input: Listening shows inward side signal motion.
4. After speech capture ends: Thinking shows rotating calculation rings.
5. During TTS: Speaking shows outward waveform clusters.
6. If Gateway reports success/error, the screen uses the matching cyber completion/fault state and never returns to the old eye/cheek face.
7. Sleep/wake still blanks and restores the StickS3 LCD correctly.

## Regression checks

1. Manual PTT recording still starts/stops normally.
2. Local wake-word flow still reaches Listening -> Thinking -> Speaking -> Idle.
3. Long TTS remains gapless across segments.
4. No new click/pop/brownout/reset occurs during voice turns.
5. Glass2 text size, position and feed cycling are unchanged.
6. Button B information navigation remains unchanged.
7. Night screen-protection window remains unchanged.

## Pass criterion

A1 is accepted only after the real StickS3 confirms both visual readability and no regression in wake/PTT/TTS/Glass2 behavior.
