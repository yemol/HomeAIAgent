#!/usr/bin/env python3
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import companion_gateway as g

html = g._kitchen_html().decode('utf-8')
assert '结束今日厨房' in html
assert "action('finish_start')" in html
assert "function renderFinish(v)" in html
assert "function renderSavePrivate(v)" in html
assert '保存所选并结束' in html
assert '不保存，直接结束' in html
assert 'private-choice' in html
assert "if(v.type==='kitchen.show_finish'){renderFinish(v);return;}" in html
assert "if(v.type==='kitchen.show_save_private'){renderSavePrivate(v);return;}" in html
print('KitchenTerminal R26 daily finish/private-recipe UI regression: PASS')
