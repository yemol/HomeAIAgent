from pathlib import Path

src = Path(__file__).with_name('companion_gateway.py').read_text(encoding='utf-8')
html = Path(__file__).with_name('kitchen_full.html').read_text(encoding='utf-8')

assert 'A3.0b FIX1 R49.2 · 小K COCKPIT FLOW POLISH' in src
assert 'A3.0b FIX1 R49.2 · 小K COCKPIT FLOW POLISH' in html

# Home menu pull stays on Home, then CTA can become cooking start.
assert "primary.onclick=function(){action(names.length?'dashboard':'home');}" in src
assert 'Home\'s “获取今日菜谱” is a refresh, not a navigation action.' in src

# Unified prep can always return to the cockpit.
assert "backDash=el('button','mock-cook-menu-back','厨房中台')" in src
assert "backDash.onclick=function(){action('dashboard');};" in src

# Cockpit footer only exposes shopping + timer. Today menu already exists in global nav.
start = src.index('function renderDashboard(v)')
end = src.index('function renderTodayMenu(v)', start)
dash = src[start:end]
assert "q('🛒','今日采购',openShoppingModal);q('◷','厨房计时',openTimerCenter);" in dash
assert "q('☰','今日菜谱'" not in dash
assert "q('▣','拍一下'" not in dash
assert '加到采购' not in dash

# Shopping is an overlay and does not force the kitchen page away from the cockpit.
assert 'id="shoppingModal"' in src
assert "overlayAction('shopping_overlay'" in src
assert "overlayAction('shopping_toggle_overlay'" in src
assert 'elif action == "shopping_overlay":' in src
assert 'elif action == "shopping_toggle_overlay":' in src
assert 'payload["overlay"] = overlay_payload' in src
assert "else if(target==='shopping')openShoppingModal();" in src

print('PASS R49.2 cockpit flow polish')
