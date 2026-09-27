#!/usr/bin/env python3
"""KitchenTerminal Home Food A0.1 persistence regression guard."""
import tempfile
from pathlib import Path

import companion_gateway as g


def main() -> None:
    old = g.FOOD_DB_FILE
    try:
        with tempfile.TemporaryDirectory() as td:
            g.FOOD_DB_FILE = Path(td) / "food.sqlite3"
            g._food_add("排骨", 2, "份", "肉类", "菜市场")
            g._food_add("鸡蛋", 12, "个", "蛋类", "超市")
            g._food_add("高丽菜", 3, "份", "蔬菜", "网上APP")
            g._food_set_status("料酒", "快没了")
            g._food_consume("排骨", 1)
            g._food_set_priority("高丽菜", "本周")

            snap = g._food_snapshot()
            items = {x["name"]: x for x in snap["items"]}
            assert items["排骨"]["quantity"] == 1
            assert items["排骨"]["unit"] == "份"
            assert items["鸡蛋"]["quantity"] == 12
            assert items["料酒"]["status"] == "快没了"
            assert items["高丽菜"]["priority_window"] == "本周"
            assert snap["default_people"] == 3
            assert any(x["name"] == "料酒" for x in snap["needs_attention"])
            assert any(x["name"] == "高丽菜" for x in snap["priority"])
            assert len(snap["events"]) >= 6

            html = g._kitchen_html().decode("utf-8")
            for token in [
                "食材管理",
                "买入食材",
                "记录消耗",
                "全部食材",
                "/kitchen/food/view",
                "/kitchen/food/action",
                "这两天",
                "本周",
                "暂不着急",
                "充足",
                "一般",
                "快没了",
            ]:
                assert token in html, token
    finally:
        g.FOOD_DB_FILE = old
    print("KitchenTerminal Home Food A0.1 persistence regression: PASS")


if __name__ == "__main__":
    main()
