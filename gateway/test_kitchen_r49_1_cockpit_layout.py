from pathlib import Path

p = Path(__file__).with_name('companion_gateway.py')
s = p.read_text(encoding='utf-8')

assert 'A3.0b FIX1 R49.1 · 小K COCKPIT LAYOUT POLISH' in s
assert "others.appendChild(el('h4','','其他菜'" in s
assert "prep.appendChild(el('h4','','统一备菜'))" in s
assert "r49-prep-button" in s
assert "r49-other-row" in s
assert "backMenu.setAttribute('aria-label','返回厨房中台')" in s
assert ".mock-cook-menu-back:before{content:'←'" in s

dashboard_start = s.index("function renderDashboard(v)")
dashboard_end = s.index("function renderTodayMenu(v)", dashboard_start)
dashboard = s[dashboard_start:dashboard_end]
assert "待处理" not in dashboard
assert "r49-pending-row" not in dashboard
assert "下一道" not in dashboard

print('PASS R49.1 cockpit layout polish')
