import tempfile
from pathlib import Path
import companion_gateway as g

old_menu = g.KITCHEN_MENU_DIR
old_private = g.KITCHEN_PRIVATE_RECIPE_DIR
try:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / 'Vault'
        menu = root / '晚餐推荐'
        nested_private = menu / '私房菜'
        sibling_private = root / '私房菜'
        deep_private = root / '家庭厨房' / '常用' / '私房菜'
        for d in (menu, nested_private, sibling_private, deep_private):
            d.mkdir(parents=True, exist_ok=True)

        (nested_private / '番茄炒蛋.md').write_text('''# 番茄炒蛋\n\n## 材料\n- 番茄 2个\n- 鸡蛋 3个\n\n## 制作步骤\n1. 番茄切块，鸡蛋打散。\n2. 先炒鸡蛋，再下番茄。\n''', encoding='utf-8')
        (sibling_private / '糖醋排骨.md').write_text('''---\nname: "糖醋排骨"\n---\n# 🍳 私房菜 · 糖醋排骨\n\n## 🧺 食材\n- 排骨 1份\n\n## 👨‍🍳 做法\n1. 排骨煎香。\n2. 加入料汁炖煮。\n''', encoding='utf-8')
        (deep_private / '清炒高丽菜.md').write_text('''# 清炒高丽菜\n\n## 食材\n- 高丽菜 1份\n\n## 烹饪方法\n- 热锅下菜\n- 大火快炒\n- 调味出锅\n''', encoding='utf-8')

        g.KITCHEN_MENU_DIR = menu
        g.KITCHEN_PRIVATE_RECIPE_DIR = root / 'does-not-exist' / '私房菜'
        dirs = g._kitchen_private_recipe_dirs()
        assert nested_private in dirs, dirs
        assert sibling_private in dirs, dirs
        assert deep_private in dirs, dirs
        lib = g._kitchen_private_recipe_library()
        names = {x['name'] for x in lib}
        assert {'番茄炒蛋','糖醋排骨','清炒高丽菜'} <= names, names
        for x in lib:
            if x['name'] in names:
                assert x['recipe']['steps']
        print('PASS private recipe dirs/parser', sorted(names))
finally:
    g.KITCHEN_MENU_DIR = old_menu
    g.KITCHEN_PRIVATE_RECIPE_DIR = old_private
