# 2026-09-07 Submission Clean

- No functional behavior change from A1R10.
- Cleaned project-local runtime/debug artifacts before submission.
- Extended `.gitignore` so transient audio captures, transcripts, notification/listener state, caches, snapshots, traces and PID files are not accidentally committed.
- Added `tools/clean_submission_artifacts.sh` for repeatable repository cleanup; it never touches `.pio-local`, `~/.config/HomeAIAgent/gateway.env`, or `~/.local/share/HomeAIAgent`.
- Verified the local offline PlatformIO platform paths remain pinned.

# A4.6 Notification A1R10 WebSocket Reconnect Wake Recovery

- Root-fixed a device state-machine deadlock after Companion WebSocket transport drops: A1R9 paused the local wake recognizer and set `CompanionState::Error` on disconnect, but reconnect never cleared that transport-originated Error, so wake listening could never re-arm despite successful `device.hello`, Info sync, and display traffic.
- Added a dedicated `gatewayTransportFault` latch. Only transport-originated Error state is cleared on successful reconnect; unrelated functional Error states are not masked.
- Successful transport recovery now logs `[WS-RECOVER] transport restored -> idle; wake re-arm pending`, after which the existing canonical wake-mic restart path re-arms MultiNet on the next loop.
- Gateway now catches normal/ungraceful WebSocket `ConnectionClosed` events inside the handler and logs a concise close line instead of emitting a server traceback while StickS3 reconnects.
- A1R9 listening improvements remain intact: wake-listen PGA 9 dB, command capture 6 dB, 5 s speech-start window, existing feed-stall recovery, and WAKE-HEALTH / WAKE-HIT diagnostics.
- Frozen wake phrase `逐光逐光`, MultiNet default threshold, TTS/Notification, 20 s capture, Gapless playback, Glass2, Info Skill, Gold, night policy, 24 FPS, Brownout protection and Wi-Fi TX 10 dBm remain unchanged.
- Verified local offline PlatformIO paths remain pinned; `.pio-local` is not deleted or overwritten.

# A4.6 Notification A1R9 Listening Reliability

- Improved idle wake-word front-end sensitivity without changing the frozen MultiNet command threshold or the `逐光逐光` phrase.
- Wake-listen ES8311 analog PGA is now 9 dB (+3 dB from the 6 dB command-capture baseline). Once a wake hit transitions into command capture, the microphone is explicitly restored to the validated 6 dB capture gain.
- Extended the post-wake speech-start window from 3.5 s to 5.0 s; end-of-speech silence, VAD threshold logic, 20 s maximum capture, and A3R9 wake acknowledgement behavior are unchanged.
- Added low-rate `[WAKE-HEALTH]` feed-readiness/alive diagnostics plus `[WAKE-HIT]` context and richer no-speech/stall diagnostics so future missed reactions can be separated into wake miss, dead feed, no-speech, and ASR stages.
- Existing Mic -> ESP-SR feed-stall self-recovery is retained unchanged.
- TTS standard voice, Notification, Gapless playback, Glass2 layout/newline support, Info Skill, Gold, 01:05-09:00 policy, 24 FPS Speaking animation, Brownout protection, and Wi-Fi TX 10 dBm are unchanged.
- PlatformIO remains pinned to the verified local offline platform paths; `.pio-local` is not removed or overwritten.

# A4.6 Notification A1R8 Explicit Headline Newline Support

- Glass2 Info headline rendering now preserves explicit line breaks supplied by OpenClaw as hard row breaks.
- Text without explicit line breaks keeps the existing width-based automatic wrapping.
- Rendering remains capped at the frozen three-line 11 px headline area; overflow still receives an ellipsis.
- Only Gateway-side 128x64 bitmap layout logic changed. StickS3 firmware, Glass2 font sizes/coordinates, 10 s rotation, Notification/TTS, 20 s capture, wake behavior, Info Skill transport, Gold, and night policy are unchanged.
- Verified local offline PlatformIO paths and `.pio-local` are preserved unchanged.

# A4.6 Notification A1R7 Standard Voice Startup Guard

- Root fix for stale persistent TTS configuration: every Gateway start now converges `gateway.env` to the verified standard pairing instead of trusting a one-time migration marker.
- Standard pairing locked for A1R7: `seed-tts-2.0` + `zh_female_vv_uranus_bigtts`.
- Added shell + Python startup guards so a stale `ICL_zh_female_tiaopigongzhu_tob` process/config cannot silently start.
- No StickS3 voice-capture, wake-word, VAD, playback, Gapless TTS, Glass2, Notification session, or 20-second capture logic changed.
- `platformio.ini` now uses the verified local offline PlatformIO platform paths on yemol's Mac mini; no GitHub platform ZIP is referenced.
- `.pio-local` is untouched.

## A4.6 Notification A1R6 - Standard voice restore (2026-09-07)

