#!/usr/bin/env python3
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import companion_gateway as g
html = g._kitchen_html().decode('utf-8')
assert '小K' in g.KITCHEN_UI_VERSION
assert 'function renderFinish(v)' in html
assert 'function renderSavePrivate(v)' in html
assert 'finish-summary' in html
assert 'finish-note' in html
assert 'finish-modern-actions' in html
assert 'position:sticky;bottom:72px' in html
assert "今天留下哪些菜？" in html
assert "进入今日收尾" in html
assert "保存所选并结束" in html
assert "private-row input:checked:after" in html
assert "finish-card" not in html
print('KitchenTerminal R27 finish style unification regression: PASS')
