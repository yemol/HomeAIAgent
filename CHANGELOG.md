# Changelog

## 2026-09-06 — A4.5 Cyber Expression A3R9 (current frozen baseline)

- Kept the real-device accepted A3R7 ~24 FPS Speaking cadence (`42 ms/frame`).
- Withdrew the failed A3R8 two-stage RMS/repetition wake confirmation.
- Restored the A3R7/default MultiNet wake runtime behavior.
- Removed the A3R8 explicit candidate-threshold override.
- Added a safe pre-build migration that removes only the withdrawn A3R8 threshold injection from preserved `.pio-local` when present; `.pio-local` is never deleted or replaced.
- Gateway, Glass2, Gapless TTS, capture/VAD, display policy, Info Skill/Gold and Brownout behavior remain unchanged.

## 2026-09-06 — A4.5 Cyber Expression A3R8 (withdrawn experiment)

- Keeps the verified A3R7 24 FPS Speaking cadence unchanged.
- MultiNet becomes a mildly more permissive first-stage candidate detector (`0.84`).
- Adds local repeated-phrase acoustic confirmation before wake ACK/PTT.
- False candidates are rejected silently and never reach Gateway.
- Adds `[WAKE-CONFIRM] PASS/REJECT` diagnostics for real-device tuning.


## 2026-09-06 — A4.5 Cyber Expression A3R7 (24 FPS candidate)

- Raised only Speaking steady-state rendering from 20 FPS (`50 ms`) to ~24 FPS (`42 ms`) for real-device comparison.
- Preserved the accepted A3R6 PCM-synced CORE-origin waveform, Gapless TTS path, state transitions, wake/listening cadence and all Gateway/Glass2 behavior.
- No `.pio-local` content is included or modified.

## 2026-09-06 — A4.5 Cyber Expression A3R6 (20 FPS candidate)

- Raised only Speaking steady-state rendering from ~15 FPS (`67 ms`) to 20 FPS (`50 ms`) for real-device comparison.
- Preserved A3R5 PCM synchronization, waveform geometry, state morphs, transition cadences, all other frame caps, Gateway, Glass2 and gapless TTS behavior.

## 2026-09-06 — A4.5 Cyber Expression A3R5 (candidate)

- Raised Speaking steady-state rendering from 8 FPS to ~15 FPS while retaining the PCM-synced CORE-origin waveform.
- Shortened ordinary state morphs from 300 ms to 220 ms and changed the interpolation to smoothstep for continuous start/end velocity.
- Replaced fixed transition re-lock arcs with time-driven bridge arcs so motion continues through state handoff.
- Removed the state-entry outer-ring radius collapse/re-expand cycle that still read as a tiny pause on every `setState()`.
- Cross-faded outgoing and incoming state motion hints during the morph instead of suppressing target motion until the second half.
- Raised only bounded transition cadence (Wake ~24 FPS; ordinary morphs ~20-24 FPS); sustained Listening remains at the conservative ~6 FPS capture cap.
- Preserved the A2 flicker-free M5Canvas renderer, Gateway, Glass2 and gapless TTS queue structure.

## 2026-09-06 — A4.5 Cyber Expression A3R4 (candidate)

- Drove Speaking amplitude from the PCM currently being played by the gapless TTS queue.
- Added lightweight short-window voice-energy tracking without microphone loopback.
- Added recent voice-energy history so syllables originate at the CORE and propagate outward along the existing A3R3 wave geometry.
- Preserved the 8 FPS Speaking cap, A3R1 smooth transitions and A2 flicker-free renderer.

## 2026-09-06 — A4.5 Cyber Expression A3R3 (candidate)

- Reworked Speaking so the central CORE is the single visible voice origin.
- Replaced side-mounted equalizer clusters and emission fans with continuous outward travelling waves.
- Preserved A3R2 amplitude visibility, A3R1 smooth transitions and the 8 FPS steady-state TTS cap.

## 2026-09-06 — A4.5 Cyber Expression A3R2 (candidate)

- Enlarged Speaking waveform amplitude and visual mass for monitor-edge readability.
- Expanded the autonomous voice envelope while keeping the verified 8 FPS steady-state TTS cap.
- Added larger nested emission fans and stronger core/VISOR pulse during Speaking only.
- Preserved A3R1 smooth transitions and all non-Speaking state designs.

## 2026-09-06 — A4.5 Cyber Expression A3R1 (candidate)

- Smoothed state changes without altering the accepted A3 state designs.
- Added a bounded high-cadence transition window, then restores the original audio-safe steady-state caps.
- Added a common CORE/VISOR/orbit pose morph between states.
- Aligned the end of Wake with the normal Listening pose to remove the final visual snap.

## 2026-09-06 — A4.5 Cyber Expression A3 (candidate)

- Added a transient 560 ms Wake acquisition animation when Idle enters Listening.
- Made Listening visually inbound and Speaking visually outbound.
- Enriched Thinking with asymmetric compute lanes, sparse nodes and a scan needle.
- Reduced Idle motion and refresh for a quieter monitor-side presence.
- Preserved the verified A2 flicker-free canvas renderer and audio-safe frame caps.

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
