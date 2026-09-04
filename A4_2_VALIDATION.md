# HomeAIAgent A4.2 Validation

PASS
- Gateway Python syntax compile
- headline limit 36
- summary limit 160
- three-line headline renderer present
- category/header removed from Mini-rendered Glass2 frame
- bottom-left zero-padded item index
- bottom-right gold status retained
- LIVE/WIFI not rendered by Gateway
- night window edge tests: 01:05 inclusive, 09:00 exclusive
- display.sleep/display.wake Gateway protocol
- current display policy sent on device.hello
- independent display scheduler task
- StickS3 LCD brightness-off path
- Glass2 black/brightness-off path
- any-button manual wake path
- 2-minute temporary night wake
- screen saver does not power down ESP32/Wi-Fi/Gateway
- 20-item device cache retained
- 10-second item hold retained
- A3.9 TTS slot declarations retained
- brownout/PTT PSRAM path retained
- C++ brace sanity check

NOT RUN
- PlatformIO compile: PlatformIO is unavailable in this artifact runtime.
- Real Glass2 visual acceptance.
- Real night transition test at 01:05/09:00.

Suggested real-device test before waiting overnight:
1. Flash normally, DO NOT erase.
2. Start Gateway.
3. Confirm the new 3-line layout.
4. Use temporary local test times only if needed, or wait for 01:05.
5. At night, press A/B and confirm instant wake.
6. Wait two minutes idle and confirm the screens sleep again.
