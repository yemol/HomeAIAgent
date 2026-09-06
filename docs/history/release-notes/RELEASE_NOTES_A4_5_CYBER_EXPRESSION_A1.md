# HomeAIAgent A4.5 Cyber Expression A1

## Baseline

This release is derived directly from the user-provided `归档.zip` baseline.

- Gateway baseline preserved: A4.4.18 RC1R13
- StickS3 functional baseline preserved: A4.4.18 RC1R9
- Gapless TTS A3.9 preserved
- Glass2 info feed, fonts, layout, refresh schedule and screen-protection logic preserved
- Wake-word, PTT, ASR, OpenClaw, audio and configuration paths preserved

## Change scope

StickS3 main-screen expression only.

The previous digital-pet face (large eyes / cheeks / mouth) is replaced by a cyber-industrial expression system built from one stable visual identity:

1. central luminous AI core
2. segmented horizontal VISOR
3. incomplete mechanical/data orbit rings
4. fixed cardinal sensor marks

## Runtime states

- `Idle` -> restrained breathing core / stable visor
- `Listening` -> incoming side signal brackets and packets
- `Thinking` -> counter-rotating calculation rings and data nodes
- `Speaking` -> outward directional waveform clusters
- `Success` -> mechanical ring alignment / synchronized completion
- `Error` -> red misalignment, broken visor geometry and restrained glitch bars

## Audio-safety policy

The renderer intentionally uses different frame caps by state:

- Idle: 10 FPS
- Listening: ~6 FPS
- Thinking: 20 FPS
- Speaking: 8 FPS
- Success: 12.5 FPS
- Error: 10 FPS

Listening and Speaking are deliberately lower-rate so UI animation does not compete aggressively with Mic/I2S capture or A3.9 gapless TTS playback.

No additional microphone RMS pass was inserted into the production audio loop solely for animation.

## Boot banner

`=== HomeAIAgent A4.5 Cyber Expression A1 / base A4.4.18 RC1R9 ===`

## Files intentionally changed

- `src/main.cpp`
- this release note
- `docs/CYBER_EXPRESSION_REFERENCE_A1.png`
- `docs/CYBER_EXPRESSION_ACCEPTANCE_A1.md`
- `SUBMISSION_MANIFEST_SHA256.txt` regenerated for this package
