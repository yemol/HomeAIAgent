# A1R25B0 Change Scope

Intentional runtime changes:

- `include/app_config.h`: wake PGA 9 dB -> 6 dB.
- `src/main.cpp`: reset Wake Observatory state across wake-listen pause/restart; version/log labels.

Documentation only:

- `README.md`
- `CHANGELOG.md`
- `docs/WAKE_FRONTEND_A1R25B0.md`
- this file

Explicitly unchanged:

- Gateway source
- OpenClaw / Agent / Skills
- NetworkSpeaker
- MultiNet threshold
- wake acceptance logic
- command-capture PGA
- VAD / local wake ACK / TTS / Glass2 / Wi-Fi
