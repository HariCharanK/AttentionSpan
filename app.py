"""Local research reader: SQLite store, content ranking, and a tiny HTTP API."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse

from sources import MIN_ARTICLE_CHARS, MIN_X_CHARS, collect

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "papers.sqlite3"
PORT = 8765
REFRESH_SECONDS = 24 * 60 * 60
SKIP_COOLDOWN = 20  # Other card decisions before a skipped item is eligible again.
refresh_lock = threading.Lock()
refresh_status = {"running": False, "last_result": {}}

# These were added directly by the user, even when their publishers also have feeds.
MANUAL_ITEM_IDS = {
    "arxiv:2507.19457", "arxiv:2601.18779",
    "url:https://builders.ramp.com/post/why-we-built-our-background-agent",
    "url:https://labs.ramp.com/research/latent-briefing-kv-cache",
    "url:https://accelerateordie.com/p/we-melted-iphones-for-science",
    "url:https://www.anthropic.com/engineering/a-postmortem-of-three-recent-issues",
}

KEYWORDS = {
    "reinforcement learning": 2.7, "post-training": 3.0, "post training": 3.0,
    "rlhf": 3.2, "agentic": 2.8, "agents": 2.1, "agent": 1.4,
    "tool use": 2.3, "reasoning": 1.8, "language model": 1.5,
    "llm": 1.7, "alignment": 1.8, "inference": 1.2, "eval": 1.1,
    "benchmark": 0.9, "machine learning": 1.0, "transformer": 1.0,
    "deep learning": 0.8, "multimodal": 0.8, "fine-tun": 1.5,
    "human data": 2.2, "computer use": 2.3, "agent security": 2.0,
    "distributed systems": 1.8, "database": 1.2, "consensus": 1.3,
    "reliability": 1.0, "security": 1.2, "hacking": 1.2,
    "philosophy": 0.7, "medicine": 0.6, "health": 0.5,
}
STOP = set("a an and are as at be been by can for from has have in into is it its of on or our that the their these this to using via was were with we you your new how what which why not more than about over under one two three some all also".split())
WORD = re.compile(r"[a-z][a-z0-9-]{2,}")


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def discovery_source(item: dict) -> str | None:
    """Where the app first found an item; the existing source is its publisher."""
    if item["id"] in MANUAL_ITEM_IDS:
        return "Manual Handpicked"
    if item["kind"] == "x":
        return "x.com"
    if item["kind"] == "paper":
        return "arXiv"
    if item["kind"] == "hn":
        return "Hacker News"
    return (urlparse(item["url"]).hostname or "").lower().removeprefix("www.") or None


def content_category(item: dict) -> str:
    """The item's format, independent of its publisher and discovery path."""
    host = (urlparse(item["url"]).hostname or "").lower().removeprefix("www.")
    if item["kind"] == "paper" or host == "arxiv.org":
        return "research_paper"
    if item["kind"] == "x" or host in {"x.com", "twitter.com"}:
        return "twitter_article"
    return "blog"


