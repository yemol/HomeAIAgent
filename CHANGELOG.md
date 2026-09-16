# KitchenTerminal A3.0d VOICE1.1 (2026-09-14)

- Fix real ASR forms `小 k ...` / `小 K ...` not matching the `小K` KitchenTerminal namespace.
- Namespace parser now accepts optional whitespace between `小` and `K/k`, including full-width K forms.
- The exact logged phrases `小 k 获取今日菜单。`, `小 K 显示今日菜单。`, and `小 K 获取今日菜单。` are covered by the local fast-path self-test.
- `小K获取今日菜单` now reaches `_kitchen_show_today_menu()` directly, which reloads today's menu source and broadcasts a fresh `kitchen.show_menu` payload to the connected iPad.
- No OpenClaw round-trip is used for this deterministic command.
- StickS3 firmware, NetworkSpeaker, HTTPS/Tailscale microphone path, timers and recipe-progress logic are unchanged.

# KitchenTerminal A3.0d VOICE1 (2026-09-14)

- `小K` is now the primary external voice-command namespace.
- Example: `小K获取今日菜单`.
- `获取` is added as a deterministic local synonym for loading today's menu.
- Legacy `厨房` prefix remains accepted for backward compatibility.
- KitchenTerminal's own iPad microphone may still omit the prefix.
- Spoken confirmations now refer to the terminal as `小K`.
- Internal protocol names, device IDs, `/kitchen` routes, timers, recipe progress, HTTPS microphone path, OpenClaw transport, StickS3 firmware and NetworkSpeaker remain unchanged.

# KitchenTerminal A3.0c UI1 (2026-09-14)

- User-visible KitchenTerminal name changed to `小K`.
- Added a single top safe-area at the outer `#app` container:
  `calc(env(safe-area-inset-top, 0px) + 18px)`.
- Internal KitchenTerminal protocol/device IDs and voice command namespace remain unchanged.
- Removed macOS archive metadata and added a root `.gitignore`.
- No StickS3 firmware, OpenClaw, NetworkSpeaker, timer, recipe-progress, microphone, TTS, HTTPS/Tailscale, or kitchen-control behavior changed.

# A1R25B1.3 SR Rollback Stable (2026-09-14)

- Restore the device runtime to the user's latest known-working project baseline.
- Remove only the persisted A1R25B2/B2.1 low-level ESP-SR probability-export experiment from `.pio-local` during the next build.
- Keep the existing A1R25A.1 `[WAKE-MN]` observe-only probability logging.
- No Wake Guard is enabled in this rollback build.
- Wake phrase remains `你好逐光`.
- Wake PGA remains 6 dB.
- Glass2 active brightness remains 96.
- Latest Gateway/OpenClaw/NetworkSpeaker code from the user's uploaded project is preserved unchanged.

# A1R25B1.1 Glass2 Half Brightness

- Glass2 active brightness changed from 255 to 128 (about 50%).
- Screen off remains 0.
- No Gateway/OpenClaw/NetworkSpeaker changes.
- Wake PGA remains 6 dB.

# A1R25B1 - Glass2 Local Voice Control (2026-09-13)

- Added local voice commands `关闭屏幕` and `打开屏幕` for Glass2 only.
- Added Gateway-local intent handling, so recognized screen commands do not enter OpenClaw.
- Added ACK-confirmed `glass2.sleep` / `glass2.wake` device protocol.
- Added independent Glass2 manual visibility override; StickS3 display behavior is unchanged.
- Manual Glass2 on/off overrides automatic night blanking for Glass2 until the opposite command or device reboot.
- Preserved A1R25B0 wake experiment unchanged: `你好逐光`, MultiNet acceptance, wake PGA 6 dB.

# A1R25B0 - Wake Front-End A/B (2026-09-13)

- Keep wake phrase `你好逐光 / ni hao zhu guang`.
- Change wake-listening analog PGA from 9 dB to 6 dB to test whether clipping is reducing MultiNet recall.
- Keep command-capture PGA at 6 dB.
- Keep MultiNet default threshold and wake acceptance behavior unchanged.
- Reset Wake Observatory segment/ring state whenever wake listening is paused/restarted, preventing stale multi-turn speech segments such as the observed 16-second diagnostic burst.
- Keep A-button manual PTT, local `在的`, Gateway, OpenClaw, TTS, Glass2, NetworkSpeaker and Wi-Fi behavior unchanged.
- Gateway source remains unchanged.

# A1R25A.1 - Wake Evidence Refinement (2026-09-13)

