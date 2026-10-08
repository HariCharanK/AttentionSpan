"""Build and seed the public AttentionSpan item catalog."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parent
RUNTIME_DB = ROOT / "data" / "papers.sqlite3"
CATALOG_DB = ROOT / "data" / "catalog.sqlite3"
ITEM_COLUMNS = (
    "id", "title", "url", "source", "kind", "summary", "author",
    "published", "tags", "added_at", "content_chars", "discovery_source",
    "category",
)
ITEM_SCHEMA = """
CREATE TABLE items (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL,
  source TEXT NOT NULL, kind TEXT NOT NULL, summary TEXT NOT NULL,
  author TEXT NOT NULL, published TEXT NOT NULL, tags TEXT NOT NULL,
  added_at TEXT NOT NULL, content_chars INTEGER NOT NULL DEFAULT 0,
  discovery_source TEXT, category TEXT NOT NULL
);
CREATE INDEX idx_items_published ON items(published DESC);
"""


def export_catalog(source: Path = RUNTIME_DB, target: Path = CATALOG_DB) -> int:
    """Atomically export only public item metadata from the local runtime DB."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    source_db = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    target_db = sqlite3.connect(temporary)
    try:
        source_columns = {row[1] for row in source_db.execute("PRAGMA table_info(items)")}
        missing = set(ITEM_COLUMNS) - source_columns
        if missing:
            raise RuntimeError(f"Runtime items table is missing: {', '.join(sorted(missing))}")
        target_db.executescript(ITEM_SCHEMA)
        placeholders = ",".join("?" for _ in ITEM_COLUMNS)
        columns = ",".join(ITEM_COLUMNS)
        rows = source_db.execute(f"SELECT {columns} FROM items ORDER BY id")
        target_db.executemany(
            f"INSERT INTO items ({columns}) VALUES ({placeholders})", rows
        )
        count = target_db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        target_db.commit()
        if target_db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Catalog integrity check failed")
        target_db.execute("VACUUM")
    finally:
        source_db.close()
        target_db.close()
    os.replace(temporary, target)
    target.chmod(0o644)
    return count


def seed_catalog(connection: sqlite3.Connection, catalog: Path = CATALOG_DB) -> int:
    """Seed an empty runtime items table from the checked-in public catalog."""
    if not catalog.exists() or connection.execute("SELECT 1 FROM items LIMIT 1").fetchone():
        return 0
    catalog_db = sqlite3.connect(f"file:{catalog}?mode=ro", uri=True)
    try:
        catalog_columns = {row[1] for row in catalog_db.execute("PRAGMA table_info(items)")}
        missing = set(ITEM_COLUMNS) - catalog_columns
        if missing:
            raise RuntimeError(f"Catalog items table is missing: {', '.join(sorted(missing))}")
        columns = ",".join(ITEM_COLUMNS)
        placeholders = ",".join("?" for _ in ITEM_COLUMNS)
        rows = catalog_db.execute(f"SELECT {columns} FROM items ORDER BY id")
        connection.executemany(
            f"INSERT OR IGNORE INTO items ({columns}) VALUES ({placeholders})", rows
        )
        return connection.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    finally:
        catalog_db.close()


def catalog_info(path: Path = CATALOG_DB) -> dict:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
        return {
            "path": str(path),
            "tables": tables,
            "items": connection.execute("SELECT COUNT(*) FROM items").fetchone()[0],
            "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("export", "info"))
    parser.add_argument("--source", type=Path, default=RUNTIME_DB)
    parser.add_argument("--target", type=Path, default=CATALOG_DB)
    args = parser.parse_args()
    if args.command == "export":
        export_catalog(args.source, args.target)
    print(json.dumps(catalog_info(args.target), indent=2))
