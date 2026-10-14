import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


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
