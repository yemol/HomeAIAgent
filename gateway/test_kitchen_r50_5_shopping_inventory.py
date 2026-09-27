#!/usr/bin/env python3
import companion_gateway as g


def main() -> None:
    old_snapshot = g._food_snapshot
    old_checked = {k: set(v) for k, v in g.KITCHEN_SHOPPING_CHECKED.items()}
    try:
        g.KITCHEN_SHOPPING_CHECKED.clear()
        g._food_snapshot = lambda: {
            "items": [
                {"name": "番茄", "unit": "个", "quantity": 4, "status": ""},
                {"name": "鸡蛋", "unit": "个", "quantity": 6, "status": ""},
                {"name": "生抽", "unit": "状态", "quantity": 0, "status": "一般"},
                {"name": "盐", "unit": "状态", "quantity": 0, "status": "充足"},
            ]
        }
        menu = {
            "date": "2026-09-20",
            "shopping": [
                {"name": "菜市场", "items": ["番茄 2个", "土鸡蛋 10个", "黄瓜 2根"]},
                {"name": "超市", "items": ["生抽 1瓶", "盐水鸭 1份", "食盐 1袋"]},
            ],
        }
        groups, _ = g._kitchen_shopping_groups_with_state(menu)
        by_text = {item["text"]: item for group in groups for item in group["items"]}
        assert by_text["番茄 2个"]["inventory"]["label"] == "库存 4个"
        assert by_text["土鸡蛋 10个"]["inventory"]["label"] == "库存 6个"
        assert by_text["生抽 1瓶"]["inventory"]["label"] == "库存 一般"
        assert "inventory" not in by_text["黄瓜 2根"]
        assert "inventory" not in by_text["盐水鸭 1份"], by_text["盐水鸭 1份"]
        assert by_text["食盐 1袋"]["inventory"]["label"] == "库存 充足"

        payload = g._kitchen_shopping_payload(menu)
        assert payload["type"] == "kitchen.show_shopping"
        assert "库存" in payload["message"]

        html = g._kitchen_html().decode("utf-8")
        assert "A3.0b FIX1 R50.5 · 小K SHOPPING INVENTORY CROSS-CHECK" in html
        assert "shopping-stock-badge" in html
        assert "item.inventory" in html
    finally:
        g._food_snapshot = old_snapshot
        g.KITCHEN_SHOPPING_CHECKED.clear()
        g.KITCHEN_SHOPPING_CHECKED.update(old_checked)
    print("PASS test_kitchen_r50_5_shopping_inventory")


if __name__ == "__main__":
    main()
