# AttentionSpan

A localhost research reader for papers, engineering blogs, long-form X posts, and Hacker News. Swipe right for more like this or left for less. These are preference signals, not bookmarks or reading plans. Votes persist in SQLite and tune a lightweight similarity ranker. Voted items stay out of Discover.

## Run

```bash
python3 app.py
```

Open <http://127.0.0.1:8765>. Python 3.10+ is the only requirement. The first launch performs a historical backfill and can take several minutes. The app refreshes on startup when the last fetch is over 24 hours old, checks hourly while running, and has a **Refresh sources** button. The historical HN and arXiv fetch runs once; daily refreshes stay smaller.

The database lives in `data/papers.sqlite3` and is tracked in Git. It contains the corpus and all votes. Commit it again when you want a new snapshot of your preferences. SQLite's temporary `-wal` and `-shm` files are ignored.

## Current sources

- arXiv: AI, ML, language models, distributed computing, databases, networks, security, software engineering, performance, algorithms, and quantitative biology categories
- Hacker News: recent front-page stories and a one-time, topic-focused backfill from HN Search
- RSS/Atom: AI labs and researchers above, plus Cloudflare, Netflix TechBlog, Tailscale, Dan Luu, Stripe, Antithesis, The Morning Paper, LessWrong, Astral Codex Ten, Experimental History, Gwern, STAT, Nature Medicine, and others
- Official site sitemaps and article pages: Anthropic, Thinking Machines Lab, Prime Intellect, Goodfire, Reflection AI
- Long-form X posts: selected researchers and labs, read through a public feed mirror and linked back to the original post

Blogs and linked HN stories must have at least 2,000 characters of extracted article text (roughly 300 words). X posts must have at least 800 characters. arXiv entries link to full papers; their abstracts are stored as previews. The database stores article excerpts and measured lengths, not full copies. Older short items remain in SQLite for vote history but no longer enter Discover.

Feed URLs and source limits are in `sources.py`. Failed sources do not prevent the others from refreshing.

## Ranking

The initial rank mixes a topic-keyword prior, publication recency, and a small source-diversity bonus. After votes, it adds TF-IDF cosine similarity to liked items and subtracts similarity to passed items. The displayed fit score is a heuristic, not a calibrated probability. The corpus and vote history are kept independently so this ranker can later be replaced with embeddings, a learned model, or reconsideration of old downvotes.

The **Network** view connects items whose text has high cosine similarity. These are content links, not citation links. The **Liked** view is positive-vote history, not a bookmark list.

**Skip** (the center button or ↓) leaves no positive or negative preference signal. A skipped item is held out for the next 20 card decisions, then becomes eligible for the ranked feed again. Skips and their cooldowns are stored in the same SQLite database; right and left votes still permanently hide an item from Discover.
