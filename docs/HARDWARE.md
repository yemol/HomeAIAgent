# Hardware

## Main devices

- M5Stack StickS3
- M5Stack Unit Glass2

## Glass2 connection

Use the normal HY2.0-4P / Grove connection.

- supply: Grove 5 V rail
- I2C address: `0x3C`
- SDA: StickS3 GPIO9
- SCL: StickS3 GPIO10

Firmware explicitly enables the StickS3 external output rail before Glass2 initialization.

## StickS3 audio

The onboard ES8311 path is treated as half-duplex:

- microphone and speaker are not run concurrently;
- Mic capture is stopped before speaker/TTS playback;
- speaker is stopped before wake listening/capture resumes.

Current production audio settings remain frozen in the firmware configuration.

## Display roles

### StickS3 LCD

Primary interaction/state display. Current candidate uses the A4.5 Cyber Expression A3R1 renderer, built on the verified A2 flicker-free path rather than the earlier digital-pet face.

### Glass2

Transparent information display for the pre-rendered game/finance feed and bottom status information.

## First hardware verification

After flashing the current baseline:

1. StickS3 boots and prints the A4.5 A3R4 PCM-Synced Voice Wave banner.
2. The cyber expression appears without full-screen flicker.
3. Glass2 initializes and displays an information frame.
4. Button A enters Listening and the voice path completes through Thinking/Speaking.
5. Wake phrase `逐光逐光` starts the hands-free path.
6. Glass2 navigation remains available outside active voice isolation.

If Glass2 initialization fails, the terminal emits a Glass2 initialization failure log over serial.
