# AttentionSpan

A local, swipe-first semantic recommender for research papers, engineering blogs, long-form X posts, and Hacker News.

Swipe right to ask for more like an item and left to ask for less. A vote is a preference signal; it does not imply that you read, saved, or endorsed the item. Voted items leave the discovery feed, and every decision updates the next ranking.

## What it does

- Collects long-form work from arXiv, Hacker News, research labs, company engineering blogs, independent writers, and selected X accounts.
- Refreshes the corpus daily and preserves where each item was first discovered.
- Learns multiple interests from positive votes and applies item-specific penalties from negative votes.
- Mixes strong matches, fresh work, and adjacent exploration in one feed.
- Shows liked history and a semantic network of related ideas.
- Runs as a small localhost web app backed by SQLite.

## Run it

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open <http://127.0.0.1:8765>.

The checked-in catalog gives a new clone an immediate feed. AttentionSpan then refreshes source metadata in the background when the last fetch is more than 24 hours old. Use **Refresh sources** to request one manually.

## Public catalog and local state

AttentionSpan deliberately keeps two SQLite artifacts with different jobs:

| File | Tracked | Contents |
| --- | --- | --- |
| `data/catalog.sqlite3.gz` | Yes | Compressed public item metadata only |
| `data/papers.sqlite3` | No | Runtime corpus, votes, skips, reading state, extracted text, profiles, embeddings, and ranking cache |

The app uses one local runtime database for simple and fast queries. On first launch, it decompresses the catalog in memory and seeds an empty runtime database. The tracked snapshot contains IDs, titles, URLs, publishers, authors, dates, categories, discovery paths, and measured content lengths. Summaries and extracted article text stay local. This keeps the useful source list forkable without publishing personal preferences or adding hundreds of megabytes of text and embeddings to Git.

To refresh the public snapshot after updating the corpus:

```bash
python catalog.py export
python catalog.py info
```

The export is atomic and contains only the metadata columns of the `items` table. It excludes summaries, extracted text, votes, skips, reads, model profiles, embeddings, API keys, refresh state, and caches.

## Ranking

The semantic pipeline extracts article text, asks a profile model for a grounded structured description, and embeds a stable serialization of that profile with `text-embedding-3-large` at 3,072 float32 dimensions. Exact cosine search is sufficient for the current corpus size.

Positive votes are reclustered from scratch into at most 18 interest groups. Negative votes produce candidate-specific penalties during ranking. The score combines 60% semantic relevance, 20% freshness, and 20% topic prior. The feed allocates 60% of its slots to best matches, 30% to fresh material, and 10% to adjacent exploration.

Semantic versions build beside the active version and cannot be activated until they cover the complete corpus. The legacy TF-IDF ranker remains available when no semantic version is active.

Indexing is explicit and requires `OPENAI_API_KEY`:

```bash
python semantic.py status
python semantic.py index --limit 100 --workers 8
python semantic.py activate
```

The optional LLM shortlist reranker is disabled by default. The displayed fit score is a ranking heuristic rather than a calibrated probability.

## Sources

The corpus includes:

- arXiv categories across AI, ML, language models, distributed systems, databases, networks, security, software engineering, algorithms, performance, and quantitative biology
- Hacker News stories and a topic-focused historical backfill
- AI lab and company writing from OpenAI, Anthropic, Google DeepMind, Microsoft Research, NVIDIA, Hugging Face, Mistral, BAIR, Ramp, Cloudflare, Netflix, Stripe, Tailscale, Antithesis, and emerging research labs
- Independent technical and long-form writing from Simon Willison, Dan Luu, Lilian Weng, Gwern, LessWrong, Astral Codex Ten, Experimental History, and others
- Long-form posts from selected researchers, builders, labs, and writers on X

Blogs and HN links need at least 2,000 extracted characters to enter the feed. X posts need at least 800. Explicitly hand-picked items bypass this length gate. Feed definitions and collection limits live in `sources.py`; one failed source does not block the rest.

`items.category` describes the format: `research_paper`, `blog`, or `twitter_article`. `items.source` is the publisher displayed in the app. `items.discovery_source` records the first discovery path, such as `x.com`, `Hacker News`, `slack #knowledge-sharing`, or `Manual Handpicked`.

## Interaction semantics

- **Right:** a positive preference signal
- **Left:** a negative preference signal
- **Skip:** no preference signal; hide the item for the next 20 decisions
- **Liked:** positive-vote history, separate from read state
- **Network:** similarity links between content, not citation links
