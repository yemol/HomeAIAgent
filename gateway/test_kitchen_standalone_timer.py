#!/usr/bin/env python3
import companion_gateway as g


def main() -> int:
    g.KITCHEN_TIMERS.clear()
    timer = g._kitchen_timer_start_standalone(300)
    assert timer.kind == "standalone"
    assert timer.dish == "独立计时"
    assert timer.duration_sec == 300
    public = g._kitchen_timer_public(timer)
    assert public["kind"] == "standalone"
    assert public["label"] == "独立计时"

    # Starting a new standalone timer replaces only the previous standalone one.
    recipe = g.KitchenTimer(timer_id="recipe-test", dish="测试菜", step=1, duration_sec=60)
    g.KITCHEN_TIMERS[recipe.timer_id] = recipe
    newer = g._kitchen_timer_start_standalone(600)
    assert newer.timer_id in g.KITCHEN_TIMERS
    assert recipe.timer_id in g.KITCHEN_TIMERS
    assert sum(1 for t in g.KITCHEN_TIMERS.values() if t.kind == "standalone") == 1

    html = g._kitchen_html().decode("utf-8")
    assert "独立计时器" in html
    assert "timer_standalone_start" in html
    assert "/kitchen/alarm.wav" in html
    assert "safe-area-inset-top" in html
    assert "结束提醒" in html
    assert len(g.KITCHEN_STANDALONE_ALARM_WAV) > 1000
    assert g.KITCHEN_STANDALONE_ALARM_WAV[:4] == b"RIFF"

    g.KITCHEN_TIMERS.clear()
    g.save_kitchen_timers()
    print("KitchenTerminal standalone timer self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
