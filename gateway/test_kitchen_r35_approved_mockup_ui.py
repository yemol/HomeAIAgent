from pathlib import Path
src=Path("companion_gateway.py").read_text()
assert "A3.0b FIX1 R49 · 小K COOKING COCKPIT" in src
for token in ["mock-welcome","mock-feature-grid","mock-menu-row","mock-source-card","mock-inventory-row","mock-prep-card","mock-cook-card","mock-shopping-row","Approved mockup UI"]:
    assert token in src, token
for text in ["获取今日菜谱","选择今日菜谱","食材管理","今日采购清单","厨房计时","库存推荐菜","私房菜","开始统一备菜"]:
    assert text in src, text
print("PASS: R35 approved mockup structure present")
