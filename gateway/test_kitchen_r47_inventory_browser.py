from pathlib import Path
p=Path(__file__).with_name('companion_gateway.py')
s=p.read_text(encoding='utf-8')
assert 'R49 · 小K COOKING COCKPIT' in s
for x in ['foodInventorySearch','foodInventoryCategory','foodInventorySort','foodInventoryPageSize','foodInventoryPrev','foodInventoryNext','foodInventoryPageInfo']:
    assert x in s, x
assert 'position:fixed!important;top:50%!important' in s
assert "mock-cook-menu-back','首页'" in s
assert 'food-log-filter-stack' in s
print('PASS R48 inventory browser / fixed nav')
