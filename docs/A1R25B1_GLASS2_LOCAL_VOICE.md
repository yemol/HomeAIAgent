# A1R25B1 Glass2 Local Voice Control

## Commands

- `关闭屏幕`: Glass2 off
- `打开屏幕`: Glass2 on

These are local Gateway commands after ASR. They do not call OpenClaw.

## Protocol

Gateway to StickS3:
- `glass2.sleep`
- `glass2.wake`

StickS3 to Gateway:
- `glass2.ack`

Each command uses a unique `command_id` and the Gateway confirms the returned visibility state.

## Scope

This controls Glass2 only. The StickS3 interaction display and the existing automatic night display policy remain separate. Manual Glass2 on/off takes precedence for Glass2 until the opposite manual command or reboot.