- Keep wake phrase `你好逐光 / ni hao zhu guang`.
- Keep MultiNet threshold, wake PGA 9 dB, capture PGA 6 dB and wake acceptance behavior unchanged.
- Fix observatory log newlines so each record is parseable on its own line.
- Add `[WAKE-EVENT] id=...` and carry the same ID into `[WAKE-OBS]` and `[WAKE-HIT]`.
- Replace whole-window candidate statistics with current/recent speech-segment statistics.
- Merge short pauses up to 240 ms inside one observed speech segment.
- Preserve A-button manual PTT behavior and add `[WAKE-MISS-SUSPECT]` diagnostics when manual PTT follows recent unmatched speech.
- Gateway source remains unchanged.

# A1R25A - Wake Observatory (2026-09-13)

- Keep wake phrase `你好逐光 / ni hao zhu guang`.
- Keep current MultiNet default threshold and wake acceptance behavior unchanged.
- Add two-second compact acoustic observation ring for wake diagnostics.
- Add `[WAKE-OBS]` accepted-hit summaries: noise floor, threshold, level/RMS/peak, active ratio, speech-run timing and clipping.
- Add `[WAKE-OBS-SPEECH]` speech-burst logs so failed wake attempts can be observed without changing recognition.
- Add optional `.pio-local` MultiNet probability logging `[WAKE-MN]`; if the local wrapper anchor differs, build continues with app-level observability instead of failing.

# Changelog

## 2026-09-13 — A1R24 Clean Source Package

- Cleaned duplicated/outdated README and deployment notes around the current A1R24 baseline.
- Removed project-local Gateway runtime captures and Mac archive metadata from the source package.
- Added a current `.gitignore` and strengthened `tools/clean_submission_artifacts.sh`.
- Simplified version-history comments in active source without changing runtime logic.
- Updated the firmware boot banner to `HomeAIAgent A1R24 / Wake=你好逐光`.
- Frozen wake ACK WAV/PCM, `srmodels.bin`, partition table and runtime behavior remain unchanged.

## 2026-09-13 — A1R24 Wake Phrase Test: 你好逐光

- Changed the local Chinese MultiNet command phrase from `逐光逐光` to `你好逐光`.
- Pinyin command changed from `zhu guang zhu guang` to `ni hao zhu guang`.
- This project uses command-only Chinese MultiNet (`mn5q8_cn`) with runtime `sr_cmd_t` commands, so no new WakeNet model is required for this phrase change.
- Kept the existing MultiNet model, default threshold, wake-listen PGA 9 dB, command-capture PGA 6 dB, local `在的` acknowledgement, PTT/VAD, Gateway, TTS, Glass2, Info and all other behavior unchanged.
- This is an A/B test build intended to measure missed wakes and false wakes before any threshold or confirmation logic is changed.

## 2026-09-12 — A1R23 Capability Guard / Display ACK noise fix

- Fixed HomeAIAgent Mini receiving primary Glass2 display-policy commands.
- `display` screen dimensions no longer imply support for the HomeAIAgent
  `display.sleep` / `display.wake` / `display.ack` protocol.
- Added explicit `display_policy` and `info_feed` capability gates.
- Current primary HomeAIAgent remains backward-compatible and continues to
  receive display policy + Info feed without firmware changes.
- HomeAIAgent Mini now receives neither service unless it explicitly opts in.
- Benign duplicate final `display.ack` packets are recognized and ignored
  instead of logged as stale warnings.
- Multi-device OpenClaw session routing and NetworkSpeaker routing from A1R22
  are unchanged.


## 2026-09-12 — A1R22 Multi-Device Session Router

- Added stable per-device OpenClaw conversation routing.
- Current primary StickS3 remains on `OPENCLAW_USER=home-ai-agent:main`.
- HomeAIAgent Mini `homeai-mini-bedroom-01` now uses `home-ai-agent-mini:main`.
- Unknown future companion ids are isolated automatically by default.
- OpenClaw Notification listener/suppression remains pinned to the primary
  HomeAIAgent conversation only.
- Reminder delivery now targets the primary companion instead of whichever
  WebSocket client connected most recently.
- Added speaker-role clients with `parent_device_id` binding and priority-based
  audio-sink resolution.
- Multiple NetworkSpeaker clients can coexist and bind to different companions.
- Planned Network Speaker Dock binds only to `homeai-mini-bedroom-01`.
- Display policy and Info sync no longer target speaker-only clients.
- Existing voice/TTS transport remains the fallback when no bound speaker is online.

