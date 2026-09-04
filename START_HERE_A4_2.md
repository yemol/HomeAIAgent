# HomeAIAgent A4.2

Based on the real-device accepted A4.1.3 baseline.

## Glass2 UI

New layout:

```text
资讯正文第1行
资讯正文第2行
资讯正文第3行
────────────────
03/20          金 ¥xxxx.x/g
```

Changes:
- category (`游戏` / `金融`) is NOT displayed
- category remains in Skill/Gateway data for filtering and context
- 3 headline lines, body font remains 11 px
- item position moved to bottom-left
- `LIVE` removed
- `WIFI` remains removed
- RMB gold remains bottom-right
- headline max: 36 Unicode chars
- summary max: 160 Unicode chars

## Night screen protection

Timezone: `Asia/Taipei` by default.

```text
01:05  screen protection begins
09:00  normal display resumes
```

During protection:
- StickS3 LCD brightness -> 0
- Glass2 pixels black + brightness -> 0
- ESP32-S3 stays running
- Wi-Fi stays connected
- Gateway stays connected
- gold quote continues updating
- Info Skill scheduling continues according to the existing schedule
- voice/audio system remains available

Any A/B button press:
- wakes both displays immediately
- normal button action still executes
- extends manual wake for 2 minutes
- if still inside the night window and idle after 2 minutes, screens sleep again

The Gateway is the wall-clock authority and sends:
- `display.sleep`
- `display.wake`

On every device reconnect it immediately sends the correct current display policy,
so a Gateway/device restart during the night does not permanently desynchronize the
screen-protection state.

## Existing frozen behavior retained

- A3.7 brownout mitigation
- A3.9 Gapless TTS
- 20 info items
- game 10 + finance 10 target
- 10-second item hold
- HomeAI Info v1.1
- info schedule: 00:00, 01:00, 09:00-23:00 hourly
- gold quote direct from Gateway, 5-minute refresh
- persistent Mac config/runtime
- StickS3 NVS config
