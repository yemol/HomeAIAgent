#!/usr/bin/env python3
from companion_gateway import _kitchen_html

def main():
    html = _kitchen_html().decode('utf-8')
    assert 'id="standaloneTimerModal"' in html
    assert 'id="idleTimerHost"' in html
    assert "function openStandaloneTimer()" in html
    assert "feature('◴','厨房计时'" in html and 'openStandaloneTimer' in html
    assert "homeTimerWrap" not in html
    assert "function renderTimerStrip(){var strip=$('timerStrip');if(!strip)return;" in html
    print('KitchenTerminal timer DOM lifecycle regression: PASS')

if __name__ == '__main__':
    main()
