"""Small, dependency-free source adapters. Metadata and excerpts only."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
import gzip
import json
import re
import time
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

USER_AGENT = "AttentionSpan/0.1 (personal localhost research reader)"
ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/elements/1.1/}"

FEEDS = {
    "OpenAI": "https://openai.com/news/rss.xml",
    "Google Research": "https://research.google/blog/rss/",
    "Google AI": "https://blog.google/innovation-and-ai/technology/ai/rss/",
    "Google DeepMind": "https://deepmind.google/blog/rss.xml",
    "Microsoft Research": "https://www.microsoft.com/en-us/research/feed/",
    "NVIDIA AI": "https://developer.nvidia.com/blog/category/generative-ai/feed/",
    "Hugging Face": "https://huggingface.co/blog/feed.xml",
    "Mistral AI": "https://mistral.ai/news/rss",
    "BAIR": "https://bair.berkeley.edu/blog/feed.xml",
    "Lilian Weng": "https://lilianweng.github.io/index.xml",
    "Simon Willison": "https://simonwillison.net/atom/everything/",
    "Interconnects": "https://www.interconnects.ai/feed",
}

SITEMAPS = {
    "Anthropic": ("https://www.anthropic.com/sitemap.xml", ("/research/", "/engineering/"), 65),
    "Thinking Machines": ("https://thinkingmachines.ai/sitemap.xml", ("/blog/", "/news/"), 35),
    "Prime Intellect": ("https://www.primeintellect.ai/sitemap.xml", ("/blog/", "/research/"), 45),
    "Goodfire": ("https://www.goodfire.com/sitemap.xml", ("/blog/", "/research/"), 45),
    "Reflection AI": ("https://reflection.ai/sitemap.xml", ("/blog/",), 20),
}

# Public X feed mirrors: long posts only. Each item links back to the original post.
X_FEEDS = {
    "Andrej Karpathy": "edf707b5c0b248579085f66d7a3c5524",
    "Jim Fan": "c6cfe7c0d6b74849997073233fdea840",
    "Thomas Wolf": "4918efb13c47459b8dcaa79cfdf72d09",
    "Alex Albert": "524525de0d69407b80f0a7d891fdc8df",
    "Yann LeCun": "f5f4f928dede472ea55053672ad27ab6",
    "Andrew Ng": "08b5488b20bc437c8bfc317a52e5c26d",
    "Fei-Fei Li": "a4bfe44bfc0d4c949da21ebd3f5f42a5",
    "Demis Hassabis": "4a884d5e2f3740c5a26c9c093de6388a",
    "Jeff Dean": "b1013166769c49f8aa3fbdc222867054",
    "Dario Amodei": "49666ce6fe3e4cb786c6574684542ec5",
}

TOPIC_RE = re.compile(
    r"\b(agents?|agentic|llms?|language models?|transformers?|reinforcement learning|"
    r"post.training|alignment|reasoning|inference|machine learning|deep learning|"
    r"neural|generative ai|diffusion|evals?|benchmarks?|fine.tun|rlhf|dpo|"
    r"robotics|multimodal|tool.use|context engineering|ai research|open source ai)\b",
    re.I,
)


def fetch(url: str, timeout: int = 25) -> bytes:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; AttentionSpan/0.1; personal reader)", "Accept": "application/atom+xml, application/rss+xml, application/json, text/html, */*"})
    with urlopen(req, timeout=timeout) as response:
        body = response.read(8_000_000)
        return gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body


def clean(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", unescape(value)).strip()


def iso_date(value: str | None) -> str:
    if not value:
        return datetime.now(timezone.utc).isoformat()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except ValueError:
        try:
            return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
        except (ValueError, TypeError):
            return datetime.now(timezone.utc).isoformat()


def arxiv_category(category: str, limit: int = 90) -> list[dict]:
    query = urlencode({
        "search_query": f"cat:{category}", "start": 0, "max_results": limit,
        "sortBy": "submittedDate", "sortOrder": "descending",
    })
    root = ET.fromstring(fetch(f"https://export.arxiv.org/api/query?{query}"))
    result = []
    for entry in root.findall(f"{ATOM}entry"):
        url = (entry.findtext(f"{ATOM}id") or "").replace("http://", "https://")
        if not url:
            continue
        authors = [clean(a.findtext(f"{ATOM}name")) for a in entry.findall(f"{ATOM}author")]
        result.append({
            "id": "arxiv:" + url.rstrip("/").split("/")[-1].split("v")[0],
            "title": clean(entry.findtext(f"{ATOM}title")),
            "url": url,
            "source": "arXiv",
            "kind": "paper",
            "summary": clean(entry.findtext(f"{ATOM}summary"))[:1800],
            "author": ", ".join(authors[:5]) + (" et al." if len(authors) > 5 else ""),
            "published": iso_date(entry.findtext(f"{ATOM}published")),
            "tags": category,
        })
    return result


def parse_feed(name: str, url: str, limit: int = 65) -> list[dict]:
    root = ET.fromstring(fetch(url))
    is_atom = root.tag.endswith("feed")
    entries = root.findall(f"{ATOM}entry") if is_atom else root.findall("./channel/item")
    result = []
    for entry in entries[:limit]:
        if is_atom:
            links = entry.findall(f"{ATOM}link")
            link = next((x.get("href") for x in links if x.get("rel", "alternate") == "alternate"), None)
            title = entry.findtext(f"{ATOM}title")
            summary = entry.findtext(f"{ATOM}summary") or entry.findtext(f"{ATOM}content")
            author = entry.findtext(f"{ATOM}author/{ATOM}name")
            published = entry.findtext(f"{ATOM}published") or entry.findtext(f"{ATOM}updated")
        else:
            link = entry.findtext("link")
            title = entry.findtext("title")
            summary = entry.findtext("description")
            author = entry.findtext(f"{DC}creator") or entry.findtext("author")
            published = entry.findtext("pubDate") or entry.findtext(f"{DC}date")
        if not link or not title:
            continue
        result.append({
            "id": "url:" + link.strip().rstrip("/"),
            "title": clean(title), "url": link.strip(), "source": name,
            "kind": "blog", "summary": clean(summary)[:1800],
            "author": clean(author) or name, "published": iso_date(published), "tags": "",
        })
    missing = [item for item in result[:25] if len(item["summary"]) < 60]
    if missing:
        with ThreadPoolExecutor(max_workers=5) as pool:
            for item, preview in zip(missing, pool.map(lambda x: article_preview(x["url"]), missing)):
                if preview:
                    item["summary"] = preview[:1800]
    return result


class PageMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}
        self.title = ""
        self.paragraphs = []
        self._in_title = False
        self._in_p = False
        self._p_parts = []
        self._ignored = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = attrs.get("property") or attrs.get("name") or attrs.get("itemprop")
            if key and attrs.get("content"):
                self.meta[key.lower()] = attrs["content"]
        elif tag == "title":
            self._in_title = True
        elif tag in ("script", "style", "nav", "footer"):
            self._ignored += 1
        elif tag == "p" and not self._ignored:
            self._in_p = True
            self._p_parts = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in ("script", "style", "nav", "footer"):
            self._ignored = max(0, self._ignored - 1)
        elif tag == "p" and self._in_p:
            paragraph = clean("".join(self._p_parts))
            if len(paragraph) >= 65:
                self.paragraphs.append(paragraph)
            self._in_p = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._in_p:
            self._p_parts.append(data)


def article_preview(url: str) -> str:
    try:
        page = PageMetadata()
        page.feed(fetch(url, timeout=16).decode("utf-8", "replace"))
        description = clean(page.meta.get("og:description") or page.meta.get("description"))
        if len(description) >= 100 and not description.lower().startswith(("anthropic is an ai safety", "a blog post by")):
            return description
        return " ".join(page.paragraphs[:3]) or description
    except Exception:
        return ""


def sitemap_articles(name: str, url: str, paths: tuple[str, ...], limit: int) -> list[dict]:
    root = ET.fromstring(fetch(url))
    ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    links = []
    for entry in root.findall(f"{ns}url"):
        link = entry.findtext(f"{ns}loc") or ""
        if any(path in link for path in paths):
            links.append((link, entry.findtext(f"{ns}lastmod")))
    links.sort(key=lambda x: x[1] or "", reverse=True)

    def one(pair):
        link, modified = pair
        try:
            page = PageMetadata()
            page.feed(fetch(link, timeout=18).decode("utf-8", "replace"))
            title = clean(page.meta.get("og:title") or page.title).split(" | ")[0].split(" \\ ")[0]
            if not title:
                return None
            description = clean(page.meta.get("og:description") or page.meta.get("description"))
            if len(description) < 90 or description.lower().startswith(("anthropic is an ai safety", "learn more about")):
                description = " ".join(page.paragraphs[:3]) or description
            published = (page.meta.get("article:published_time") or page.meta.get("datepublished")
                         or page.meta.get("publishdate") or modified)
            return {"id": "url:" + link.rstrip("/"), "title": title, "url": link,
                    "source": name, "kind": "blog", "summary": description[:1800],
                    "author": name, "published": iso_date(published), "tags": ""}
        except Exception:
            return None
    with ThreadPoolExecutor(max_workers=5) as pool:
        return [item for item in pool.map(one, links[:limit]) if item]


def x_long_posts(name: str, feed_id: str) -> list[dict]:
    root = ET.fromstring(fetch(f"https://api.xgo.ing/rss/user/{feed_id}"))
    items = []
    for entry in root.findall("./channel/item"):
        link = entry.findtext("link") or ""
        if not link.startswith("https://x.com/"):
            continue
        raw = entry.findtext("description") or ""
        body = clean(raw).split("💬", 1)[0].replace("⚡ Powered by xgo.ing", "").strip()
        if len(body) < 400:
            continue
        title = clean(entry.findtext("title"))
        if title.endswith("..."):
            title = title[:-3].strip() + "…"
        items.append({"id": "x:" + link.rstrip("/").split("/")[-1],
                      "title": title or body[:110] + "…", "url": link,
                      "source": f"X · {name}", "kind": "x", "summary": body[:1800],
                      "author": name, "published": iso_date(entry.findtext("pubDate")), "tags": "long-form X post"})
    return items


def hn_stories(limit: int = 160) -> list[dict]:
    ids = json.loads(fetch("https://hacker-news.firebaseio.com/v0/topstories.json"))[:limit]
    def one(item_id: int) -> dict | None:
        try:
            item = json.loads(fetch(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json", timeout=12))
            if not item or item.get("type") != "story" or item.get("dead") or item.get("deleted"):
                return None
            title = clean(item.get("title"))
            text = clean(item.get("text"))
            if not TOPIC_RE.search(title + " " + text):
                return None
            link = item.get("url") or f"https://news.ycombinator.com/item?id={item_id}"
            return {
                "id": f"hn:{item_id}", "title": title, "url": link,
                "source": "Hacker News", "kind": "hn",
                "summary": text[:1800] or f"Hacker News discussion · {item.get('score', 0)} points · {item.get('descendants', 0)} comments",
                "author": item.get("by", "HN"),
                "published": datetime.fromtimestamp(item.get("time", time.time()), timezone.utc).isoformat(),
                "tags": urlparse(link).netloc.removeprefix("www."),
            }
        except Exception:
            return None
    with ThreadPoolExecutor(max_workers=12) as pool:
        return [item for item in pool.map(one, ids) if item]


def collect() -> tuple[list[dict], dict[str, str]]:
    jobs = {name: (parse_feed, name, url) for name, url in FEEDS.items()}
    jobs.update({name: (sitemap_articles, name, *args) for name, args in SITEMAPS.items()})
    jobs.update({f"X · {name}": (x_long_posts, name, feed_id) for name, feed_id in X_FEEDS.items()})
    jobs["Hacker News"] = (hn_stories,)
    items, status = [], {}
    # arXiv requests are sequential to respect its request cadence.
    for cat in ("cs.AI", "cs.LG", "cs.CL", "stat.ML"):
        try:
            batch = arxiv_category(cat)
            items.extend(batch)
            status[f"arXiv {cat}"] = f"{len(batch)} items"
        except Exception as exc:
            status[f"arXiv {cat}"] = f"error: {exc}"
        time.sleep(3)
    with ThreadPoolExecutor(max_workers=6) as pool:
        future_to_name = {pool.submit(fn, *args): name for name, (fn, *args) in jobs.items()}
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            try:
                batch = future.result()
                items.extend(batch)
                status[name] = f"{len(batch)} items"
            except Exception as exc:
                status[name] = f"error: {exc}"
    return items, status
