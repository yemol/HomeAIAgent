#!/usr/bin/env python3
import asyncio
import companion_gateway as gateway

async def main() -> int:
    print("=== HomeAIAgent HomeAI Info Skill check ===")
    try:
        body = await gateway.call_homeai_info_get_feed()
        items = gateway.normalize_skill_feed(body)
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}")
        return 2

    game = sum(1 for item in items if item.category == "游戏")
    finance = sum(1 for item in items if item.category == "金融")

    print(
        f"[OK] protocol={body.get('protocol_version')} "
        f"status={body.get('status')} "
        f"items={len(items)} game={game} finance={finance}"
    )
    for index, item in enumerate(items, 1):
        print(
            f"{index:02d}. [{item.category}] "
            f"{item.headline} | {item.source}"
        )
    return 0

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
