# HomeAIAgent A1R25B1 - Glass2 Local Voice Control

A1R25B1 keeps the A1R25B0 wake-listening experiment unchanged at **6 dB** and adds two local voice commands for the primary HomeAIAgent Glass2 display.

## New local voice commands

After wake + ASR, these commands are handled by the Gateway locally and do **not** enter OpenClaw:

- `关闭屏幕` -> turn off **Glass2 only**
- `打开屏幕` -> turn on **Glass2 only**

Natural equivalents such as `关掉屏幕`, `息屏`, `亮屏`, and `点亮屏幕` are also accepted. The command does not change NetworkSpeaker routing or the StickS3 interaction display.

The manual Glass2 state has priority over the automatic night policy for Glass2 until the opposite voice command or a device reboot. The existing night policy still controls the StickS3 display normally.

## Update scope

- StickS3 firmware: adds `glass2.sleep` / `glass2.wake` / `glass2.ack` and independent Glass2 manual visibility state.
- Gateway: adds local ASR intent parsing plus ACK-confirmed Glass2 commands.
- OpenClaw Agent / skills: unchanged.
- Wake phrase and MultiNet acceptance: unchanged.
- Wake PGA remains 6 dB.

Expected boot line:

```text
=== HomeAIAgent A1R25B1 / Glass2 Local Voice + Wake Front-End 6dB / Wake=你好逐光 ===
```

For wake A/B details, the previous `docs/WAKE_FRONTEND_A1R25B0.md` remains the reference.

