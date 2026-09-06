#!/usr/bin/env python3
import asyncio
import json
import companion_gateway as gateway

async def one(category: str):
    body = await gateway.call_homeai_info_get_feed(
        category,
        max_items=gateway._category_limit(category),
    )
    items = gateway.normalize_skill_feed(
        body,
        expected_category=category,
        allow_empty=True,
    )
    return body, items

async def main() -> int:
    print("=== HomeAIAgent A4.4 split Info Skill check ===")
    total = 0
    for category in ("game", "finance"):
        try:
            body, items = await one(category)
        except Exception as exc:
            print(f"[FAIL] {category}: {type(exc).__name__}: {exc}")
            return 2

        protocol = body.get("protocol_version") or body.get("protocol")
        bad = [
            item for item in items
            if item.category != gateway._display_category(category)
        ]
        if bad:
            print(f"[FAIL] {category}: category contamination")
            return 3

        print(
            f"[OK] {category} protocol={protocol} "
            f"status={body.get('status')} items={len(items)}"
        )
        for index, item in enumerate(items, 1):
            print(f"  {index:02d}. {item.headline} | {item.source}")
        total += len(items)

    print(f"[READY] split feed valid total={total}")
    print(
        "[SCHEDULE] 09,11,13,15,17,19,21,23,01 / "
        "quiet 01:00-09:00"
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
