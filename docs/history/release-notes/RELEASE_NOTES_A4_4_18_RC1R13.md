# HomeAIAgent A4.4.18 RC1R13

## Scope
Gateway-only change. StickS3 firmware remains RC1R9 and does not need reflashing.

## Root fix
RC1R12 used SSH to reach the OpenClaw host and execute the remote CLI for ephemeral Info-session deletion. That coupled HomeAIAgent to a specific machine/user/path.

RC1R13 removes the SSH/remote-CLI cleanup path completely.

Info Skill cleanup now uses the OpenClaw Gateway WebSocket control plane directly:

- each game/finance request still omits `user`
- each request still gets a unique `x-openclaw-session-key`
- after the request, Gateway opens the configured OpenClaw Gateway WS endpoint
- authenticates as an operator backend client
- calls `sessions.delete` with `operator.admin`
- deletes only the exact `agent:main:homeai-info-*` session key

The WS endpoint defaults to the same host/port as `OPENCLAW_BASE_URL`, with `http→ws` and `https→wss`. It can be overridden with `OPENCLAW_GATEWAY_WS_URL`.

Moving OpenClaw to another machine therefore requires only changing the normal OpenClaw endpoint/token configuration. No SSH user, SSH host, remote shell, or remote CLI path is stored in HomeAIAgent.

## Expected logs

```text
[CFG] info_skill_cleanup=gateway-rpc ... method=sessions.delete scope=operator.admin ssh=disabled
[INFO-CLEANUP] category=game ... status=deleted transport=gateway-rpc
[INFO-CLEANUP] category=finance ... status=deleted transport=gateway-rpc
```

## Frozen behavior preserved
Refresh schedule, last-good isolation, snapshots, Gold 300s refresh, Glass2 UI, 01:05-09:00 screen protection, voice chain, and StickS3 firmware are unchanged.
