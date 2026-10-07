"""Small, dependency-free source adapters. Metadata and excerpts only."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
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
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
RSS1 = "{http://purl.org/rss/1.0/}"
MIN_ARTICLE_CHARS = 2000
MIN_X_CHARS = 800

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
    "Cloudflare Engineering": "https://blog.cloudflare.com/rss/",
    "Netflix TechBlog": "https://netflixtechblog.com/feed",
    "Tailscale": "https://tailscale.com/blog/index.xml",
    "Dan Luu": "https://danluu.com/atom.xml",
    "Stripe Engineering": "https://stripe.com/blog/feed.rss",
    "Bert Hubert": "https://berthub.eu/articles/index.xml",
    "Antithesis": "https://antithesis.com/blog/rss.xml",
    "LessWrong": "https://www.lesswrong.com/feed.xml",
    "Astral Codex Ten": "https://www.astralcodexten.com/feed",
    "Experimental History": "https://www.experimental-history.com/feed",
    "Gwern": "https://gwern.net/rss.xml",
    "STAT": "https://www.statnews.com/feed/",
    "Nature Medicine": "https://feeds.nature.com/nm/rss/aop",
    "The Morning Paper": "https://blog.acolyer.org/feed",
    "Sebastian Raschka": "https://magazine.sebastianraschka.com/feed",
    "Geohot": "https://geohot.github.io/blog/feed.xml",
    "ML@CMU": "https://blog.ml.cmu.edu/feed",
    "Jay Alammar": "https://jalammar.github.io/feed.xml",
    "inFERENCe": "https://www.inference.vc/rss",
    "Steph Ango": "https://stephango.com/feed.xml",
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
    "Geoffrey Hinton": "cb6169815e2e447e8e6148a4af3f9686",
    "Greg Brockman": "af19d054e26a49129f23abfa82d9e268",
    "Ian Goodfellow": "57831559d22440debbfb2f2528e4ba84",
    "Lilian Weng": "a8f7e2238039461cbc8bf55f5f194498",
    "Arthur Mensch": "d8121d969fb34c7daad2dd2aac4ba270",
    "Jan Leike": "dceb5cd131b34c72a8376cba8ea5d864",
    "Mustafa Suleyman": "394acfaff8c44e09936f5bc0b8504f2c",
    "Anthropic": "fc28a211471b496682feff329ec616e5",
    "Google DeepMind": "a99538443a484fcc846bdcc8f50745ec",
    "DeepSeek": "68b610deb24b47ae9a236811563cda86",
    "Paul Graham": "900549ddadf04e839d3f7a17ebaba3fc",
    "Naval Ravikant": "b43bc203409e4c5a9c3ae86fe1ac00c9",
    "Marc Andreessen": "63316630d94543f5a6480f230f483008",
    "Sahil Lavingia": "baad3713defe4182844d2756b4c2c9ed",
    "Simon Willison": "30ad80be93c84e44acc37d5ddf31db57",
    "Jerry Liu": "b3d904c0d7c446558ef3a1e7f2eb362b",
    "Gary Marcus": "35a38c5646d946fb894d8c30c1d9629e",
    "Lex Fridman": "adf65931519340f795e2336910b4cd15",
    "Harrison Chase": "f299207df53745bca04a03db8d11c5aa",
    "Philipp Schmid": "ce352bbf72e44033985bc756db2ee0e2",
    "Junyang Lin": "082097117b4543e9a741cd2580f936d3",
    "Richard Socher": "4d2d4165a7524217a08d3f57f27fa190",
    "Latent Space": "a7be8b61a1264ea7984abfaea3eff686",
    "Guillermo Rauch": "e8750659b8154dbfa0489f451e044af1",
    "Justine Moore": "c61046471f174d86bc0eb76cb44a21c3",
    "Suhail": "c961547e08df4396b3ab69367a07a1cd",
    "Anton Osika": "5f13b32b124a41cfb659f903a84032b1",
}

TOPIC_RE = re.compile(
    r"\b(agents?|agentic|llms?|language models?|transformers?|reinforcement learning|"
    r"post.training|alignment|reasoning|inference|machine learning|deep learning|"
    r"neural|generative ai|diffusion|evals?|benchmarks?|fine.tun|rlhf|dpo|"
    r"robotics|multimodal|tool.use|context engineering|ai research|open source ai|"
    r"distributed systems?|databases?|consensus|reliability|security|hacking|"
    r"medicine|medical|health|philosophy|essay|humor|funny)\b",
    re.I,
)

HN_QUERIES = (
    "AI agents", "LLM agents", "post training", "reinforcement learning", "AI security",
    "agent infrastructure", "human data", "distributed systems", "database internals",
    "consensus", "reliability engineering", "systems programming", "machine learning systems",
    "computer security", "philosophy", "essays", "humor", "medicine", "health research",
)

ARXIV_BACKFILL_LIMITS = {
    "cs.AI": 330, "cs.LG": 330, "cs.CL": 330, "stat.ML": 330,
    "cs.DC": 180, "cs.NI": 180, "cs.OS": 180, "cs.CR": 180,
    "cs.DB": 300, "cs.SE": 300, "cs.PF": 300, "cs.DS": 300, "q-bio.QM": 300,
}


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
            "tags": category, "content_chars": len(clean(entry.findtext(f"{ATOM}summary"))),
        })
    return result


def parse_feed(name: str, url: str, limit: int = 100) -> list[dict]:
    root = ET.fromstring(fetch(url))
    is_atom = root.tag.endswith("feed")
    is_rdf = root.tag.endswith("RDF")
    entries = (root.findall(f"{ATOM}entry") if is_atom else
               root.findall(f"{RSS1}item") if is_rdf else root.findall("./channel/item"))
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
            link = entry.findtext(f"{RSS1}link" if is_rdf else "link")
            title = entry.findtext(f"{RSS1}title" if is_rdf else "title")
            summary = (entry.findtext(f"{CONTENT}encoded") or
                       entry.findtext(f"{RSS1}description" if is_rdf else "description"))
            author = entry.findtext(f"{DC}creator") or entry.findtext("author")
            published = entry.findtext("pubDate") or entry.findtext(f"{DC}date")
        if not link or not title:
            continue
        if name == "STAT" and clean(title).startswith("STAT+"):
            continue
        body = clean(summary)
        result.append({
            "id": "url:" + link.strip().rstrip("/"),
            "title": clean(title), "url": link.strip(), "source": name,
            "kind": "blog", "summary": body[:1800],
            "author": clean(author) or name, "published": iso_date(published), "tags": "",
            "content_chars": len(body),
        })
    missing = [item for item in result if item["content_chars"] < MIN_ARTICLE_CHARS]
    if missing:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for item, details in zip(missing, pool.map(lambda x: article_details(x["url"]), missing)):
                if details:
                    preview, size = details
                    item["content_chars"] = size
                    if len(item["summary"]) < 100:
                        item["summary"] = preview[:1800]
    return [item for item in result if item["content_chars"] >= MIN_ARTICLE_CHARS]


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


def article_details(url: str) -> tuple[str, int] | None:
    try:
        page = PageMetadata()
        page.feed(fetch(url, timeout=16).decode("utf-8", "replace"))
        description = clean(page.meta.get("og:description") or page.meta.get("description"))
        if len(description) >= 100 and not description.lower().startswith(("anthropic is an ai safety", "a blog post by")):
            preview = description
        else:
            preview = " ".join(page.paragraphs[:3]) or description
        return preview, sum(map(len, page.paragraphs))
    except Exception:
        return None


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
            content_chars = sum(map(len, page.paragraphs))
            if content_chars < MIN_ARTICLE_CHARS:
                return None
            return {"id": "url:" + link.rstrip("/"), "title": title, "url": link,
                    "source": name, "kind": "blog", "summary": description[:1800],
                    "author": name, "published": iso_date(published), "tags": "",
                    "content_chars": content_chars}
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
        if len(body) < MIN_X_CHARS:
            continue
        title = clean(entry.findtext("title"))
        if title.endswith("..."):
            title = title[:-3].strip() + "…"
        items.append({"id": "x:" + link.rstrip("/").split("/")[-1],
                      "title": title or body[:110] + "…", "url": link,
                      "source": f"X · {name}", "kind": "x", "summary": body[:1800],
                      "author": name, "published": iso_date(entry.findtext("pubDate")), "tags": "long-form X post",
                      "content_chars": len(body)})
    return items


def hn_candidate(item: dict) -> dict | None:
    try:
        item_id = item.get("objectID") or item.get("id")
        title = clean(item.get("title"))
        link = item.get("url")
        if not item_id or not title or not link or urlparse(link).scheme not in ("http", "https"):
            return None
        if urlparse(link).netloc.endswith(("youtube.com", "youtu.be", "github.com", "arxiv.org")):
            return None
        details = article_details(link)
        if not details or details[1] < MIN_ARTICLE_CHARS:
            return None
        preview, size = details
        published = item.get("created_at") or datetime.fromtimestamp(item.get("time", time.time()), timezone.utc).isoformat()
        return {
            "id": f"hn:{item_id}", "title": title, "url": link,
            "source": "Hacker News", "kind": "hn", "summary": preview[:1800],
            "author": item.get("author") or item.get("by") or "HN",
            "published": iso_date(published), "tags": urlparse(link).netloc.removeprefix("www."),
            "content_chars": size,
        }
    except Exception:
        return None


def hn_stories(backfill: bool = False) -> list[dict]:
    ids = json.loads(fetch("https://hacker-news.firebaseio.com/v0/topstories.json"))[:200]
    def load_current(item_id):
        try:
            return json.loads(fetch(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json", timeout=12))
        except Exception:
            return None
    with ThreadPoolExecutor(max_workers=16) as pool:
        current = list(pool.map(load_current, ids))
    candidates = [item for item in current if item and item.get("type") == "story"
                  and not item.get("dead") and not item.get("deleted")
                  and TOPIC_RE.search(clean(item.get("title")) + " " + clean(item.get("text")))]
    if backfill:
        cutoff = int((datetime.now(timezone.utc) - timedelta(days=3 * 365)).timestamp())
        queries = [(query, page) for query in HN_QUERIES for page in range(2)]
        def search(pair):
            query, page = pair
            params = urlencode({"query": query, "tags": "story", "hitsPerPage": 100, "page": page,
                              "numericFilters": f"points>=25,created_at_i>{cutoff}"})
            try:
                return json.loads(fetch(f"https://hn.algolia.com/api/v1/search?{params}"))["hits"]
            except Exception:
                return []
        with ThreadPoolExecutor(max_workers=6) as pool:
            for batch in pool.map(search, queries):
                candidates.extend(batch)
    unique = {}
    for item in candidates:
        item_id = str(item.get("objectID") or item.get("id") or "")
        if item_id:
            unique[item_id] = item
    # A relevance backfill should still favor well-received, recent stories.
    ordered = sorted(unique.values(), key=lambda x:
        (x.get("points", x.get("score", 0)) or 0) +
        0.002 * (x.get("created_at_i", x.get("time", 0)) or 0) / 86400, reverse=True)
    with ThreadPoolExecutor(max_workers=16) as pool:
        return [item for item in pool.map(hn_candidate, ordered[:1800 if backfill else 200]) if item]


def collect(backfill: bool = False) -> tuple[list[dict], dict[str, str]]:
    jobs = {name: (parse_feed, name, url) for name, url in FEEDS.items()}
    jobs.update({name: (sitemap_articles, name, *args) for name, args in SITEMAPS.items()})
    jobs.update({f"X · {name}": (x_long_posts, name, feed_id) for name, feed_id in X_FEEDS.items()})
    jobs["Hacker News"] = (hn_stories, backfill)
    items, status = [], {}
    # arXiv requests are sequential to respect its request cadence.
    for cat, backfill_limit in ARXIV_BACKFILL_LIMITS.items():
        try:
            batch = arxiv_category(cat, backfill_limit if backfill else
                                   90 if cat in ("cs.AI", "cs.LG", "cs.CL", "stat.ML") else 45)
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
