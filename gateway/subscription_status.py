#!/usr/bin/env python3
from pathlib import Path
import json, os

config_dir = Path(
    os.getenv(
        "HOMEAI_CONFIG_DIR",
        str(Path.home() / ".config" / "HomeAIAgent"),
    )
).expanduser()
path = Path(
    os.getenv(
        "HOMEAI_SUBSCRIPTIONS_FILE",
        str(config_dir / "subscriptions.json"),
    )
).expanduser()

print(f"subscriptions={path}")
print(f"exists={path.exists()}")
if not path.exists():
    raise SystemExit(1)

body = json.loads(path.read_text(encoding="utf-8"))
items = body.get("items") or []
print(f"provider={body.get('provider','manual')}")
print(f"count={len(items)}")
for i, item in enumerate(items, 1):
    print(
        f"{i}. [{item.get('category','')}] "
        f"{item.get('headline','')}  id={item.get('id','')}"
    )