# A1R21 - Gateway Voice-Turn Ordering Fix

- Added a transcript-native Voice Turn Fence around synchronous `/v1/chat/completions` voice turns.
- When OpenClaw writes several assistant progress rows before the exact final synchronous reply, Gateway now marks all assistant rows between the nearest preceding user row and that final reply as consumed. They never enter the Notification queue.
- If the final reply has not yet been committed to `chat.history`, reconciliation is deferred rather than prematurely queueing progress rows.
- Assistant messages before the voice-turn user row and genuinely later assistant messages remain eligible for normal asynchronous notification delivery.
- Reconnect/history replay is safe because consumed progress/final rows are written into the existing seen-message cursor.
- Added offline regression coverage for progress-row suppression, pre-turn async preservation, post-turn async preservation, delayed final-row commit, and reconnect replay.
- Firmware, wake recognition, local `在的` asset, TTS volume, 15-second Info hold, Info cache-only startup, embedded SSH transport, and all A1R20 frozen behavior are unchanged.

# A1R20 Submission Clean — Frozen wake ACK asset

- Bundled the user-approved production-TTS `assets/wake_ack_zaide_tts.wav` directly in the submission.
- Bundled the matching `include/wake_ack_voice_pcm.h`; ordinary builds no longer require a one-time cloud TTS generation step.
- Updated `.gitignore` and `tools/clean_submission_artifacts.sh` so the approved wake asset is preserved while ordinary runtime WAV/log/cache artifacts are still removed.
- `generate_wake_ack_tts.sh` remains available only as an optional maintenance tool for an intentional future voice replacement.
- No change to Gateway behavior, embedded SSH transport, Info schedule/cache policy, wake recognizer, audio gains, normal TTS voice, or display behavior.

# A1R20

- Fixed A1R19 wake-ACK TTS asset compile failure: generated header omitted `kWakeAckVoiceDurationMs`.
- `main.cpp` now derives wake-ACK duration directly from sample count/rate, removing runtime dependence on that generated constant.
- Generator now also emits `kWakeAckVoiceDurationMs` for completeness.
- No changes to wake model, ASR/TTS voice, audio gain, Gateway transport, Info schedule, or display behavior.

## A1R19 - Production-TTS local wake acknowledgement

- Replaced the hand-processed/mechanical `在的` asset workflow with a one-time generator using the same frozen production voice as normal answers: `seed-tts-2.0 + zh_female_vv_uranus_bigtts`.
- Added root `./generate_wake_ack_tts.sh` and `gateway/generate_wake_ack_tts.py`. The generator reads the existing persistent `~/.config/HomeAIAgent/gateway.env`; API credentials are never copied into the repository or firmware.
- Synthesizes `在的。`, preserves natural TTS timing, uses no dynamic compression, keeps 35 ms pre-roll and 180 ms post-roll, and applies only linear peak/RMS normalization plus tiny anti-click fades.
- Generates both `assets/wake_ack_zaide_tts.wav` for Mac preview and `include/wake_ack_voice_pcm.h` for fully local StickS3 playback.
- A1R19 deliberately refuses to compile until the one-time TTS asset has been generated, preventing accidental fallback to the older mechanical PCM.
- Runtime wake playback remains local-only at dedicated MAG6; normal assistant TTS remains MAG5. No wake-time cloud request or token use is introduced.


## A1R18 - Full-length clean local wake acknowledgement

- Restored the complete natural-duration local `在的` PCM (about 632 ms).
- Removed A1R17 hard compression and aggressive tail trimming which caused audible breakup.
- Kept dedicated wake acknowledgement playback at MAG6 for immediate audibility.
- Normal assistant TTS remains MAG5.
- Gateway, OpenClaw transport, Info scheduling/cache policy, notifications, wake recognizer behavior, and all other device logic remain unchanged.
## A1R17 - 2026-09-08 - Wake ACK loudness master

- Kept normal assistant TTS at `AUDIO_SPEAKER_VOLUME=255`, channel volume 255, `MAG5`.
- Raised only the brief local wake acknowledgement "在的" to dedicated `MAG6`.
- Re-mastered the embedded wake PCM with compression + limiter:
  - duration: 632 ms -> 507 ms
  - peak: -1.82 dBFS -> -0.45 dBFS
  - active RMS: -18.70 dBFS -> -12.00 dBFS
- Removed excess pre/post silence so successful wake acknowledgement starts sooner.
- No Gateway/OpenClaw/ASR/TTS/network behavior changes.

