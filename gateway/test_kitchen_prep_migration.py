#!/usr/bin/env python3
import json
import tempfile
import time
from pathlib import Path

import companion_gateway as g


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        g.HOMEAI_DATA_DIR = root
        g.KITCHEN_PROGRESS_STATE_FILE = root / "kitchen_progress.json"
        g.KITCHEN_TIMER_STATE_FILE = root / "kitchen_timers.json"

        g.KITCHEN_PROGRESS_STATE_FILE.write_text(json.dumps({
            "schema": 1,
            "progress": {"2026-09-14": {"测试菜": 0}},
        }, ensure_ascii=False), encoding="utf-8")
        g.load_kitchen_progress()
        step, ok = g._kitchen_progress_get("2026-09-14", "测试菜", 4)
        assert ok and step == 1
        saved = json.loads(g.KITCHEN_PROGRESS_STATE_FILE.read_text(encoding="utf-8"))
        assert saved["schema"] == 2
        assert saved["progress"]["2026-09-14"]["测试菜"] == 1

        now = time.time()
        g.KITCHEN_TIMER_STATE_FILE.write_text(json.dumps({
            "version": 1,
            "timers": [{
                "timer_id": "legacy-timer",
                "dish": "测试菜",
                "step": 0,
                "duration_sec": 600,
                "status": "running",
                "started_at": now,
                "ends_at": now + 600,
                "paused_remaining_sec": 0,
                "created_at": now,
                "updated_at": now,
                "finished_at": 0,
                "notified": False,
            }],
        }, ensure_ascii=False), encoding="utf-8")
        g.load_kitchen_timers()
        assert g.KITCHEN_TIMERS["legacy-timer"].step == 1
        saved_timers = json.loads(g.KITCHEN_TIMER_STATE_FILE.read_text(encoding="utf-8"))
        assert saved_timers["version"] == 2
        assert saved_timers["timers"][0]["step"] == 1

    print("KitchenTerminal prep-step migration self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
