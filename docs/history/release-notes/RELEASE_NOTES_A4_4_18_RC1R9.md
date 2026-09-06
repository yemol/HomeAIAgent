# HomeAIAgent P0 A4.4.18 RC1R9

## 10-second capture + abort/VAD trace

RC1R9 is based directly on RC1R8. It does not change the `逐光逐光` MultiNet sensitivity or Button A behavior.

### Changes

1. Maximum manual and hands-free command capture reduced from 30 s to 10 s.
2. PSRAM capture capacity is 11 s while the logical limit remains 10 s, leaving room for the final queued I2S blocks.
3. The duration guard runs before accepting another mic block, preventing a normal time-limit finish from becoming `PTT buffer overflow`.
4. Local failure paths send `ptt.abort` with both `trigger` and `reason` (`no_speech`, `buffer_overflow`, `empty`, `tx_failed`).
5. `ptt.stop` also carries the trigger source explicitly.
6. Gateway logs `ptt.abort` without invoking ASR/OpenClaw.
7. VAD diagnostics log only silence/voice-resume transitions and a timeout summary (`level`, `threshold`, `longest`).
8. Repetitive `[MEM]` output every 25 mic blocks was removed; reset checkpoints remain.

### New field evidence

The latest accidental turns are confirmed as `trigger=wake_word`, not `button_a`. One false turn produced an empty ASR transcript at roughly 7.9 dB SNR; another captured ordinary nearby conversation at roughly 7.4 dB SNR and reached the agent. RC1R9 intentionally keeps MultiNet sensitivity unchanged so the next false-wake trace remains directly comparable.

### Unchanged

- A3.9 gapless TTS
- Brownout-safe local acknowledgement
- Wake-feed watchdog
- Info Skill startup/scheduled snapshots
- Fixed 2-hour info schedule
- Gold refresh
- Glass2 layout
- 01:05 night sleep / 09:00 wake policy
- Partition table and `.pio-local`

### Update requirement

Both StickS3 and Gateway should be updated/restarted for the complete `ptt.abort` protocol trace. Use PlatformIO `Clean -> Upload -> Monitor`; do not Erase Flash.
