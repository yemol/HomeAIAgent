from pathlib import Path

p = Path(__file__).with_name('companion_gateway.py')
s = p.read_text(encoding='utf-8')

assert 'A3.0b FIX1 R49 · 小K COOKING COCKPIT' in s
assert 'def _kitchen_dashboard_payload' in s
assert 'async def _kitchen_show_dashboard' in s
assert '"type": "kitchen.show_dashboard"' in s
assert "if(v.type==='kitchen.show_dashboard'){renderDashboard(v);return;}" in s
assert 'function renderDashboard(v)' in s
assert 'function openTimerCenter()' in s
assert 'timer-center-grid' in s
assert 'r49-dash-body' in s
assert 'body[data-k-page="dashboard"] .content{overflow:hidden!important' in s
assert "action(names.length?'dashboard':'home')" in s
assert "mock-cook-menu-back','厨房中台'" in s
assert "mock-cook-timer-button','⏱ 多计时器中心'" in s
assert "dashboardTimerLabel(t)" in s
assert "timerForDishStep(c.name,c.step)" in s
assert "q('🛒','今日采购',openShoppingModal);q('◷','厨房计时',openTimerCenter);" in s
print('PASS R49 cooking cockpit static contract')
