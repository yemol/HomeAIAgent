# Changelog

This file is a concise history. Detailed historical notes remain in `docs/history/release-notes/`.

## 2026-09-06 — A4.5 Cyber Expression A2

- Replaced direct LCD clear/redraw with a full-screen RGB565 `M5Canvas` renderer.
- Kept the completed previous frame visible until the next frame is ready.
- Preserved the six-state cyber visual language and audio-safe frame caps.
- Real-device verification confirmed removal of whole-screen flicker.
- Cleaned repository comments/documentation for submission.

## 2026-09-06 — A4.5 Cyber Expression A1

- Replaced the previous digital-pet face with a cyber terminal identity.
- Added central AI core, segmented VISOR, incomplete data orbits and cardinal sensor marks.
- Added state-specific visuals for Idle, Listening, Thinking, Speaking, Success and Error.
- Preserved Gateway, Glass2 and audio behavior.

## 2026-09-05 — A4.4.18 RC1R13

- Replaced Info Skill SSH/remote-CLI session cleanup with OpenClaw Gateway WebSocket RPC `sessions.delete`.
- Kept one unique transient OpenClaw session per background Info request.
- Kept normal voice conversation continuity separate from Info refreshes.

## 2026-09-05 — A4.4.18 RC1R12

- Fixed OpenClaw CLI discovery for the previous SSH cleanup implementation.

## 2026-09-05 — A4.4.18 RC1R11

- Added unique ephemeral Info-session keys and immediate cleanup after each background request.

## 2026-09-05 — A4.4.18 RC1R10

- Removed the fixed Info Skill `user` field so game/finance background refreshes do not accumulate conversational context.

## 2026-09-05 — A4.4.18 RC1R9

- Set the device capture ceiling to 10 s with an 11 s PSRAM buffer.
- Added explicit PTT abort reasons and sparse VAD state diagnostics.

## 2026-09-05 — A4.4.18 RC1R8

- Fixed the PlatformIO/SCons partition-size gate callback signature.

## 2026-09-05 — A4.4.18 RC1R7

- Added a hard firmware-size gate for the fixed application partition.
- Reduced nonessential diagnostic footprint after an oversized image boot failure.

## 2026-09-05 — A4.4.18 RC1R6

- Added PTT trigger provenance for wake-word versus Button-A turns.
