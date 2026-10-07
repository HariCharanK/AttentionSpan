# Paper Swipe

A private, localhost research reader for papers, engineering blogs, long-form X posts, and Hacker News. Swipe right for more like this or left for less. These are preference signals, not bookmarks or reading plans. Votes persist in SQLite and tune a lightweight similarity ranker. Voted items stay out of Discover.

## Run

```bash
python3 app.py
```

Open <http://127.0.0.1:8765>. Python 3.10+ is the only requirement. The first launch fetches the corpus and can take a minute. The app refreshes on startup when the last fetch is over 24 hours old, checks hourly while running, and has a **Refresh sources** button.

The database lives in `data/papers.sqlite3` and is tracked in Git. It contains the corpus and all votes. Commit it again when you want a new snapshot of your preferences. SQLite's temporary `-wal` and `-shm` files are ignored.

## Current sources

- arXiv: recent `cs.AI`, `cs.LG`, `cs.CL`, and `stat.ML` papers
- Hacker News: relevant recent front-page stories
- RSS/Atom: OpenAI, Google Research, Google AI, Google DeepMind, Microsoft Research, NVIDIA AI, Hugging Face, Mistral, BAIR, Lilian Weng, Simon Willison, Interconnects
- Official site sitemaps and article pages: Anthropic, Thinking Machines Lab, Prime Intellect, Goodfire, Reflection AI
- Long-form X posts: selected researchers from your Following list, read through a public feed mirror and linked back to the original post

Feed URLs and source limits are in `sources.py`. Failed sources do not prevent the others from refreshing.

## Ranking

The initial rank mixes a topic-keyword prior, publication recency, and a small source-diversity bonus. After votes, it adds TF-IDF cosine similarity to liked items and subtracts similarity to passed items. The displayed fit score is a heuristic, not a calibrated probability. The corpus and vote history are kept independently so this ranker can later be replaced with embeddings, a learned model, or reconsideration of old downvotes.

The **Network** view connects items whose text has high cosine similarity. These are content links, not citation links. The **Liked** view is positive-vote history, not a bookmark list.
