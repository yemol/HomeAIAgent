# HomeAIAgent P0 A4.4.18 RC1R10

## Info Skill stateless OpenClaw session hotfix

RC1R10 is based directly on RC1R9 and changes only the Gateway Info Skill request path.

### Root cause
Background game/finance requests used the fixed OpenAI-compatible user:

`home-ai-agent:info-feed`

OpenClaw therefore reused one long-lived session and accumulated refresh history until latency/context failures appeared as ReadTimeout / HTTP 500.

### Fix
- Removed `OPENCLAW_INFO_USER`.
- `_openclaw_background_json()` no longer accepts or sends a `user` field.
- Game/finance refreshes are stateless at the OpenClaw request layer.
- Normal voice conversation still sends `OPENCLAW_USER=home-ai-agent:main`.
- Added outbound Info request snapshot: `<category>_<attempt>_openclaw_request.json`.
- Added explicit config/runtime logs showing `openclaw_session=stateless user=omitted`.
- Added `gateway/test_info_stateless_user.py`.

### Unchanged
- RC1R9 StickS3 firmware and 10 s capture/VAD diagnostics.
- Info refresh schedule.
- game/finance retry and last-good fallback.
- all-refresh snapshots.
- Gold refresh.
- Glass2 UI.
- voice conversation continuity.
- A3.9 gapless TTS.
- night display policy.

### Update requirement
Gateway only. No StickS3 reflash is required.

After replacing the Gateway files, restart with:

`./run_full.sh`

Then verify five consecutive real refresh rounds if practical. The Info request snapshot must not contain a `user` key.
