# HomeAIAgent P0 A4.4.18 RC1R11

## Info Skill ephemeral session + immediate cleanup

RC1R11 is based directly on RC1R10 and changes only the Gateway Info Skill request/session lifecycle.

### Why
RC1R10 correctly stopped sending a fixed `user`, so refresh context no longer accumulates. However OpenClaw still creates a new persisted session for each otherwise-stateless HTTP call. RC1R11 makes that transient session addressable and deletes it immediately after each request.

### Fix
- Info Skill request body still omits `user`.
- Each game/finance HTTP request gets a unique explicit header:
  `x-openclaw-session-key: agent:main:homeai-info-<category>-<uuid>`
- Exactly one HTTP request is made per transient session key. Category-level retry remains the retry authority.
- After the response completes, Gateway runs the equivalent of:
  `openclaw sessions delete <exact-key> --yes --json`
  on the OpenClaw MacBook Air over the existing SSH/Tailscale path.
- Cleanup runs in `finally`, including HTTP/JSON failure paths.
- Cleanup failure is diagnostic-only and never converts a valid feed into a failed refresh.
- A strict namespace guard refuses to delete any key outside `agent:<agent>:homeai-info-*` and explicitly protects the normal main session.
- Snapshot files record the exact transient session and cleanup result.

### Expected logs

`[INFO-SKILL] category=game attempt=1 openclaw_session=ephemeral key=agent:main:homeai-info-game-... user=omitted`

`[INFO-CLEANUP] category=game attempt=1 session=agent:main:homeai-info-game-... status=deleted`

### Important OpenClaw behavior
OpenClaw's supported `sessions delete` removes the active session row/runtime state and performs transcript cleanup. Ordinary sessions may still leave OpenClaw's verified `.jsonl.deleted.<timestamp>` archive according to upstream lifecycle behavior. RC1R11 does not directly delete OpenClaw-owned archive files.

### Unchanged
- StickS3 firmware remains RC1R9. No reflash required.
- Normal voice conversation continues using `OPENCLAW_USER=home-ai-agent:main`.
- Info refresh wall-clock schedule.
- game/finance last-good isolation.
- Info snapshots.
- Gold 300 s refresh.
- Glass2 UI and night policy.
- A3.9 gapless TTS.
