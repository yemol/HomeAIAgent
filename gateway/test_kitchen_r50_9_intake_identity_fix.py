#!/usr/bin/env python3
import sqlite3
import tempfile
from pathlib import Path
import companion_gateway as g


def item_by_name(snapshot, name):
    return next(x for x in snapshot['items'] if x['name'] == name)


def test_fresh_database_edit_and_rename_keep_intake_time(tmp: Path) -> None:
    g.FOOD_DB_FILE = tmp / 'fresh.sqlite3'
    g._food_add('番茄', 3, '份', '蔬菜', '菜市场')
    with g._food_db() as conn:
        row = conn.execute("SELECT id FROM food_items WHERE name='番茄'").fetchone()
        item_id = int(row['id'])
        conn.execute("UPDATE food_events SET created_at='2026-09-18T08:30:00+08:00' WHERE event_type='add' AND item_id=?", (item_id,))

    before = item_by_name(g._food_snapshot(), '番茄')
    assert before['last_added_at'] == '2026-09-18T08:30:00+08:00'

    edited = g._food_edit('番茄', '番茄', quantity=2, item_id=item_id)
    assert edited['last_added_at'] == before['last_added_at']
    after_qty = item_by_name(g._food_snapshot(), '番茄')
    assert after_qty['last_added_at'] == before['last_added_at']

    renamed = g._food_edit('番茄', '西红柿', quantity=2, item_id=item_id)
    assert renamed['last_added_at'] == before['last_added_at']
    after_rename = item_by_name(g._food_snapshot(), '西红柿')
    assert after_rename['id'] == item_id
    assert after_rename['last_added_at'] == before['last_added_at']

    with g._food_db() as conn:
        rows = conn.execute("SELECT event_type,item_id,name FROM food_events ORDER BY id").fetchall()
        assert rows
        assert all(int(r['item_id']) == item_id for r in rows if r['item_id'] is not None)


def test_legacy_r50_8_database_migrates_rename_history(tmp: Path) -> None:
    db = tmp / 'legacy.sqlite3'
    conn = sqlite3.connect(str(db))
    conn.executescript("""
    CREATE TABLE food_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        category TEXT NOT NULL DEFAULT '其他',
        unit TEXT NOT NULL DEFAULT '份',
        quantity REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT '',
        storage TEXT NOT NULL DEFAULT '',
        priority_window TEXT NOT NULL DEFAULT '暂不着急',
        updated_at TEXT NOT NULL
    );
    CREATE TABLE food_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        name TEXT NOT NULL,
        amount REAL NOT NULL DEFAULT 0,
        unit TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    INSERT INTO food_items(id,name,category,unit,quantity,updated_at)
      VALUES(7,'小番茄','蔬菜','份',2,'2026-09-20T12:00:00+08:00');
    INSERT INTO food_events(event_type,name,amount,unit,category,source,note,created_at)
      VALUES('add','番茄',3,'份','蔬菜','菜市场','','2026-09-10T08:00:00+08:00');
    INSERT INTO food_events(event_type,name,unit,category,note,created_at)
      VALUES('rename','西红柿','份','蔬菜','番茄 → 西红柿','2026-09-15T09:00:00+08:00');
    INSERT INTO food_events(event_type,name,unit,category,note,created_at)
      VALUES('rename','小番茄','份','蔬菜','西红柿 → 小番茄','2026-09-20T12:00:00+08:00');
    """)
    conn.commit()
    conn.close()

    g.FOOD_DB_FILE = db
    snap = g._food_snapshot()
    item = item_by_name(snap, '小番茄')
    assert item['id'] == 7
    assert item['last_added_at'] == '2026-09-10T08:00:00+08:00'

    with g._food_db() as migrated:
        cols = {r['name'] for r in migrated.execute('PRAGMA table_info(food_events)').fetchall()}
        assert 'item_id' in cols
        links = migrated.execute('SELECT event_type,name,item_id FROM food_events ORDER BY id').fetchall()
        assert [int(r['item_id']) for r in links] == [7, 7, 7]

    edited = g._food_edit('小番茄', '圣女果', quantity=1, item_id=7)
    assert edited['last_added_at'] == '2026-09-10T08:00:00+08:00'
    assert item_by_name(g._food_snapshot(), '圣女果')['last_added_at'] == '2026-09-10T08:00:00+08:00'


def main() -> None:
    old_db = g.FOOD_DB_FILE
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            test_fresh_database_edit_and_rename_keep_intake_time(root)
            test_legacy_r50_8_database_migrates_rename_history(root)
        html = g._kitchen_html().decode('utf-8')
        assert 'A3.0b FIX1 R50.9 · 小K INTAKE IDENTITY FIX' in html
        assert 'A3.0b FIX1 R50.8 · 小K INVENTORY INTAKE DATE FILTER' in html
        assert 'foodInventoryIntakeDate' in html
        assert "sort==='intake'" in html
    finally:
        g.FOOD_DB_FILE = old_db
    print('PASS test_kitchen_r50_9_intake_identity_fix')


if __name__ == '__main__':
    main()