def init_db() -> None:
    with connect() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS items (
              id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL,
              source TEXT NOT NULL, kind TEXT NOT NULL, summary TEXT NOT NULL,
              author TEXT NOT NULL, published TEXT NOT NULL, tags TEXT NOT NULL,
              added_at TEXT NOT NULL, content_chars INTEGER NOT NULL DEFAULT 0,
              discovery_source TEXT, category TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS votes (
              item_id TEXT PRIMARY KEY REFERENCES items(id), vote INTEGER NOT NULL,
              voted_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS skips (
              item_id TEXT PRIMARY KEY REFERENCES items(id), eligible_after INTEGER NOT NULL,
              skipped_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_items_published ON items(published DESC);
        """)
        if "content_chars" not in {row[1] for row in con.execute("PRAGMA table_info(items)")}:
            con.execute("ALTER TABLE items ADD COLUMN content_chars INTEGER NOT NULL DEFAULT 0")
        if "discovery_source" not in {row[1] for row in con.execute("PRAGMA table_info(items)")}:
            con.execute("ALTER TABLE items ADD COLUMN discovery_source TEXT")
            for row in con.execute("SELECT id, url, kind FROM items"):
                con.execute("UPDATE items SET discovery_source=? WHERE id=?",
                            (discovery_source(row), row["id"]))
        if "category" not in {row[1] for row in con.execute("PRAGMA table_info(items)")}:
            con.execute("ALTER TABLE items ADD COLUMN category TEXT")
        for row in con.execute("""SELECT id, url, kind FROM items
                                WHERE category IS NULL OR category NOT IN
                                ('research_paper', 'blog', 'twitter_article')"""):
            con.execute("UPDATE items SET category=? WHERE id=?",
                        (content_category(row), row["id"]))


def meta(con: sqlite3.Connection, key: str) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def refresh(force: bool = False) -> bool:
    if not refresh_lock.acquire(blocking=False):
        return False
    try:
        with connect() as con:
            last = meta(con, "last_refresh")
            backfill = meta(con, "long_form_backfill_v1") is None
        if not force and not backfill and last and time.time() - float(last) < REFRESH_SECONDS:
            return False
        refresh_status["running"] = True
        items, statuses = collect(backfill=backfill)
        if not items:
            refresh_status["last_result"] = {"fetched": 0, "sources": statuses}
            return False
        now = datetime.now(timezone.utc).isoformat()
        with connect() as con:
            for item in items:
                item = {**item, "discovery_source": discovery_source(item),
                        "category": content_category(item)}
                con.execute("""INSERT INTO items
                    (id,title,url,source,kind,summary,author,published,tags,added_at,content_chars,discovery_source,category)
                    VALUES (:id,:title,:url,:source,:kind,:summary,:author,:published,:tags,:added_at,:content_chars,:discovery_source,:category)
                    ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title, summary=excluded.summary,
                    author=excluded.author, tags=excluded.tags,
                    published=excluded.published, content_chars=excluded.content_chars,
                    discovery_source=COALESCE(items.discovery_source, excluded.discovery_source),
                    category=excluded.category""", {**item, "added_at": now})
            con.execute("INSERT OR REPLACE INTO meta VALUES ('last_refresh', ?)", (str(time.time()),))
            con.execute("INSERT OR REPLACE INTO meta VALUES ('source_status', ?)", (json.dumps(statuses),))
            if backfill and len(items) >= 1000:
                con.execute("INSERT OR REPLACE INTO meta VALUES ('long_form_backfill_v1', ?)", (now,))
        refresh_status["last_result"] = {"fetched": len(items), "sources": statuses}
        return True
    except Exception as exc:
        refresh_status["last_result"] = {"error": str(exc)}
        return False
    finally:
        refresh_status["running"] = False
        refresh_lock.release()


def background_loop() -> None:
    while True:
        refresh()
        time.sleep(3600)


def tokens(item: dict) -> Counter:
    title = item["title"].lower()
    text = (title + " " + title + " " + item["summary"][:1000] + " " + item["tags"]).lower()
    return Counter(word for word in WORD.findall(text) if word not in STOP)


def vectors(items: list[dict]) -> dict[str, dict[str, float]]:
    bags = {item["id"]: tokens(item) for item in items}
    df = Counter(word for bag in bags.values() for word in bag)
    n = len(items)
    result = {}
    for item_id, bag in bags.items():
        vec = {word: (1 + math.log(count)) * (1 + math.log((n + 1) / (df[word] + 1))) for word, count in bag.items()}
        norm = math.sqrt(sum(x * x for x in vec.values())) or 1
        result[item_id] = {word: weight / norm for word, weight in vec.items()}
    return result


def cosine(a: dict, b: dict) -> float:
    return sum(weight * b.get(word, 0) for word, weight in a.items())


def item_identity(item: dict) -> tuple[str, str]:
    parsed = urlparse(item["url"])
    query = urlencode([(key, value) for key, value in parse_qsl(parsed.query)
                       if not key.lower().startswith("utm_") and key.lower() not in ("ref", "source")])
    url = parsed.netloc.lower().removeprefix("www.") + parsed.path.rstrip("/") + ("?" + query if query else "")
    title = re.sub(r"[^a-z0-9]+", " ", item["title"].lower()).strip()
    return url, title if len(title) >= 35 else ""


def rank(items: list[dict], votes: dict[str, int], limit: int = 120,
         skips: dict[str, int] | None = None, decision_count: int = 0) -> list[dict]:
    vecs = vectors(items)
    liked = [vecs[item_id] for item_id, vote in votes.items() if vote == 1 and item_id in vecs]
    passed = [vecs[item_id] for item_id, vote in votes.items() if vote == -1 and item_id in vecs]
    now = datetime.now(timezone.utc)
    source_count = Counter()
    voted_identities = {identity for item in items if item["id"] in votes
                        for identity in item_identity(item) if identity}
    cooling_identities = {identity for item in items
                          if (skips or {}).get(item["id"], 0) > decision_count
                          for identity in item_identity(item) if identity}
    def group(item):
        if item["kind"] == "x":
            return "X"
        if item["source"] == "arXiv":
            return f"arXiv {item['tags']}"
        return item["source"]
    scored = []
    for item in items:
        if item["id"] in votes or (skips or {}).get(item["id"], 0) > decision_count:
            continue
        if any(identity in voted_identities or identity in cooling_identities
               for identity in item_identity(item) if identity):
            continue
        text = (item["title"] + " " + item["summary"][:400]).lower()
        prior = min(1, sum(weight for phrase, weight in KEYWORDS.items() if phrase in text) / 6)
        try:
            age = max(0, (now - datetime.fromisoformat(item["published"])).total_seconds() / 86400)
        except ValueError:
            age = 30
        freshness = math.exp(-age / 35)
        positive = max((cosine(vecs[item["id"]], v) for v in liked), default=0)
        negative = max((cosine(vecs[item["id"]], v) for v in passed), default=0)
        personal = 1.4 * positive - 0.65 * negative
        score = 0.42 * prior + 0.20 * freshness + personal
        if item["kind"] == "x":
            score += 0.06  # A small starting signal from followed research accounts.
        if item["kind"] == "hn":
            score += 0.02
        if item["kind"] != "paper":
            score += min(0.08, 0.025 * math.log1p(item["content_chars"] / 2000))
        if re.search(r"\b(introducing|announcing|launch|live blog|release notes|changelog)\b", item["title"], re.I):
            score -= 0.12
        scored.append((score, item, prior, positive))
    scored.sort(key=lambda x: x[0], reverse=True)
    unique_scored = []
    seen_identities = set()
    for candidate in scored:
        identities = {identity for identity in item_identity(candidate[1]) if identity}
        if identities & seen_identities:
            continue
        seen_identities.update(identities)
        unique_scored.append(candidate)
    scored = unique_scored
    # Greedy source balance gives the feed breadth without hiding lower-ranked items.
    result = []
    for _ in range(min(limit, len(scored))):
        best = max(range(len(scored)), key=lambda i: scored[i][0] - 0.10 * math.sqrt(source_count[group(scored[i][1])]))
        score, item, prior, positive = scored.pop(best)
        source_count[group(item)] += 1
        result.append({**item, "score": round(score, 3), "match": min(99, round(42 + score * 45)),
                       "why": "Similar to your likes" if liked and positive > 0.13 else "Matches your interests" if prior > 0.2 else "Explore something new"})
    return result


def all_data() -> tuple[list[dict], dict[str, int], dict[str, int], int]:
    with connect() as con:
        items = [dict(row) for row in con.execute("""SELECT * FROM items WHERE
            kind = 'paper' OR (kind = 'x' AND content_chars >= ?) OR
            (kind IN ('blog','hn') AND content_chars >= ?) OR
            id IN (SELECT item_id FROM votes) ORDER BY published DESC""",
            (MIN_X_CHARS, MIN_ARTICLE_CHARS))]
        votes = {row["item_id"]: row["vote"] for row in con.execute("SELECT item_id,vote FROM votes")}
        skips = {row["item_id"]: row["eligible_after"] for row in con.execute("SELECT item_id,eligible_after FROM skips")}
        decision_count = int(meta(con, "decision_count") or 0)
    return items, votes, skips, decision_count


def network(items: list[dict], votes: dict[str, int], skips: dict[str, int], decision_count: int) -> dict:
    ordered = rank(items, votes, limit=48, skips=skips, decision_count=decision_count)
    saved = [item for item in items if votes.get(item["id"]) == 1]
    selected = (sorted(saved, key=lambda x: x["published"], reverse=True)[:30] + ordered[:48])
    seen = set()
    selected = [x for x in selected if not (x["id"] in seen or seen.add(x["id"]))]
    vecs = vectors(selected)
    edges = []
    for i, a in enumerate(selected):
        neighbors = sorted(((cosine(vecs[a["id"]], vecs[b["id"]]), b["id"]) for b in selected[i+1:]), reverse=True)[:3]
        edges.extend({"source": a["id"], "target": item_id, "weight": round(sim, 3)} for sim, item_id in neighbors if sim >= 0.15)
    return {"nodes": [{"id": x["id"], "title": x["title"], "source": x["source"],
                        "kind": x["kind"], "category": x["category"], "url": x["url"],
                        "saved": votes.get(x["id"]) == 1} for x in selected],
            "edges": edges}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "static"), **kwargs)

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            return super().do_GET()
        items, votes, skips, decision_count = all_data()
        if path == "/api/feed":
            return self.send_json({"items": rank(items, votes, skips=skips, decision_count=decision_count)})
        if path in ("/api/liked", "/api/saved"):
            return self.send_json({"items": sorted([{**x, "vote": 1} for x in items if votes.get(x["id"]) == 1], key=lambda x: x["published"], reverse=True)})
        if path == "/api/network":
            return self.send_json(network(items, votes, skips, decision_count))
        if path == "/api/stats":
            with connect() as con:
                last = meta(con, "last_refresh")
                sources = json.loads(meta(con, "source_status") or "{}")
            return self.send_json({"total": len(items), "unseen": len(items) - len(votes),
                                   "saved": sum(v == 1 for v in votes.values()), "passed": sum(v == -1 for v in votes.values()),
                                   "cooling": sum(item_id not in votes and eligible > decision_count for item_id, eligible in skips.items()),
                                   "skip_cooldown": SKIP_COOLDOWN,
                                   "refreshing": refresh_status["running"], "last_refresh": float(last) if last else None,
                                   "sources": sources, "last_result": refresh_status["last_result"]})
        return self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        size = int(self.headers.get("Content-Length", "0"))
        if size > 10000:
            return self.send_json({"error": "Request too large"}, 413)
        try:
            data = json.loads(self.rfile.read(size) or b"{}")
        except ValueError:
            return self.send_json({"error": "Invalid JSON"}, 400)
        if path in ("/api/vote", "/api/skip"):
            if not isinstance(data.get("id"), str) or (path == "/api/vote" and data.get("vote") not in (-1, 1)):
                return self.send_json({"error": "Expected item id and a valid action"}, 400)
            with connect() as con:
                con.execute("BEGIN IMMEDIATE")
                if not con.execute("SELECT 1 FROM items WHERE id=?", (data["id"],)).fetchone():
                    return self.send_json({"error": "Unknown item"}, 404)
                if con.execute("SELECT 1 FROM votes WHERE item_id=?", (data["id"],)).fetchone():
                    return self.send_json({"error": "Item already voted on"}, 409)
                current = int(meta(con, "decision_count") or 0)
                if path == "/api/skip" and (row := con.execute("SELECT eligible_after FROM skips WHERE item_id=?", (data["id"],)).fetchone()) and row[0] > current:
                    return self.send_json({"error": "Item is still cooling down"}, 409)
                decision_count = current + 1
                con.execute("INSERT OR REPLACE INTO meta VALUES ('decision_count', ?)", (str(decision_count),))
                if path == "/api/skip":
                    con.execute("INSERT OR REPLACE INTO skips VALUES (?,?,?)", (data["id"], decision_count + SKIP_COOLDOWN, datetime.now(timezone.utc).isoformat()))
                else:
                    con.execute("INSERT OR REPLACE INTO votes VALUES (?,?,?)", (data["id"], data["vote"], datetime.now(timezone.utc).isoformat()))
                    con.execute("DELETE FROM skips WHERE item_id=?", (data["id"],))
            return self.send_json({"ok": True})
        if path == "/api/refresh":
            if refresh_status["running"]:
                return self.send_json({"ok": True, "running": True})
            threading.Thread(target=refresh, args=(True,), daemon=True).start()
            return self.send_json({"ok": True, "running": True})
        return self.send_json({"error": "Not found"}, 404)


if __name__ == "__main__":
    init_db()
    threading.Thread(target=background_loop, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"AttentionSpan: http://127.0.0.1:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