- Restored the previously verified standard Volcengine TTS pairing: `seed-tts-2.0` + `zh_female_vv_uranus_bigtts`.
- Added a new one-time persistent-config migration so systems already migrated by A1R5 are corrected automatically on next Gateway start.
- Migration backs up `gateway.env` and preserves API keys, OpenClaw tokens, tunnel settings, notification settings, and all unrelated configuration.
- Gateway-only change. StickS3 firmware, 20 s capture, wake word, Gapless TTS playback, Glass2, Info Skill, Gold, night policy, and `.pio-local` are unchanged.

# HomeAIAgent A4.6 Notification A1R5

- Corrected the A1R3/A1R4 regression that paired the public TTS 2.0 “调皮公主” voice with the voice-cloning resource.
- Official public voice pairing is now `seed-tts-2.0` + `ICL_zh_female_tiaopigongzhu_tob`; the `ICL_` speaker prefix does not mean the request must use `seed-icl-2.0`.
- Added a new one-time persistent-config migration so machines that already ran A1R3/A1R4 are forced back to `seed-tts-2.0` without touching API keys or OpenClaw tokens.
- Preserved A1R4 20 s command capture with 21 s PSRAM headroom, Notification Session Listener, no-prefix reminders, 24 FPS, wake behavior, Glass2, Info Skill and Gold.
- TTS errors now print the active resource and speaker directly.

# HomeAIAgent A4.6 Notification A1R4

- Voice command maximum capture duration increased from 10 s to 20 s for both wake-word hands-free capture and button PTT.
- PSRAM capture buffer now derives from the logical maximum plus 1 s safety headroom, preventing the historical exact-capacity overflow edge case.
- Hands-free maximum now derives from `AUDIO_MAX_PTT_MS` so manual and wake-word capture cannot drift apart.
- Gateway, Notification Session Listener, TTS/ASR configuration, Info Skill, Gold, Glass2, wake recognition behavior, and 24 FPS expression rendering are unchanged.

# A4.6 Notification A1R3

- Corrected Volcengine resource/speaker pairing for “调皮公主”: `seed-icl-2.0` + `ICL_zh_female_tiaopigongzhu_tob`.
- Keeps the existing V3 unidirectional WebSocket streaming endpoint and request framing.
- Added one-time persistent-config migration that updates BOTH `VOLCENGINE_TTS_RESOURCE_ID` and `VOLCENGINE_TTS_VOICE`, including machines that already ran A1R2.
- TTS errors now include the active resource and speaker for direct diagnosis.
- Notification listener and no-prefix reminder playback retained.
- StickS3 firmware, wake path, 24 FPS expression animation, Glass2, Info Skill, Gold and frozen subsystems unchanged.

# A4.6 Notification A1R2

- Fixed Volcengine TTS speaker/resource mismatch for “调皮公主”.
- Use official TTS 2.0 Speaker ID `ICL_zh_female_tiaopigongzhu_tob` with existing `seed-tts-2.0` V3 streaming resource.
- Added one-time persistent-config migration from the incorrect `saturn_zh_female_tiaopigongzhu_tobsvg` value to the official Speaker ID.
- Reminder prefix remains removed; Notification Session Listener remains unchanged.
- StickS3 firmware and frozen device behavior unchanged.

# A4.6 Notification A1R1 (2026-09-07)

- Reminder TTS no longer prepends `提醒你，`; OpenClaw final assistant text is spoken verbatim (trimmed only).
- Default Volcengine TTS voice changed to `ICL_zh_female_tiaopigongzhu_tob` (调皮公主).
- Added one-shot persistent config migration so existing Mac mini `gateway.env` adopts the new voice without touching API keys/tokens.
- StickS3 firmware, 24 FPS, wake path, Glass2, Info Skill, Gold, and notification session-listener transport unchanged.

# Changelog

## 2026-09-07 — A4.6 Notification A1 — OpenClaw Session Listener

- Replaced the withdrawn webhook/reverse-port design with an outbound OpenClaw Gateway WebSocket listener over the existing `127.0.0.1:18790` SSH/Tailscale tunnel.
- Subscribes to the exact OpenAI-compatible voice session key derived from `OPENCLAW_USER`: `agent:<agentId>:openai-user:<user>`.
- Uses `sessions.messages.subscribe` plus `sessions.subscribe` invalidation and authoritative bounded `chat.history` reconciliation.
- Added durable listener cursor state so reconnects catch missed async assistant output without replaying historical messages.
- Added synchronous voice-reply suppression so normal request/response TTS is not duplicated by the session listener.
- Kept the durable local notification queue, existing Info-frame Glass2 overlay, Gapless TTS delivery path and `playback.done` end-to-end acknowledgement.
- Added `gateway/check_notification_listener.py`; removed webhook token/setup/test artifacts and the dedicated 8766 HTTP ingress.
- Preserved A3R9 24 FPS/wake behavior, Info Skill/Gold schedule, voice session continuity, Brownout protection and `.pio-local` rules.


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
