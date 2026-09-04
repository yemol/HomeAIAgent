# HomeAIAgent A4.1.2

## Firmware compile root cause

A4.1/A4.1.1 generated `main.cpp` accidentally removed the global declaration
block immediately after `initFallbackInfoItems()`.

That block contains, among others:

- Glass2 state globals
- mic/PTT buffers
- `TtsSlot` and A3.9 gapless TTS state
- diagnostic checkpoint declarations
- `CompanionState` state
- WebSocket/Wi-Fi state

Once that block disappeared, the compiler produced a cascade of
`was not declared in this scope` errors.

A4.1.2 does NOT patch those errors individually.
It rebuilds `main.cpp` from the known-good A4.0.1 firmware baseline and applies
only three surgical changes:

1. `INFO_MAX_ITEMS`: 12 -> 20
2. `INFO_HOLD_MS`: 5000 -> 10000
3. fallback content -> one neutral waiting item

The complete original global declaration block is retained.

## Info Skill schedule

Local timezone default:

```text
Asia/Taipei
```

Fetch times:

```text
00:00
01:00
09:00
10:00
11:00
...
23:00
```

So after the 01:00 fetch, the next fetch is exactly 09:00.

The Skill's `next_refresh_after_sec` remains protocol-compatible but no longer
controls scheduling.

## Installation

Use this full package as the replacement project.

- Normal PlatformIO Upload
- DO NOT erase flash
- NVS remains intact
- Restart Gateway

A3.9 Gapless TTS and A4.0.3 accepted Glass2 font/layout remain frozen.
