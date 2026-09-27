#!/usr/bin/env python3
import companion_gateway as g


def badge(text, items):
    return g._kitchen_shopping_inventory_match(text, items)


def main() -> None:
    inventory = [
        {"name": "牛肉", "unit": "份", "quantity": 1, "status": ""},
        {"name": "猪肉", "unit": "份", "quantity": 2, "status": ""},
        {"name": "鸡蛋", "unit": "个", "quantity": 6, "status": ""},
        {"name": "番茄", "unit": "个", "quantity": 4, "status": ""},
        {"name": "盐", "unit": "状态", "quantity": 0, "status": "充足"},
    ]

    # Regression: preparation/cut forms must not inherit base-ingredient stock.
    assert badge("牛肉片 1份", inventory) is None
    assert badge("猪肉丝 1份", inventory) is None
    assert badge("牛肉卷 1盒", inventory) is None
    assert badge("盐水鸭 1份", inventory) is None

    # Exact ingredient still matches.
    assert badge("牛肉 1份", inventory)["label"] == "库存 1份"
    assert badge("鸡蛋 10个", inventory)["label"] == "库存 6个"

    # Safe name synonyms / quality variants remain useful.
    assert badge("土鸡蛋 10个", inventory)["label"] == "库存 6个"
    assert badge("有机鸡蛋 10个", inventory)["label"] == "库存 6个"
    assert badge("西红柿 2个", inventory)["label"] == "库存 4个"
    assert badge("食盐 1袋", inventory)["label"] == "库存 充足"

    # If both generic and exact variant exist, prefer exact displayed name.
    both = inventory + [{"name": "土鸡蛋", "unit": "个", "quantity": 3, "status": ""}]
    exact = badge("土鸡蛋 10个", both)
    assert exact["name"] == "土鸡蛋", exact
    assert exact["label"] == "库存 3个", exact

    # Parser must keep food-form words and remove only trailing quantity/unit.
    assert g._kitchen_shopping_ingredient_name("牛肉片 1份") == "牛肉片"
    assert g._kitchen_shopping_ingredient_name("番茄 2个") == "番茄"
    assert g._kitchen_shopping_identity("牛肉") != g._kitchen_shopping_identity("牛肉片")

    print("PASS test_kitchen_r50_10_shopping_identity_match")


if __name__ == "__main__":
    main()
