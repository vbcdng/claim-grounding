"""Actual-usage ledger (owner ask, 2026-07-11): every real API call through
LLMClient.call records reported tokens + litellm-computed cost; verify_my_text
writes usage_summary() into metadata.llm_usage. Offline — the completion is
stubbed.

Run:  venv/bin/python3 -m unittest tests.test_llm_usage -v
"""
import os
import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
