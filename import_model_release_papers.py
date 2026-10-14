"""Import the reviewed language-model release papers into the AttentionSpan catalog.

The selection lives in data/model_release_paper_selection.json. Paper metadata comes
from arXiv's own abstract pages. Existing catalog entries and votes are left alone.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen

from storage import connect_databases, init_storage

ROOT = Path(__file__).resolve().parent
SELECTION = ROOT / "data" / "model_release_paper_selection.json"


class ArxivMetadata(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.fields: dict[str, list[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "meta":
            return
        values = dict(attrs)
        name, content = values.get("name"), values.get("content")
        if name and content and name.startswith("citation_"):
            self.fields.setdefault(name, []).append(content)


def fetch_paper(group: str, arxiv_id: str) -> dict[str, str | int]:
    url = f"https://arxiv.org/abs/{arxiv_id}"
    request = Request(url, headers={"User-Agent": "AttentionSpan/0.1 (paper catalog)"})
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=20) as response:
                parser = ArxivMetadata()
                parser.feed(response.read().decode("utf-8"))
            fields = parser.fields
            title = fields["citation_title"][0].strip()
            abstract = fields["citation_abstract"][0].strip()
            date = fields["citation_date"][0].replace("/", "-")
            authors = fields.get("citation_author", [])
            if not title or len(abstract) < 100 or not authors:
                raise ValueError(f"Incomplete arXiv metadata: {arxiv_id}")
            return {
                "id": f"arxiv:{arxiv_id}", "title": title, "url": url,
                "source": group, "kind": "paper", "author": ", ".join(authors[:5]) +
                (" et al." if len(authors) > 5 else ""),
                "published": f"{date}T00:00:00+00:00",
                "content_chars": len(abstract), "discovery_source": "Manual Handpicked",
                "category": "research_paper", "summary": abstract[:1800],
                "tags": f"{group}; language model; model release",
            }
        except Exception as error:
            last_error = error
            time.sleep(attempt + 1)
    raise RuntimeError(f"Could not fetch {url}: {last_error}")


def main() -> None:
    selection = json.loads(SELECTION.read_text())
    selected = [(group, paper_id) for group, ids in selection["papers"].items()
                for paper_id in ids]
    if len(selected) != len({paper_id for _, paper_id in selected}):
        raise ValueError("Duplicate arXiv ID in selection")

    papers = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch_paper, group, paper_id): paper_id
                   for group, paper_id in selected}
        for future in as_completed(futures):
            papers.append(future.result())

    now = datetime.now(timezone.utc).isoformat()
    added = 0
    with connect_databases() as connection:
        init_storage(connection)
        for paper in papers:
            cursor = connection.execute("""INSERT OR IGNORE INTO catalog.items
                (id,title,url,source,kind,author,published,content_chars,
                 discovery_source,category,added_at)
                VALUES (:id,:title,:url,:source,:kind,:author,:published,
                        :content_chars,:discovery_source,:category,:added_at)""",
                {**paper, "added_at": now})
            added += cursor.rowcount
            connection.execute("""INSERT OR IGNORE INTO item_content
                (item_id,summary,tags,added_at) VALUES (:id,:summary,:tags,:added_at)""",
                {**paper, "added_at": now})
    print(f"Selected {len(papers)} papers; added {added} catalog entries.")


if __name__ == "__main__":
    main()
