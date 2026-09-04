#!/usr/bin/env python3
from pathlib import Path
import os
from dotenv import dotenv_values

path = Path(
    os.getenv(
        "HOMEAI_CONFIG_FILE",
        str(Path.home() / ".config" / "HomeAIAgent" / "gateway.env"),
    )
).expanduser()

print(f"config={path}")
print(f"exists={path.exists()}")
if not path.exists():
    raise SystemExit(1)

cfg = dotenv_values(path)
for key in [
    "GATEWAY_HOST","GATEWAY_PORT","GATEWAY_PATH",
    "ASR_PROVIDER","VOLCENGINE_ASR_RESOURCE_ID",
    "TTS_PROVIDER","VOLCENGINE_TTS_RESOURCE_ID",
    "OPENCLAW_BASE_URL","OPENCLAW_MODEL","OPENCLAW_USER",
    "OPENCLAW_SSH_USER","OPENCLAW_SSH_HOST",
    "OPENCLAW_LOCAL_PORT","OPENCLAW_REMOTE_PORT",
]:
    print(f"{key}={cfg.get(key,'')}")
print(f"VOLCENGINE_API_KEY={'SET' if cfg.get('VOLCENGINE_API_KEY') else 'MISSING'}")
print(f"OPENCLAW_TOKEN={'SET' if cfg.get('OPENCLAW_TOKEN') else 'MISSING'}")
