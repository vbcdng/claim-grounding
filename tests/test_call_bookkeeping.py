"""Per-call bookkeeping (task #20 phase 1, 2026-08-01): every logical
LLMClient.call carries a `purpose` tag, rolls up into
usage_summary()[model]["by_purpose"], and appends one JSONL line to the
per-run call log installed via set_call_log(). All offline — the completion
is stubbed. Nothing here touches the verdict path.

Run:  venv/bin/python3 -m unittest tests.test_call_bookkeeping
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import llm_client
from modules.papertrail.llm_client import LLMClient, parallel_map


def _fake_resp(text="ok", pt=100, ct=7, finish="stop"):
    choice = MagicMock()
    choice.message.content = text
    choice.finish_reason = finish
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage.prompt_tokens = pt
    resp.usage.completion_tokens = ct
    return resp


class _Base(unittest.TestCase):
    def setUp(self):
        with llm_client._USAGE_LOCK:
            llm_client._USAGE.clear()
        llm_client.set_call_log(None)
        self._tmp = tempfile.TemporaryDirectory()
        self.log_path = os.path.join(self._tmp.name, "llm_calls.jsonl")

    def tearDown(self):
        llm_client.set_call_log(None)
        self._tmp.cleanup()
        os.environ.pop("PAPERTRAIL_LOG_PROMPTS", None)

    def _client(self):
        return LLMClient(model="gemini/gemini-2.5-flash-lite")

    def _lines(self):
        with open(self.log_path, "r", encoding="utf-8") as f:
            return [json.loads(ln) for ln in f if ln.strip()]


class TestPurposeRollup(_Base):

    def test_purposes_accumulate_separately(self):
        c = self._client()
        with patch.object(c, "_completion", side_effect=[_fake_resp(pt=100, ct=7),
                                                         _fake_resp(pt=50, ct=3),
                                                         _fake_resp(pt=10, ct=1)]), \
             patch("litellm.completion_cost", return_value=0.001):
            c.call("p1", purpose="retrieval_judge")
            c.call("p2", purpose="retrieval_judge")
            c.call("p3", purpose="citation_scope")
        bp = llm_client.usage_summary()["gemini/gemini-2.5-flash-lite"]["by_purpose"]
        self.assertEqual(bp["retrieval_judge"]["calls"], 2)
        self.assertEqual(bp["retrieval_judge"]["prompt_tokens"], 150)
        self.assertEqual(bp["retrieval_judge"]["completion_tokens"], 10)
        self.assertAlmostEqual(bp["retrieval_judge"]["cost_usd"], 0.002)
        self.assertEqual(bp["citation_scope"]["calls"], 1)
        self.assertEqual(bp["citation_scope"]["prompt_tokens"], 10)

    def test_untagged_falls_through(self):
        c = self._client()
        with patch.object(c, "_completion", return_value=_fake_resp()), \
             patch("litellm.completion_cost", return_value=0.0):
            c.call("p")
        bp = llm_client.usage_summary()["gemini/gemini-2.5-flash-lite"]["by_purpose"]
        self.assertEqual(bp["untagged"]["calls"], 1)

    def test_top_level_totals_unchanged_by_purpose_rollup(self):
        # back-compat: existing per-model counters keep API-attempt semantics
        c = self._client()
        with patch.object(c, "_completion", return_value=_fake_resp(pt=42, ct=5)), \
             patch("litellm.completion_cost", return_value=0.0):
            c.call("p", purpose="arbiter")
        u = llm_client.usage_summary()["gemini/gemini-2.5-flash-lite"]
        self.assertEqual((u["calls"], u["prompt_tokens"], u["completion_tokens"]),
                         (1, 42, 5))

    def test_retry_folds_into_one_logical_call(self):
        # a truncation retry = 2 API attempts but ONE by_purpose call, tokens summed
        c = self._client()
        with patch.object(c, "_completion",
                          side_effect=[_fake_resp(pt=100, ct=50, finish="length"),
                                       _fake_resp(pt=100, ct=80)]), \
             patch("litellm.completion_cost", return_value=0.001):
            out = c.call("p", purpose="fulltext_extract", max_output_tokens=64)
        self.assertEqual(out, "ok")
        u = llm_client.usage_summary()["gemini/gemini-2.5-flash-lite"]
        self.assertEqual(u["calls"], 2)                       # attempts
        bp = u["by_purpose"]["fulltext_extract"]
        self.assertEqual(bp["calls"], 1)                      # logical
        self.assertEqual(bp["prompt_tokens"], 200)
        self.assertEqual(bp["completion_tokens"], 130)

    def test_no_token_reporting_still_counts_logical_calls(self):
        # the claude-code backend never reaches _record_usage — by_purpose must
        # still count its calls (zero tokens), so $0 dev runs get the table too
        c = self._client()
        with patch.object(c, "_call_impl", return_value="ok"):
            c.call("p", purpose="own_split")
        u = llm_client.usage_summary()["gemini/gemini-2.5-flash-lite"]
        self.assertEqual(u["calls"], 0)
        self.assertEqual(u["by_purpose"]["own_split"],
                         {"calls": 1, "prompt_tokens": 0, "completion_tokens": 0,
                          "cost_usd": 0.0, "cached_prompt_tokens": 0})

    def test_call_json_threads_purpose(self):
        c = self._client()
        with patch.object(c, "_completion", return_value=_fake_resp(text='{"a": 1}')), \
             patch("litellm.completion_cost", return_value=0.0):
            self.assertEqual(c.call_json("p", purpose="crux"), {"a": 1})
        bp = llm_client.usage_summary()["gemini/gemini-2.5-flash-lite"]["by_purpose"]
        self.assertEqual(bp["crux"]["calls"], 1)

    def test_summary_snapshot_is_isolated(self):
        c = self._client()
        with patch.object(c, "_completion", return_value=_fake_resp()), \
             patch("litellm.completion_cost", return_value=0.0):
            c.call("p", purpose="dedup")
        snap = llm_client.usage_summary()
        snap["gemini/gemini-2.5-flash-lite"]["by_purpose"]["dedup"]["calls"] = 999
        fresh = llm_client.usage_summary()
        self.assertEqual(fresh["gemini/gemini-2.5-flash-lite"]["by_purpose"]["dedup"]["calls"], 1)


class TestCallLog(_Base):

    def test_jsonl_line_well_formed(self):
        llm_client.set_call_log(self.log_path)
        c = self._client()
        with patch.object(c, "_completion", return_value=_fake_resp(text="yes", pt=100, ct=7)), \
             patch("litellm.completion_cost", return_value=0.002):
            c.call("my prompt", purpose="arbiter", claim_id="t5")
        (rec,) = self._lines()
        self.assertEqual(rec["purpose"], "arbiter")
        self.assertEqual(rec["claim_id"], "t5")
        self.assertEqual(rec["model"], "gemini/gemini-2.5-flash-lite")
        self.assertEqual(rec["prompt_tokens"], 100)
        self.assertEqual(rec["completion_tokens"], 7)
        self.assertAlmostEqual(rec["cost_usd"], 0.002)
        self.assertEqual(rec["response_text"], "yes")
        self.assertEqual(rec["prompt_chars"], len("my prompt"))
        self.assertEqual(len(rec["prompt_sha256"]), 64)
        self.assertEqual(rec["api_attempts"], 1)
        self.assertFalse(rec["failed"])
        self.assertGreaterEqual(rec["latency_s"], 0)
        self.assertIn("ts", rec)
        self.assertEqual(rec["seq"], 1)
        self.assertNotIn("prompt_text", rec)   # hash-only by default

    def test_prompt_text_only_under_env_var(self):
        llm_client.set_call_log(self.log_path)
        os.environ["PAPERTRAIL_LOG_PROMPTS"] = "1"
        c = self._client()
        with patch.object(c, "_completion", return_value=_fake_resp()), \
             patch("litellm.completion_cost", return_value=0.0):
            c.call("secret prompt", purpose="arbiter")
        (rec,) = self._lines()
        self.assertEqual(rec["prompt_text"], "secret prompt")

    def test_claim_id_omitted_when_unknown(self):
        llm_client.set_call_log(self.log_path)
        c = self._client()
        with patch.object(c, "_completion", return_value=_fake_resp()), \
             patch("litellm.completion_cost", return_value=0.0):
            c.call("p", purpose="argmap_edges")
        (rec,) = self._lines()
        self.assertNotIn("claim_id", rec)

    def test_failed_call_still_logged(self):
        llm_client.set_call_log(self.log_path)
        c = self._client()
        with patch.object(c, "_call_impl", return_value=None):
            self.assertIsNone(c.call("p", purpose="second_opinion"))
        (rec,) = self._lines()
        self.assertTrue(rec["failed"])
        self.assertIsNone(rec["response_text"])

    def test_logging_off_by_default_and_after_disable(self):
        c = self._client()
        with patch.object(c, "_completion", return_value=_fake_resp()), \
             patch("litellm.completion_cost", return_value=0.0):
            c.call("p")
        self.assertFalse(os.path.exists(self.log_path))
        llm_client.set_call_log(self.log_path)
        llm_client.set_call_log(None)
        with patch.object(c, "_completion", return_value=_fake_resp()), \
             patch("litellm.completion_cost", return_value=0.0):
            c.call("p")
        self.assertFalse(os.path.exists(self.log_path))

    def test_log_write_failure_never_raises(self):
        llm_client.set_call_log(os.path.join(self._tmp.name, "no-such-dir", "x.jsonl"))
        c = self._client()
        with patch.object(c, "_completion", return_value=_fake_resp()), \
             patch("litellm.completion_cost", return_value=0.0):
            self.assertEqual(c.call("p"), "ok")   # the call itself must survive

    def test_thread_safety_smoke(self):
        llm_client.set_call_log(self.log_path)
        c = self._client()
        with patch.object(c, "_completion", side_effect=lambda **kw: _fake_resp()), \
             patch("litellm.completion_cost", return_value=0.0):
            parallel_map(lambda i: c.call(f"p{i}", purpose="retrieval_judge"),
                         range(24), workers=8)
        lines = self._lines()
        self.assertEqual(len(lines), 24)
        self.assertEqual(sorted(r["seq"] for r in lines), list(range(1, 25)))
        bp = llm_client.usage_summary()["gemini/gemini-2.5-flash-lite"]["by_purpose"]
        self.assertEqual(bp["retrieval_judge"]["calls"], 24)


if __name__ == "__main__":
    unittest.main()
