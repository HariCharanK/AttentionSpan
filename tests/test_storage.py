import sqlite3
import tempfile
import unittest
from pathlib import Path

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
                    "SELECT id,title,url,category FROM catalog.items"
                ).fetchone()),
                ("item-1", "Title", "https://example.com", "blog"),
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


if __name__ == "__main__":
    unittest.main()
