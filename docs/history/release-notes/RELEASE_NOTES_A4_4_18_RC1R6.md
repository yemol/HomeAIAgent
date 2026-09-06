# HomeAIAgent P0-A4.4.18 RC1R6

## Changes

- Adds PTT trigger provenance without changing any trigger behavior.
- Wake-word initiated turns send `trigger=wake_word` with `ptt.start`.
- Manual A-button initiated turns send `trigger=button_a` with `ptt.start`.
- Gateway stores the source for the active turn and prints it on both `[PTT] start` and `[PTT] stop`.
- Terminal serial logs identify wake-word detection and manual button PTT separately.
- Keeps the `逐光逐光` wake phrase unchanged.
- Keeps RC1R4 all-refresh snapshot capture unchanged.
- Keeps brownout-safe wake ACK, Wake Feed Watchdog, A3.9 gapless TTS, Glass2, Gold refresh, fixed info schedule, and night-screen policy unchanged.

## Diagnostic intent

This build is deliberately observational. It does not raise MultiNet thresholds, alter VAD, remove manual PTT, or change button handling. The next unexpected conversation can therefore be attributed directly to `wake_word` or `button_a` before changing behavior.

## Overnight screen test

The frozen night-screen policy remains unchanged: sleep at 01:05 and wake at 09:00 local time.
