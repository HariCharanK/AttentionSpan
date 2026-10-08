import json
import os
import sqlite3
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

os.environ["ATTENTIONSPAN_DISABLE_AI"] = "1"

import numpy as np

import recommender
import semantic


def unit(*values):
    vector = np.asarray(values, dtype=np.float32)
    return vector / (np.linalg.norm(vector) or 1)


def item(item_id, title="A technical article", published=None):
    return {
        "id": item_id,
        "title": title,
        "url": f"https://example.com/{item_id}",
        "source": "Example",
        "kind": "blog",
        "summary": "A detailed article about systems and machine learning.",
        "author": "Author",
        "published": published or datetime.now(timezone.utc).isoformat(),
        "tags": "systems",
        "added_at": datetime.now(timezone.utc).isoformat(),
        "content_chars": 4000,
        "discovery_source": "Manual Handpicked",
        "category": "blog",
    }


def base_database(connection):
    connection.executescript("""
        CREATE TABLE items (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL,
          source TEXT NOT NULL, kind TEXT NOT NULL, summary TEXT NOT NULL,
          author TEXT NOT NULL, published TEXT NOT NULL, tags TEXT NOT NULL,
          added_at TEXT NOT NULL, content_chars INTEGER NOT NULL DEFAULT 0,
          discovery_source TEXT, category TEXT NOT NULL
        );
        CREATE TABLE votes (item_id TEXT PRIMARY KEY, vote INTEGER, voted_at TEXT);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    connection.row_factory = sqlite3.Row


class SemanticRankingTests(unittest.TestCase):
    def test_interest_groups_split_and_cap(self):
        vectors = {
            "a": unit(1, 0, 0),
            "b": unit(.95, .05, 0),
            "c": unit(0, 1, 0),
        }
        groups = recommender.interest_groups({key: 1 for key in vectors}, vectors)
        self.assertEqual(sorted(len(group["members"]) for group in groups), [1, 2])

        many = {str(index): np.eye(20, dtype=np.float32)[index] for index in range(20)}
        capped = recommender.interest_groups(
            {key: 1 for key in many}, many, threshold=.99, max_groups=18)
        self.assertEqual(len(capped), 18)
        self.assertEqual(sum(len(group["members"]) for group in capped), 20)

    def test_negative_signal_is_a_runtime_local_penalty(self):
        items = [item("clean"), item("penalized"), item("liked"), item("passed")]
        vectors = {
            "liked": unit(1, 0),
            "passed": unit(.8, .6),
            "clean": unit(.8, -.6),
            "penalized": unit(.8, .6),
        }
        votes = {"liked": 1, "passed": -1}
        ranked = recommender.rank(items, votes, vectors, {}, limit=2)
        self.assertEqual([entry["id"] for entry in ranked], ["clean", "penalized"])
        self.assertEqual(votes, {"liked": 1, "passed": -1})

    def test_votes_and_active_skip_are_excluded(self):
        items = [item("liked"), item("cooling"), item("ready")]
        vectors = {entry["id"]: unit(1, 0) for entry in items}
        ranked = recommender.rank(
            items, {"liked": 1}, vectors, {}, skips={"cooling": 12}, decision_count=10)
        self.assertEqual([entry["id"] for entry in ranked], ["ready"])

    def test_agreed_ranker_config_has_no_depth_or_feed_local_penalties(self):
        config = semantic.RANKER_CONFIG
        self.assertEqual(
            (config["semantic_weight"], config["freshness_weight"], config["topic_prior_weight"]),
            (.60, .20, .20),
        )
        self.assertFalse(config["depth_score"])
        self.assertFalse(config["source_repetition_penalty"])
        self.assertFalse(config["near_duplicate_penalty"])


class SemanticStorageTests(unittest.TestCase):
    def test_profile_text_is_stable_and_stored_before_embedding(self):
        description = {
            "gist": "A concrete result.",
            "themes": ["agents", "evaluation"],
            "technical_detail": "The authors compare two systems.",
            "voice": "Technical",
            "cluster_label": "Agent evaluation",
        }
        text = semantic.profile_text(item("doc", "Paper title"), description)
        self.assertEqual(
            text,
            "Title: Paper title\nGist: A concrete result.\nThemes: agents; evaluation\n"
            "Technical detail: The authors compare two systems.\nVoice: Technical\n"
            "Cluster label: Agent evaluation",
        )

    def test_embedding_is_normalized_float32(self):
        with patch.object(semantic, "EMBED_DIMENSIONS", 4), patch.object(
            semantic, "api_post", return_value={"data": [{"embedding": [3, 4, 0, 0]}]}
        ):
            blob = semantic.normalized_embedding("profile", "test-key")
        vector = np.frombuffer(blob, dtype="<f4")
        self.assertEqual(blob.__len__(), 16)
        self.assertAlmostEqual(float(np.linalg.norm(vector)), 1.0, places=6)

    def test_activation_requires_complete_coverage(self):
        connection = sqlite3.connect(":memory:")
        base_database(connection)
        document = item("doc")
        connection.execute(
            "INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(document.values()))
        semantic.init_schema(connection)
        with self.assertRaisesRegex(RuntimeError, "incomplete index"):
            semantic.activate(connection)

        description = {"gist": "g", "themes": [], "technical_detail": "t",
                       "voice": "v", "cluster_label": "c"}
        text = semantic.profile_text(document, description)
        connection.execute(
            """INSERT INTO semantic_items
               (item_id,semantic_version,source_content_hash,source_text,source_text_hash,
                profile_json,profile_text,embedding_hash,profile_embedding,extraction,generated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            ("doc", semantic.SEMANTIC_VERSION, semantic.item_content_hash(document),
             "source document", "source-hash", json.dumps(description), text, semantic.embedding_hash(text),
             np.zeros(semantic.EMBED_DIMENSIONS, dtype="<f4").tobytes(), "excerpt", semantic.now()),
        )
        connection.execute(
            "UPDATE semantic_items SET source_content_hash='stale' WHERE item_id='doc'")
        with self.assertRaisesRegex(RuntimeError, "incomplete index"):
            semantic.activate(connection)
        connection.execute(
            "UPDATE semantic_items SET source_content_hash=? WHERE item_id='doc'",
            (semantic.item_content_hash(document),),
        )
        semantic.activate(connection)
        pointers = dict(connection.execute("SELECT key,value FROM meta"))
        self.assertEqual(pointers["active_semantic_version"], semantic.SEMANTIC_VERSION)
        self.assertEqual(pointers["active_recommender_version"], semantic.RECOMMENDER_VERSION)

    def test_profile_can_be_reused_when_only_embedding_version_changes(self):
        connection = sqlite3.connect(":memory:")
        base_database(connection)
        document = item("doc")
        connection.execute(
            "INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(document.values()))
        semantic.init_schema(connection)
        connection.execute(
            "INSERT INTO semantic_versions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("old", semantic.PROFILE_MODEL, semantic.PROFILE_PROMPT_VERSION,
             semantic.EXTRACTION_VERSION, "older-text", "older-embedding", 2,
             "float32", "inactive", semantic.now(), None),
        )
        connection.execute(
            """INSERT INTO semantic_items
               (item_id,semantic_version,source_content_hash,source_text,source_text_hash,
                profile_json,profile_text,embedding_hash,profile_embedding,extraction,generated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            ("doc", "old", semantic.item_content_hash(document), "saved source text", "source-hash",
             '{"gist":"saved"}', "saved profile text", "hash", unit(1, 0).astype("<f4").tobytes(),
             "article", semantic.now()),
        )
        self.assertEqual(
            semantic.reusable_profile(connection, document, semantic.SEMANTIC_VERSION),
            ('{"gist":"saved"}', "saved profile text", "article",
             "saved source text", "source-hash"),
        )

    def test_index_row_keeps_exact_text_supplied_to_profile_model(self):
        document = item("doc")
        description = {"gist": "g", "themes": [], "technical_detail": "t",
                       "voice": "v", "cluster_label": "c"}
        extracted = "The complete extracted document supplied to the profile model."
        with patch.object(semantic, "source_text", return_value=(extracted, "article")), \
             patch.object(semantic, "generate_profile", return_value=description), \
             patch.object(semantic, "normalized_embedding", return_value=b"vector"):
            row = semantic.index_one(document, "test-key")
        self.assertEqual(row[3], extracted)
        self.assertEqual(row[4], semantic.sha256(extracted.encode()).hexdigest())
        self.assertEqual(row[5], json.dumps(description, ensure_ascii=False, sort_keys=True))

    def test_old_semantic_table_is_migrated_in_place(self):
        connection = sqlite3.connect(":memory:")
        base_database(connection)
        connection.execute("""CREATE TABLE semantic_items (
          item_id TEXT NOT NULL, semantic_version TEXT NOT NULL,
          source_content_hash TEXT NOT NULL, profile_json TEXT NOT NULL,
          profile_text TEXT NOT NULL, embedding_hash TEXT NOT NULL,
          profile_embedding BLOB NOT NULL, extraction TEXT NOT NULL,
          generated_at TEXT NOT NULL, PRIMARY KEY (item_id, semantic_version))""")
        semantic.init_schema(connection)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(semantic_items)")}
        self.assertIn("source_text", columns)
        self.assertIn("source_text_hash", columns)

    def test_schema_initialization_never_calls_openai(self):
        connection = sqlite3.connect(":memory:")
        base_database(connection)
        with patch.object(semantic, "api_post", side_effect=AssertionError("unexpected API call")) as call:
            semantic.init_schema(connection)
        call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
