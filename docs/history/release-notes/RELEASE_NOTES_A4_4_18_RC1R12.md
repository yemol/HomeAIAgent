# HomeAIAgent P0 A4.4.18 RC1R12

## Scope

Gateway-only root fix based directly on RC1R11. StickS3 remains RC1R9 and is not changed.

## Real-device defect fixed

RC1R11 correctly created unique ephemeral Info sessions and omitted `user`, but cleanup failed on the Air with:

```text
[INFO-CLEANUP-WARN] ... rc=127 stderr='zsh:1: command not found: openclaw'
```

The SSH command ran in a non-interactive shell whose PATH did not include the user's OpenClaw installation.

## RC1R12 root fix

- Resolve the Air-side OpenClaw CLI once through `/bin/zsh -lic`, matching the user's normal login + interactive shell environment.
- Cache the resolved absolute CLI path for later cleanups.
- Execute `sessions delete` through the same login+interactive zsh environment so npm/nvm/bun Node shims remain usable.
- Fallback discovery covers common Homebrew, local, npm-global, bun, volta, pnpm, and nvm locations.
- Keep the existing strict `homeai-info-*` session-key safety gate.
- Keep cleanup best-effort so a cleanup failure cannot turn a valid 10+10 feed into a refresh failure.
- Invalidate the cached CLI path after remote exit 126/127 so the next request re-resolves it.

## Expected real-device evidence

First cleanup after Gateway start:

```text
[INFO-CLEANUP] remote_cli status=resolved path=/.../openclaw target=yuanxiang@100.105.66.46
```

Each Info request:

```text
[INFO-SKILL] category=game attempt=1 openclaw_session=ephemeral key=agent:main:homeai-info-... user=omitted
[INFO-CLEANUP] category=game attempt=1 session=agent:main:homeai-info-... status=deleted cli=/.../openclaw
```

There must be no `rc=127 command not found: openclaw`.
