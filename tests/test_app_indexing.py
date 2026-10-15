import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

import app


class FeedMoodTests(unittest.TestCase):
    def test_moods_only_select_the_requested_content_kind(self):
        items = [{"id": kind, "kind": kind} for kind in ("paper", "x", "blog", "hn")]
        self.assertEqual(app.items_for_mood(items, "all"), items)
        for mood, kind in (("papers", "paper"), ("x", "x"),
                           ("blogs", "blog"), ("hacker-news", "hn")):
            self.assertEqual(app.items_for_mood(items, mood), [{"id": kind, "kind": kind}])
        self.assertEqual(len(items), 4)

    def test_feed_endpoint_filters_before_ranking_and_rejects_unknown_moods(self):
        items = [{"id": kind, "kind": kind, "tags": "", "discovery_source": "source"}
                 for kind in ("paper", "x", "blog", "hn")]
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with patch.object(app, "all_data", return_value=(items, {}, {}, 0)), \
                    patch.object(app, "active_representations", return_value=(None, {}, {})), \
                    patch.object(app, "legacy_rank", side_effect=lambda candidates, *args, **kwargs: [
                        item for item in candidates if item["id"] in kwargs["candidate_ids"]]):
                base = f"http://127.0.0.1:{server.server_port}/api/feed"
                with urlopen(base) as response:
                    self.assertEqual({item["id"] for item in json.load(response)["items"]},
                                     {"paper", "x", "blog", "hn"})
                for mood, expected in (("papers", "paper"), ("x", "x"),
                                       ("blogs", "blog"), ("hacker-news", "hn")):
                    with urlopen(f"{base}?mood={mood}") as response:
                        self.assertEqual([item["id"] for item in json.load(response)["items"]], [expected])
                with self.assertRaises(HTTPError) as error:
                    urlopen(f"{base}?mood=unknown")
                self.assertEqual(error.exception.code, 400)
                error.exception.close()
            with patch.object(app, "all_data", return_value=(items, {}, {}, 0)), \
                    patch.object(app, "active_representations", return_value=("v1", {}, {})), \
                    patch.object(app, "semantic_rank", side_effect=lambda candidates, *args, **kwargs: candidates), \
                    patch.object(app, "apply_cached_rerank", side_effect=lambda ranked, *args: ranked):
                with urlopen(f"{base}?mood=papers") as response:
                    self.assertEqual([item["id"] for item in json.load(response)["items"]], ["paper"])
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_legacy_mood_keeps_votes_from_other_content_kinds(self):
        items = [{"id": kind, "kind": kind, "title": f"A {kind} about agents",
                  "url": f"https://example.com/{kind}", "summary": "agents",
                  "source": "Example", "tags": "", "published": "2026-10-01T00:00:00+00:00",
                  "content_chars": 3000} for kind in ("blog", "paper")]
        with patch.object(app, "vectors", return_value={kind: {"agents": 1.0}
                                                      for kind in ("blog", "paper")}):
            ranked = app.legacy_rank(items, {"blog": 1}, candidate_ids={"paper"})
        self.assertEqual([item["id"] for item in ranked], ["paper"])
        self.assertEqual(ranked[0]["why"], "Similar to your likes")


class RefreshIndexingTests(unittest.TestCase):
    def test_refresh_requests_share_one_worker(self):
        app.index_requested.clear()
        with patch.object(app.threading, "Thread") as worker:
            try:
                app.queue_indexing()
                app.queue_indexing()
                worker.assert_called_once_with(target=app.index_after_refresh, daemon=True)
                worker.return_value.start.assert_called_once()
                self.assertTrue(app.index_requested.is_set())
            finally:
                app.index_lock.release()
                app.index_requested.clear()

    def test_background_indexing_drains_pending_batches(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "state.sqlite3"
            catalog = Path(folder) / "catalog.sqlite3"
            with patch.object(app, "DB", state), patch.object(app, "CATALOG", catalog):
                app.index_requested.set()
                self.assertTrue(app.index_lock.acquire(blocking=False))
                result = {"indexed": 1, "failed": 0, "pending": 0, "errors": []}
                with patch.object(app, "semantic_coverage", side_effect=[{"complete": False}, {"complete": True}]), \
                        patch.object(app, "index_pending", return_value=result) as index:
                    app.index_after_refresh()
                index.assert_called_once_with(state, limit=100, workers=4, catalog_path=catalog)
                self.assertEqual(app.index_status["last_result"], result)
                self.assertFalse(app.index_status["running"])
                self.assertTrue(app.index_lock.acquire(blocking=False))
                app.index_lock.release()

    def test_indexing_stops_after_a_failed_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "state.sqlite3"
            catalog = Path(folder) / "catalog.sqlite3"
            with patch.object(app, "DB", state), patch.object(app, "CATALOG", catalog):
                app.index_requested.set()
                self.assertTrue(app.index_lock.acquire(blocking=False))
                result = {"indexed": 0, "failed": 1, "pending": 1, "errors": ["document failed"]}
                with patch.object(app, "semantic_coverage", return_value={"complete": False}), \
                        patch.object(app, "index_pending", return_value=result) as index:
                    app.index_after_refresh()
                index.assert_called_once()
                self.assertEqual(app.index_status["last_result"], result)
                self.assertFalse(app.index_status["running"])


if __name__ == "__main__":
    unittest.main()
