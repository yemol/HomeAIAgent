#!/usr/bin/env python3
"""Local KitchenTerminal control/debug client."""
from __future__ import annotations

import argparse
import asyncio
import json

import websockets


def _payload_from_args(args: argparse.Namespace) -> dict:
    if args.command == "status":
        return {"type": "kitchen.control.status"}
    if args.command == "today":
        return {"type": "kitchen.control.today"}
    if args.command == "shopping":
        return {"type": "kitchen.control.shopping"}
    if args.command == "timeline":
        return {"type": "kitchen.control.timeline"}
    if args.command == "finish":
        return {"type": "kitchen.control.finish"}
    if args.command == "finish-confirm":
        return {"type": "kitchen.control.finish_confirm"}
    if args.command == "finish-save":
        dishes = [x.strip() for x in (args.dishes or "").split(",") if x.strip()]
        return {"type": "kitchen.control.finish_save", "dishes": dishes}
    if args.command == "recipe":
        payload = {"type": "kitchen.control.recipe", "dish": args.dish}
        if args.step is not None:
            payload["step"] = args.step
        return payload
    if args.command == "timer-start":
        payload = {"type": "kitchen.control.timer_start"}
        if args.seconds is not None:
            payload["seconds"] = args.seconds
        return payload
    if args.command == "timer-add":
        return {"type": "kitchen.control.timer_adjust", "timer_id": args.timer_id, "seconds": args.seconds}
    if args.command == "timer-set":
        return {"type": "kitchen.control.timer_set", "timer_id": args.timer_id, "seconds": args.seconds}
    if args.command == "timer-pause":
        return {"type": "kitchen.control.timer_pause", "timer_id": args.timer_id}
    if args.command == "timer-resume":
        return {"type": "kitchen.control.timer_resume", "timer_id": args.timer_id}
    if args.command == "timer-cancel":
        return {"type": "kitchen.control.timer_cancel", "timer_id": args.timer_id}
    if args.command == "speak":
        return {"type": "kitchen.control.speak", "text": args.text, "kind": "debug"}
    if args.command == "idle":
        return {
            "type": "kitchen.show_idle",
            "title": args.title,
            "message": args.message,
            "footer": "等待逐光发送菜单",
        }
    if args.command == "message":
        return {
            "type": "kitchen.show_message",
            "title": args.title,
            "message": args.message,
        }
    items = [item.strip() for item in args.items.split(",") if item.strip()]
    return {
        "type": "kitchen.show_menu",
        "eyebrow": "HOME AI · 调试菜单",
        "title": args.title,
        "message": args.message,
        "items": items,
        "footer": "本机调试菜单",
    }


async def _run(args: argparse.Namespace) -> int:
    uri = f"ws://127.0.0.1:{args.port}/kitchen/control"
    payload = _payload_from_args(args)
    try:
        async with websockets.connect(uri, open_timeout=3, close_timeout=2) as ws:
            await ws.send(json.dumps(payload, ensure_ascii=False))
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
    except Exception as exc:
        print(f"[FAIL] KitchenTerminal control unavailable: {type(exc).__name__}: {exc}")
        return 2

    try:
        reply = json.loads(raw)
    except json.JSONDecodeError:
        print(f"[FAIL] invalid reply: {raw!r}")
        return 3

    print(json.dumps(reply, ensure_ascii=False, indent=2))
    if reply.get("status") == "rejected":
        return 4
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="KitchenTerminal local control client")
    parser.add_argument("--port", type=int, default=8765)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show connected clients/current state/timers")
    sub.add_parser("today", help="load today's Obsidian menu and show it")
    sub.add_parser("shopping", help="show today's shopping list")
    sub.add_parser("timeline", help="show today's cooking timeline")
    sub.add_parser("finish", help="open end-of-day cooking confirmation")
    sub.add_parser("finish-confirm", help="confirm cooking is finished and show private-recipe selection")
    p_finish_save = sub.add_parser("finish-save", help="save selected recipes to 私房菜 and close today's cooking")
    p_finish_save.add_argument("dishes", nargs="?", default="", help="comma-separated dish names; empty means save none")

    p_recipe = sub.add_parser("recipe", help="open one recipe from today's menu")
    p_recipe.add_argument("dish", help="dish name or unique fragment, e.g. 河虾")
    p_recipe.add_argument("--step", type=int, default=None, help="zero-based step index; omit to resume last viewed step")

    p_start = sub.add_parser("timer-start", help="start timer for current recipe step")
    p_start.add_argument("seconds", type=int, nargs="?", default=None, help="optional override; default uses step suggestion")

    p_add = sub.add_parser("timer-add", help="adjust current/specified timer by seconds")
    p_add.add_argument("seconds", type=int, help="positive or negative seconds")
    p_add.add_argument("--timer-id", default="")

    p_set = sub.add_parser("timer-set", help="set remaining timer seconds")
    p_set.add_argument("seconds", type=int)
    p_set.add_argument("--timer-id", default="")

    for name in ("timer-pause", "timer-resume", "timer-cancel"):
        p = sub.add_parser(name)
        p.add_argument("--timer-id", default="")

    p_speak = sub.add_parser("speak", help="synthesize and play one test sentence on the KitchenTerminal iPad")
    p_speak.add_argument("text")

    p_idle = sub.add_parser("idle", help="show idle page")
    p_idle.add_argument("--title", default="厨房终端")
    p_idle.add_argument("--message", default="等待逐光发送菜单")

    p_msg = sub.add_parser("message", help="show a large text message")
    p_msg.add_argument("--title", default="逐光")
    p_msg.add_argument("message")

    p_menu = sub.add_parser("menu", help="show a raw debug menu")
    p_menu.add_argument("--title", default="今日晚餐")
    p_menu.add_argument("--message", default="选择一道菜查看详情")
    p_menu.add_argument("items", help="comma-separated dish names")

    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
