import companion_gateway as g

menu = {
    'date': g._kitchen_today(), 'weekday': '周五', 'style': '家常', 'servings': 3,
    'items': [
        {'name': '糖醋小排', 'cook_order': 1},
        {'name': '醋溜娃娃菜', 'cook_order': 2},
        {'name': '木耳鸡汤', 'cook_order': 3},
    ],
    'recipes': [
        {'name': '糖醋小排', 'steps': ['备菜', '焖煮20分钟', '收汁'], 'step_kinds': ['prep','cook','cook'],
         'prep_items': ['排骨焯水', '调糖醋汁'], 'step_timers': [None, {'default_sec': 1200, 'max_sec': 1500}, None]},
        {'name': '醋溜娃娃菜', 'steps': ['备菜', '爆炒'], 'step_kinds': ['prep','cook'],
         'prep_items': ['切娃娃菜'], 'step_timers': [None, None]},
        {'name': '木耳鸡汤', 'steps': ['备菜', '炖煮'], 'step_kinds': ['prep','cook'],
         'prep_items': ['泡木耳'], 'step_timers': [None, {'default_sec': 1800, 'max_sec': 2400}]},
    ],
}

g.KITCHEN_CURRENT_MENU = menu
g.KITCHEN_CURRENT_STATE = {'screen': 'recipe', 'date': menu['date'], 'dish': '糖醋小排', 'step': 1}
payload = g._kitchen_dashboard_payload(menu)
assert payload['type'] == 'kitchen.show_dashboard'
assert payload['current']['name'] == '糖醋小排'
assert payload['current']['step'] == 1
assert payload['current']['timer_hint']['default_sec'] == 1200
assert payload['next']['name'] == '醋溜娃娃菜'
assert payload['pending']
print('PASS R49 dashboard runtime payload')
