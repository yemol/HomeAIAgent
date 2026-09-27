#!/usr/bin/env python3
import time
from companion_gateway import _kitchen_html, KitchenTimer, _kitchen_timer_public


def main():
    now = time.time()
    timer = KitchenTimer(
        timer_id='test-r25', dish='测试菜', step=1, duration_sec=300,
        status='running', started_at=now, ends_at=now + 300,
        created_at=now, updated_at=now,
    )
    public = _kitchen_timer_public(timer)
    assert 'remaining_precise_sec' in public
    assert 299.0 <= float(public['remaining_precise_sec']) <= 300.0, public
    assert int(public['remaining_sec']) == 300, public

    html = _kitchen_html().decode('utf-8')
    assert "Number(t.ends_at)-Date.now()/1000" not in html, 'timer must not mix Gateway epoch with iPad epoch'
    assert "function clientClockMs()" in html
    assert "function anchorTimer(t)" in html
    assert "remaining_precise_sec" in html
    assert "anchorTimer(latestTimers[ai])" in html
    assert ".mock-cook-actions{position:sticky;bottom:82px" in html
    assert "← 上一步" in html and "下一步 →" in html
    print('KitchenTerminal R25 sticky navigation + timer clock-skew regression: PASS')


if __name__ == '__main__':
    main()
