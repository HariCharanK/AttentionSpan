# AttentionSpan

A localhost research reader for papers, engineering blogs, long-form X posts, and Hacker News. Swipe right for more like this or left for less. These are preference signals, not bookmarks or reading plans. Votes persist in SQLite and tune a lightweight similarity ranker. Voted items stay out of Discover.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open <http://127.0.0.1:8765>. The first launch performs a historical backfill and can take several minutes. The app refreshes on startup when the last fetch is over 24 hours old, checks hourly while running, and has a **Refresh sources** button. The historical HN and arXiv fetch runs once; daily refreshes stay smaller.

The database lives in `data/papers.sqlite3`. It contains the corpus, votes, generated profiles, and embeddings. It stays local and is ignored by Git because semantic indexes can grow substantially. SQLite's temporary `-wal` and `-shm` files are ignored too.

## Current sources

- arXiv: AI, ML, language models, distributed computing, databases, networks, security, software engineering, performance, algorithms, and quantitative biology categories
- Hacker News: recent front-page stories and a one-time, topic-focused backfill from HN Search
- RSS/Atom: AI labs and researchers above, plus Cloudflare, Netflix TechBlog, Tailscale, Dan Luu, Stripe, Antithesis, The Morning Paper, LessWrong, Astral Codex Ten, Experimental History, Gwern, STAT, Nature Medicine, and others
- Ramp Builders: official RSS feed, with article length checked against the text bundled by its JavaScript site
- Ramp Labs: research articles from its official research index
- Official site sitemaps and article pages: Anthropic, Thinking Machines Lab, Prime Intellect, Goodfire, Reflection AI
- Long-form X posts: selected researchers and labs, read through a public feed mirror and linked back to the original post

Blogs and linked HN stories must have at least 2,000 characters of extracted article text (roughly 300 words). X posts must have at least 800 characters. arXiv entries link to full papers; their abstracts are stored as previews. The database stores article excerpts and measured lengths, not full copies. Older short items remain in SQLite for vote history but no longer enter Discover.

Feed URLs and source limits are in `sources.py`. Failed sources do not prevent the others from refreshing.

`items.category` records the content format: `research_paper`, `blog`, or `twitter_article`. `items.source` is the publisher shown in the app. The nullable `items.discovery_source` records where an item was first found, such as `x.com`, `Hacker News`, `slack #knowledge-sharing`, or `Manual Handpicked`. Existing records were backfilled from their ingestion adapters and known manual additions. Later refreshes preserve the first recorded discovery source. The legacy `kind` field remains an internal ingestion and ranking label, so Hacker News can be a discovery path while the linked item is categorized as a blog or paper.

## Ranking

The legacy ranker remains available for rollback. It mixes a topic-keyword prior, recency, TF-IDF similarity to liked items, and distance from passed items.

The semantic ranker uses a grounded profile generated for each document and stores both its structured JSON and the exact normalized profile text. `text-embedding-3-large` embeds that profile text at its full 3,072 dimensions as normalized float32. Interest matching uses only these profile embeddings. Exact matrix cosine search is sufficient for the current corpus size.

Positive votes are reclustered from scratch into at most 18 interest groups. Negative votes remain ordinary vote rows; their candidate-specific penalty is calculated during ranking and is not persisted. The main score is 60% semantic relevance, 20% freshness, and 20% topic prior. Feed lanes allocate 60% to best matches, 30% to fresh material, and 10% to adjacent exploration. Depth, source repetition, and feed-local near-duplicate penalties are intentionally absent.

Semantic data and ranker configuration are versioned independently. A new semantic version builds beside every prior version. It cannot be activated until it covers the full corpus, so the current active ranker continues serving during a rebuild. Once activated, daily new items can enter through freshness and topic prior until their profiles are added. Stored profile text can be reused when a later version changes only its embedding recipe.

Indexing never runs from app startup, source refresh, or an API request. It starts only through the explicit CLI command:

```bash
python semantic.py status
python semantic.py index --limit 40 --workers 3
python semantic.py activate
```

Activation fails if coverage is incomplete. Optional LLM shortlist reranking is disabled by default. The displayed fit score is a heuristic, not a calibrated probability.

The **Network** view connects items using the active ranker's content representation: TF-IDF in legacy mode and generated profile embeddings in semantic mode. These are content links, not citation links. The **Liked** view is positive-vote history, not a bookmark list.

**Skip** (the center button or ↓) leaves no positive or negative preference signal. A skipped item is held out for the next 20 card decisions, then becomes eligible for the ranked feed again. Skips and their cooldowns are stored in the same SQLite database; right and left votes still permanently hide an item from Discover.
