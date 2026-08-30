"""Actual-usage ledger (owner ask, 2026-07-11): every real API call through
LLMClient.call records reported tokens + litellm-computed cost; verify_my_text
writes usage_summary() into metadata.llm_usage. Offline — the completion is
stubbed.

Run:  venv/bin/python3 -m unittest tests.test_llm_usage -v
"""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import llm_client
from modules.papertrail.llm_client import LLMClient


def _fake_resp(text="ok", pt=100, ct=7):
    choice = MagicMock()
    choice.message.content = text
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage.prompt_tokens = pt
    resp.usage.completion_tokens = ct
    return resp


class TestUsageLedger(unittest.TestCase):

    def setUp(self):
        with llm_client._USAGE_LOCK:
            llm_client._USAGE.clear()

    def test_calls_accumulate_tokens_and_cost(self):
        c = LLMClient(model="gemini/gemini-2.5-flash-lite")
        with patch.object(c, "_completion", side_effect=[_fake_resp(pt=100, ct=7),
                                                         _fake_resp(pt=50, ct=3)]), \
             patch("litellm.completion_cost", return_value=0.001):
            self.assertEqual(c.call("p1"), "ok")
            self.assertEqual(c.call("p2"), "ok")
        u = llm_client.usage_summary()["gemini/gemini-2.5-flash-lite"]
        self.assertEqual(u["calls"], 2)
        self.assertEqual(u["prompt_tokens"], 150)
        self.assertEqual(u["completion_tokens"], 10)
        self.assertAlmostEqual(u["cost_usd"], 0.002)

    def test_unknown_pricing_still_counts_tokens(self):
        c = LLMClient(model="gemini/gemini-2.5-flash-lite")
        with patch.object(c, "_completion", return_value=_fake_resp(pt=42, ct=5)), \
             patch("litellm.completion_cost", side_effect=Exception("no pricing")):
            c.call("p")
        u = llm_client.usage_summary()["gemini/gemini-2.5-flash-lite"]
        self.assertEqual((u["calls"], u["prompt_tokens"], u["cost_usd"]), (1, 42, 0.0))

    def test_empty_ledger_summary(self):
        self.assertEqual(llm_client.usage_summary(), {})


class TestCachedPromptTokens(unittest.TestCase):
    """Cache-hit split (2026-08-01): a cached input token is ~10x cheaper, so
    without this the cost columns aren't comparable across providers/runs.
    Exercises llm_client._record_usage directly against synthetic `resp`
    objects shaped like each provider's real usage payload."""

    def setUp(self):
        with llm_client._USAGE_LOCK:
            llm_client._USAGE.clear()

    def test_deepseek_shaped_prompt_cache_hit_tokens(self):
        resp = SimpleNamespace(usage=SimpleNamespace(
            prompt_tokens=100, completion_tokens=5, prompt_cache_hit_tokens=80))
        llm_client._record_usage("deepseek/deepseek-v4-flash", resp)
        u = llm_client.usage_summary()["deepseek/deepseek-v4-flash"]
        self.assertEqual(u["cached_prompt_tokens"], 80)

    def test_openai_shaped_prompt_tokens_details(self):
        resp = SimpleNamespace(usage=SimpleNamespace(
            prompt_tokens=100, completion_tokens=5,
            prompt_tokens_details=SimpleNamespace(cached_tokens=60)))
        llm_client._record_usage("openai/gpt-4o-mini", resp)
        u = llm_client.usage_summary()["openai/gpt-4o-mini"]
        self.assertEqual(u["cached_prompt_tokens"], 60)

    def test_anthropic_shaped_cache_read_input_tokens(self):
        resp = SimpleNamespace(usage=SimpleNamespace(
            prompt_tokens=100, completion_tokens=5,
            cache_read_input_tokens=40, cache_creation_input_tokens=10))
        llm_client._record_usage("anthropic/claude-sonnet-4", resp)
        u = llm_client.usage_summary()["anthropic/claude-sonnet-4"]
        # a cache WRITE (cache_creation_input_tokens) is a cost, not a hit —
        # only the read count is recorded as cached_prompt_tokens.
        self.assertEqual(u["cached_prompt_tokens"], 40)

    def test_no_cache_fields_defaults_to_zero(self):
        resp = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=100, completion_tokens=5))
        llm_client._record_usage("gemini/gemini-2.5-flash-lite", resp)
        u = llm_client.usage_summary()["gemini/gemini-2.5-flash-lite"]
        self.assertEqual(u["cached_prompt_tokens"], 0)

    def test_usage_as_plain_dict(self):
        resp = SimpleNamespace(usage={"prompt_tokens": 100, "completion_tokens": 5,
                                       "prompt_cache_hit_tokens": 30})
        llm_client._record_usage("deepseek/deepseek-v4-flash", resp)
        u = llm_client.usage_summary()["deepseek/deepseek-v4-flash"]
        self.assertEqual(u["cached_prompt_tokens"], 30)
        # the totals must read from a dict too, or a cache hit could be
        # recorded against a prompt-token count of zero
        self.assertEqual(u["prompt_tokens"], 100)
        self.assertEqual(u["completion_tokens"], 5)

    def test_usage_as_dict_with_nested_openai_details(self):
        resp = SimpleNamespace(usage={"prompt_tokens": 100, "completion_tokens": 5,
                                       "prompt_tokens_details": {"cached_tokens": 15}})
        llm_client._record_usage("openai/gpt-4o-mini", resp)
        u = llm_client.usage_summary()["openai/gpt-4o-mini"]
        self.assertEqual(u["cached_prompt_tokens"], 15)

    def test_accumulates_across_multiple_calls(self):
        for hit in (80, 20):
            resp = SimpleNamespace(usage=SimpleNamespace(
                prompt_tokens=100, completion_tokens=5, prompt_cache_hit_tokens=hit))
            llm_client._record_usage("deepseek/deepseek-v4-flash", resp)
        u = llm_client.usage_summary()["deepseek/deepseek-v4-flash"]
        self.assertEqual(u["cached_prompt_tokens"], 100)
        self.assertEqual(u["calls"], 2)


if __name__ == "__main__":
    unittest.main()