# A1R16 - Wake ACK + answer volume lift (2026-09-08)

- Normal assistant playback speaker magnification: MAG4 -> MAG5.
- Wake acknowledgement now uses the exact same speaker rail as normal TTS: master 255, channel 255, MAG5.
- Embedded `在的` PCM normalized by 1.8x (+5.1 dB); source peak remains below full scale, so no sample clipping is introduced.
- No Gateway/OpenClaw/Info/Notification behavior changes.

## A1R15 — Local Wake Voice ACK “在的” (2026-09-08)

- Restored the immediate local acknowledgement after a successful `逐光逐光` wake hit.
- Replaced the previous two-note wake confirmation with an embedded 16 kHz mono PCM voice prompt: `在的`.
- The acknowledgement is fully local on StickS3: no Gateway, OpenClaw, ASR, TTS request, token usage, or network round trip.
- Playback completes before Mic restart so the command microphone does not capture the device's own acknowledgement.
- Added a short fallback tone if local `playRaw()` unexpectedly fails, so a successful wake is never silent.
- Wake phrase, MultiNet default threshold, wake-listen PGA 9 dB, command-capture PGA 6 dB, 5 s post-wake speech-start window, A1R10 reconnect recovery, A1R12 Info cache-only startup / 15 s hold, and A1R14 PlatformIO behavior remain unchanged.


## A1R14 — Reuse Installed PIO Tools (2026-09-08)

- Fix A1R13 `UnknownPackageError` caused by treating pioarduino tool packages as normal registry packages.
- Removed explicit registry-style pins for `toolchain-xtensa-esp-elf` and `tool-esptoolpy`.
- The wake environment continues to use the local pioarduino platform and project-local Arduino framework packages.
- PlatformIO now reuses the tool packages already installed under the Mac mini global package cache.
- No firmware logic, Gateway logic, Info behavior, TTS/ASR behavior, wake behavior, or 15 s Info hold timing changed.
# A1R13 - Pinned Local PlatformIO Tool Packages (2026-09-08)

- Pinned `toolchain-xtensa-esp-elf@14.2.0+20251107` and `tool-esptoolpy@5.1.2`, matching the versions just installed successfully on the Mac mini.
- Keeps the verified local pioarduino platform symlink and the preserved `.pio-local` Arduino framework sources unchanged.
- No HomeAIAgent runtime, Gateway, wake, ASR/TTS, Notification, Info, Gold, display layout, or 15 s hold behavior changes.
- Device firmware source is unchanged from A1R12; this package only makes PlatformIO tool resolution deterministic for the current Mac mini installation.

# A1R12 - Info Startup Cache-Only + 15s Hold (2026-09-08)

- Gateway restart no longer triggers an immediate OpenClaw Info Skill refresh; startup serves the persisted last-good cache only.
- Fixed Info refresh slots remain `09:00,11:00,13:00,15:00,17:00,19:00,21:00,23:00,01:00` in `Asia/Taipei`.
- Glass2 per-item information hold increased from 10 s to 15 s.
- No changes to wake phrase, MultiNet threshold, ASR/TTS, Notification, 20 s capture, Gapless playback, Gold, night policy, Brownout protection, or Wi-Fi TX power.
- Local offline PlatformIO sources remain unchanged.

# A4.6 Notification A1R11 Managed OpenClaw Transport

- Replaced the separately started `ssh -N -L` / `openclaw_air_tunnel.sh` normal runtime with an in-process AsyncSSH local-forward manager owned by `companion_gateway.py`.
- `./run_full.sh` is now the only service command required on the Mac mini. It no longer requires an external tunnel or a separate `--check` process before launch.
- Added bounded SSH connect/reconnect with keepalive and exponential retry. OpenClaw transport loss does not terminate the StickS3-facing HomeAIAgent server.
- Startup can enter a degraded mode with last-good Info cache while SSH reconnects; scheduled Info refreshes skip cleanly while the transport is unavailable.
- Notification listener waits for managed transport readiness instead of generating repeated connection errors.
- Added `OPENCLAW_TRANSPORT=embedded_ssh`; future same-host deployment may use `direct` without changing the StickS3 protocol.
- Pinned `asyncssh==2.14.2` because the current persistent Gateway runtime uses Python 3.9.
- A1R10 StickS3 firmware, wake reliability, Notification/TTS, explicit headline newlines, Info/Gold behavior, local offline PlatformIO paths and `.pio-local` policy remain unchanged. No firmware flash is required for A1R11.

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
