#!/usr/bin/env python3
from companion_gateway import _kitchen_html

def main():
    html = _kitchen_html().decode('utf-8')
    assert "if(!hint||!hint.default_sec)return 0" in html
    assert "这一步没有固定计时时间，需要时再开启" in html
    assert "timer-controls-single" in html
    assert "⏱ 计时" in html
    assert "mock-cook-timer-button" in html
    assert "⏱ 厨房计时" in html
    assert "本步骤没有预设时间，可直接开始" not in html
    print('KitchenTerminal R43 timer UX regression: PASS')

if __name__ == '__main__':
    main()
