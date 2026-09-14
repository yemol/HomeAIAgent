#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-shot manual HomeAI Info refresh using the running Gateway's OpenClaw tunnel.

This utility intentionally does not start a second embedded SSH tunnel. Keep the
normal HomeAIAgent Gateway running so 127.0.0.1:18790 is available, run this
utility from a second terminal, then restart the normal Gateway so it reloads
and pushes the freshly persisted Info cache to connected devices.
"""
from __future__ import annotations

import asyncio
from datetime import datetime


import companion_gateway as g


async def _check_openclaw() -> None:
    headers = {}
    if g.OPENCLAW_TOKEN:
        headers["Authorization"] = f"Bearer {g.OPENCLAW_TOKEN}"
    async with g._openclaw_http_client(10) as client:
        response = await client.get(f"{g.OPENCLAW_BASE_URL}/v1/models", headers=headers)
        response.raise_for_status()


async def main() -> int:
    g.load_info_skill_cache()

    try:
        await _check_openclaw()
    except Exception as exc:
        print(
            "[FORCE-INFO-FAIL] OpenClaw tunnel is not reachable. "
            "Keep ./run_full.sh running in another terminal first."
        )
        print(f"[FORCE-INFO-FAIL] {type(exc).__name__}: {exc}")
        return 3

    started = datetime.now(g._info_schedule_tz())
    slot_label = f"manual-{started.strftime('%Y-%m-%dT%H:%M:%S')}"
    g._begin_startup_info_snapshot(
        started,
        trigger="manual",
        slot_label=slot_label,
    )

    try:
        changed = await g.refresh_info_from_skill(
            force=True,
            slot_label=slot_label,
        )
        failed = list(g.LAST_INFO_REFRESH_DIAGNOSTIC.get("failed") or [])
        succeeded = list(g.LAST_INFO_REFRESH_DIAGNOSTIC.get("succeeded") or [])

        if failed and succeeded:
            status = "partial"
        elif failed and not succeeded:
            status = "last-good"
        else:
            status = "success"

        g._finish_startup_info_snapshot(
            started_at=started,
            status=status,
            trigger="manual",
            slot_label=slot_label,
        )

        print(
            f"[FORCE-INFO] status={status} changed={changed} "
            f"succeeded={','.join(succeeded) or '-'} "
            f"failed={','.join(failed) or '-'} "
            f"count={len(g.FEED_ITEMS)} revision={g.FEED_REVISION}"
        )
        print(f"[FORCE-INFO] cache={g.INFO_SKILL_CACHE_FILE}")
        if failed:
            print("[FORCE-INFO-WARN] Some categories kept last-good data.")
            return 2

        print(
            "[FORCE-INFO] Refresh complete. Restart the normal Gateway once "
            "to reload and push the new cache to Glass2."
        )
        return 0

    except asyncio.CancelledError:
        g._finish_startup_info_snapshot(
            started_at=started,
            status="cancelled",
            error="asyncio.CancelledError",
            trigger="manual",
            slot_label=slot_label,
        )
        raise
    except Exception as exc:
        g._finish_startup_info_snapshot(
            started_at=started,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
            trigger="manual",
            slot_label=slot_label,
        )
        print(f"[FORCE-INFO-FAIL] {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
