# HomeAIAgent P0 A4.4.18 RC1R7

## Boot-loop root fix

RC1R6 produced a firmware image of 2,293,792 bytes while `app0` is 2,293,760 bytes. The bootloader therefore rejected the image before application startup.

RC1R7 keeps the RC1R6 trigger-source tracing behavior and makes only size/diagnostic changes:

- Keep `trigger=wake_word` and `trigger=button_a` in `ptt.start`.
- Keep Gateway trigger logging unchanged.
- Compact nonessential terminal diagnostic strings.
- Lower Arduino core library log level from INFO (3) to WARN (2); project `Serial` diagnostics remain intact.
- Add `scripts/check_app_partition_size.py` as a build/upload hard gate for the fixed `0x230000` app0 partition. Oversized firmware is blocked before upload.
- No partition-table change.
- No `.pio-local` change.
- No MultiNet/VAD/button behavior change.
- No change to Brownout-safe wake ACK, A3.9 gapless TTS, info snapshots, or overnight screen schedule.

## Recovery

The bootloader remains usable. Flash RC1R7 with PlatformIO `Clean -> Upload -> Monitor`; Erase Flash is not required.
