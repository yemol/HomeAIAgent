#!/usr/bin/env python3
# Historical regression slot retained after R45 moved consumption to day finish.
import companion_gateway as g


def main() -> None:
    html = g._kitchen_html().decode('utf-8')
    assert 'A3.0b FIX1 R49 · 小K COOKING COCKPIT' in html
    assert '完成这道菜 · 登记消耗' not in html
    assert "mock-recipe-consume-button','✓ 完成这道菜'" not in html
    assert ('今日库存消耗' in html) or ('今日食材结算' in html)
    print('PASS test_kitchen_r44_recipe_flow_consumption (R45 compatibility)')


if __name__ == '__main__':
    main()
