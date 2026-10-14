import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import storage


class SplitStorageTests(unittest.TestCase):
    def test_legacy_items_are_split_without_losing_content(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "state.sqlite3"
            catalog = Path(folder) / "catalog.sqlite3"
            connection = storage.connect_databases(state, catalog)
            connection.executescript("""
                CREATE TABLE main.items (
                  id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL,
                  source TEXT NOT NULL, kind TEXT NOT NULL, summary TEXT NOT NULL,
                  author TEXT NOT NULL, published TEXT NOT NULL, tags TEXT NOT NULL,
                  added_at TEXT NOT NULL, content_chars INTEGER NOT NULL,
                  discovery_source TEXT, category TEXT NOT NULL
                );
                INSERT INTO main.items VALUES
                  ('item-1','Title','https://example.com','Example','blog','Summary',
                   'Author','2026-01-01T00:00:00+00:00','systems',
                   '2026-01-02T00:00:00+00:00',4000,'Hacker News','blog');
                CREATE TABLE main.votes (
                  item_id TEXT PRIMARY KEY, vote INTEGER NOT NULL, voted_at TEXT NOT NULL
                );
                INSERT INTO main.votes VALUES ('item-1',1,'2026-01-03T00:00:00+00:00');
            """)
            storage.init_storage(connection)
            connection.commit()

            self.assertFalse(connection.execute(
                "SELECT 1 FROM main.sqlite_master WHERE type='table' AND name='items'"
            ).fetchone())
            self.assertEqual(
                tuple(connection.execute(
                    "SELECT id,title,url,category,added_at FROM catalog.items"
                ).fetchone()),
                ("item-1", "Title", "https://example.com", "blog", "2026-01-02T00:00:00+00:00"),
            )
            self.assertEqual(
                tuple(connection.execute(
                    "SELECT item_id,summary,tags FROM item_content"
                ).fetchone()),
                ("item-1", "Summary", "systems"),
            )
            self.assertEqual(
                tuple(connection.execute(
                    "SELECT item_id,vote,voted_at FROM catalog.votes"
                ).fetchone()),
                ("item-1", 1, "2026-01-03T00:00:00+00:00"),
            )
            self.assertFalse(connection.execute(
                "SELECT 1 FROM main.sqlite_master WHERE type='table' AND name='votes'"
            ).fetchone())
            storage.init_storage(connection)
            connection.close()

            with sqlite3.connect(catalog) as public:
                columns = {row[1] for row in public.execute("PRAGMA table_info(items)")}
                self.assertEqual(public.execute("SELECT COUNT(*) FROM votes").fetchone()[0], 1)
            self.assertNotIn("summary", columns)
            self.assertNotIn("tags", columns)

    def test_existing_catalog_dates_remain_unknown_and_new_inserts_require_date(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "state.sqlite3"
            catalog = Path(folder) / "catalog.sqlite3"
            with sqlite3.connect(catalog) as public:
                public.executescript("""
                    CREATE TABLE items (
                      id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL,
                      source TEXT NOT NULL, kind TEXT NOT NULL, author TEXT NOT NULL,
                      published TEXT NOT NULL, content_chars INTEGER NOT NULL,
                      discovery_source TEXT, category TEXT NOT NULL
                    ) WITHOUT ROWID;
                    INSERT INTO items VALUES
                      ('old','Old','https://example.com/old','Example','blog','Author',
                       '2026-01-01T00:00:00+00:00',100,'Feed','blog');
                """)
            with storage.connect_databases(state, catalog) as connection:
                storage.init_storage(connection)
                self.assertIsNone(connection.execute(
                    "SELECT added_at FROM catalog.items WHERE id='old'"
                ).fetchone()[0])
                with self.assertRaisesRegex(sqlite3.IntegrityError, "added_at is required"):
                    connection.execute("""INSERT INTO catalog.items
                        (id,title,url,source,kind,author,published,content_chars,category)
                        VALUES ('new','New','https://example.com/new','Example','blog','Author',
                                '2026-01-01T00:00:00+00:00',100,'blog')""")
                connection.execute("""INSERT INTO catalog.items
                    (id,title,url,source,kind,author,published,content_chars,category,added_at)
                    VALUES ('new','New','https://example.com/new','Example','blog','Author',
                            '2026-01-01T00:00:00+00:00',100,'blog','2026-10-14T00:00:00+00:00')""")
                self.assertEqual(connection.execute(
                    "SELECT added_at FROM catalog.items WHERE id='new'"
                ).fetchone()[0], "2026-10-14T00:00:00+00:00")
                storage.init_storage(connection)

    def test_refresh_keeps_first_added_date(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "state.sqlite3"
            catalog = Path(folder) / "catalog.sqlite3"
            item = {"id": "example", "title": "Example", "url": "https://example.com/post",
                    "source": "Example", "kind": "blog", "author": "Author",
                    "published": "2026-01-01T00:00:00+00:00", "content_chars": 2000,
                    "summary": "First summary", "tags": "systems"}
            with patch.object(app, "DB", state), patch.object(app, "CATALOG", catalog), \
                    patch.object(app, "collect", return_value=([item], {})), \
                    patch.object(app, "queue_indexing") as queued:
                app.init_db()
                self.assertTrue(app.refresh(force=True))
                with storage.connect_databases(state, catalog) as connection:
                    connection.execute("UPDATE catalog.items SET added_at='2026-01-02T00:00:00+00:00'")
                    connection.execute("UPDATE item_content SET added_at='2026-01-02T00:00:00+00:00'")
                self.assertTrue(app.refresh(force=True))
                with storage.connect_databases(state, catalog) as connection:
                    row = connection.execute(storage.ITEMS_SELECT).fetchone()
                    self.assertEqual(row["added_at"], "2026-01-02T00:00:00+00:00")
                    self.assertEqual(connection.execute(
                        "SELECT added_at FROM item_content"
                    ).fetchone()[0], "2026-01-02T00:00:00+00:00")
                self.assertEqual(queued.call_count, 2)


if __name__ == "__main__":
    unittest.main()
