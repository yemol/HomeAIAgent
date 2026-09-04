# HomeAIAgent A4.1 OpenClaw Info Skill Poller

This package replaces the abandoned "Gateway fetches RSS itself" route.

Architecture:

```text
HomeAI Info Skill
  homeai_info.get_feed
  homeai_info.get_item
        ↓
OpenClaw
        ↓
HomeAIAgent Gateway
        ↓
StickS3 / Glass2
```

A4.1 behavior:

- Gateway calls `homeai_info.get_feed`
- protocol: `homeai-info/1.1`
- requests max 20
- game 10 + finance 10
- default poll interval 900 s
- honors Skill `next_refresh_after_sec`
- only replaces the current feed after a valid `ok` / `partial` result
- Skill/OpenClaw failure keeps last-good cache
- cache: `~/.local/share/HomeAIAgent/info_skill_feed_cache.json`
- device sync waits until voice path is idle
- current item id remains the anchor for voice follow-up
- voice prompt instructs OpenClaw to call `homeai_info.get_item` for "这个讲讲" style questions

Device/UI:

- cache capacity: 20 items
- each item: 10 seconds
- category 12 px
- body 11 px
- A4.0.3 accepted vertical positioning unchanged
- A3.9 Gapless TTS unchanged

Manual Skill test:

```bash
cd HomeAIAgent/gateway
./run_full.sh
```

In another terminal:

```bash
cd HomeAIAgent/gateway
~/.local/share/HomeAIAgent/venv/bin/python check_info_skill.py
```

Expected:

```text
[OK] protocol=homeai-info/1.1 status=ok items=20 game=10 finance=10
```

Firmware must be flashed once because capacity and 10-second hold are firmware changes.
Do not erase flash; StickS3 NVS must remain intact.
