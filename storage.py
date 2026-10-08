"""SQLite boundaries for the public catalog and local AttentionSpan state."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parent
STATE_DB = Path(os.environ.get("ATTENTIONSPAN_DB", ROOT / "data" / "papers.sqlite3"))
CATALOG_DB = Path(os.environ.get("ATTENTIONSPAN_CATALOG", ROOT / "data" / "catalog.sqlite3"))

ITEMS_SELECT = """
SELECT i.id,i.title,i.url,i.source,i.kind,
       COALESCE(c.summary,'') AS summary,i.author,i.published,
       COALESCE(c.tags,'') AS tags,COALESCE(c.added_at,'') AS added_at,
       i.content_chars,i.discovery_source,i.category
FROM catalog.items i LEFT JOIN item_content c ON c.item_id=i.id
"""


def connect_databases(state: Path = STATE_DB, catalog: Path = CATALOG_DB) -> sqlite3.Connection:
    state.parent.mkdir(parents=True, exist_ok=True)
    catalog.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(state, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA main.journal_mode=WAL")
    connection.execute("ATTACH DATABASE ? AS catalog", (str(catalog),))
    return connection


def init_storage(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS catalog.items (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL,
          source TEXT NOT NULL, kind TEXT NOT NULL, author TEXT NOT NULL,
          published TEXT NOT NULL, content_chars INTEGER NOT NULL DEFAULT 0,
          discovery_source TEXT, category TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS item_content (
          item_id TEXT PRIMARY KEY, summary TEXT NOT NULL DEFAULT '',
          tags TEXT NOT NULL DEFAULT '', added_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS votes (
          item_id TEXT PRIMARY KEY, vote INTEGER NOT NULL, voted_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS skips (
          item_id TEXT PRIMARY KEY, eligible_after INTEGER NOT NULL, skipped_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reads (
          item_id TEXT PRIMARY KEY, read_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    legacy_columns = {row[1] for row in connection.execute("PRAGMA main.table_info(items)")}
    if legacy_columns:
        required = {"id", "title", "url", "source", "kind", "summary", "author",
                    "published", "tags", "added_at", "content_chars",
                    "discovery_source", "category"}
        missing = required - legacy_columns
        if missing:
            raise RuntimeError(f"Legacy items table is missing: {', '.join(sorted(missing))}")
        connection.execute("""INSERT OR REPLACE INTO catalog.items
            (id,title,url,source,kind,author,published,content_chars,discovery_source,category)
            SELECT id,title,url,source,kind,author,published,content_chars,
                   discovery_source,category FROM main.items""")
        connection.execute("""INSERT OR REPLACE INTO item_content
            (item_id,summary,tags,added_at)
            SELECT id,summary,tags,added_at FROM main.items""")
        connection.execute("DROP TABLE main.items")
    count = connection.execute("SELECT COUNT(*) FROM catalog.items").fetchone()[0]
    if count >= 1000:
        connection.execute("INSERT OR IGNORE INTO meta VALUES ('long_form_backfill_v1', ?)",
                           (datetime.now(timezone.utc).isoformat(),))


def catalog_info(catalog: Path = CATALOG_DB) -> dict:
    with sqlite3.connect(f"file:{catalog}?mode=ro", uri=True) as connection:
        return {
            "path": str(catalog),
            "items": connection.execute("SELECT COUNT(*) FROM items").fetchone()[0],
            "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
        }
