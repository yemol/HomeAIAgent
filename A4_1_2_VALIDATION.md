# HomeAIAgent A4.1.2 Validation

PASS:
- Gateway Python syntax compile
- Hourly/quiet-window schedule edge-case tests
- 00:15 -> 01:00
- 01:00:05 -> 09:00
- 01:30 -> 09:00
- 08:59 -> 09:00
- 09:10 -> 10:00
- 23:10 -> next-day 00:00
- Skill `next_refresh_after_sec` no longer controls scheduler
- last-good cache behavior retained
- HomeAI Info v1.1 / 20 items / 10+10 retained
- headline <=30 / summary <=115 retained
- device INFO_MAX_ITEMS=20
- device INFO_HOLD_MS=10000
- A4.0.3 Glass2 font/layout retained in Gateway
- known-good firmware global declaration block restored
- TtsSlot declarations restored
- WebSocket/Wi-Fi state declarations restored
- PTT/mic globals restored
- A3.9 Gapless TTS source retained
- C++ brace sanity check

NOT RUN:
- PlatformIO compilation, because PlatformIO is unavailable in the artifact runtime.

Real Mac acceptance:
1. Open corrected full project.
2. PlatformIO Upload without erase.
3. If compile succeeds, boot device.
4. Start Gateway.
5. Confirm log prints next scheduled poll.
6. Around 01:00, next scheduled poll must be 09:00.
