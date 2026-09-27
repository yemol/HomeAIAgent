from pathlib import Path

src = Path(__file__).with_name("companion_gateway.py").read_text(encoding="utf-8")
assert "A3.0b FIX1 R49 · 小K COOKING COCKPIT" in src
assert 'class="food-log-filter-stack"' in src
assert src.count('class="food-log-filter-row"') >= 2
assert 'food-log-filter-grid' not in src
assert '#foodLogDate{width:100%!important;min-width:0!important;max-width:100%!important' in src
assert '.food-log-filter-row .food-input{display:block;width:100%!important' in src
print("PASS R48 food log stacked layout")
