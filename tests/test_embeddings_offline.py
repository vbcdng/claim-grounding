"""Blocked first-run model download (task #69, known-issues item 3): when the
sentence-comparison model is not on disk and the one-time download fails, the
error must be a plain-language RuntimeError, not a raw hub trace — and it must
name the ..._OFFLINE settings when they are the cause. Offline: the model
class is mocked; nothing touches the network.

Run:  venv/bin/python3 -m unittest tests.test_embeddings_offline -v
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import embeddings


class TestBlockedDownload(unittest.TestCase):

    def setUp(self):
        self._saved = embeddings._model
        embeddings._model = None

    def tearDown(self):
        embeddings._model = self._saved

    def _fail_both(self):
        # First call (local_files_only) and second call (download) both fail.
        return mock.patch("sentence_transformers.SentenceTransformer",
                          side_effect=OSError("connection refused"))

    def test_plain_runtime_error_instead_of_raw_trace(self):
        with self._fail_both():
            with self.assertRaises(RuntimeError) as ctx:
                embeddings.get_model()
        msg = str(ctx.exception)
        self.assertIn("440 MB", msg)
        self.assertIn("internet", msg)
        self.assertIn("connection refused", msg)   # the technical cause stays visible

    def test_offline_env_vars_are_named_when_set(self):
        with self._fail_both(), \
             mock.patch.dict(os.environ, {"HF_HUB_OFFLINE": "1"}):
            with self.assertRaises(RuntimeError) as ctx:
                embeddings.get_model()
        self.assertIn("HF_HUB_OFFLINE", str(ctx.exception))

    def test_local_copy_needs_no_network(self):
        fake = mock.Mock(device="cpu")
        with mock.patch("sentence_transformers.SentenceTransformer",
                        return_value=fake) as st:
            self.assertIs(embeddings.get_model(), fake)
        # the first (local_files_only) attempt succeeded; no download call
        self.assertEqual(st.call_count, 1)
        self.assertTrue(st.call_args.kwargs.get("local_files_only"))


if __name__ == "__main__":
    unittest.main()
