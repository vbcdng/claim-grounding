"""The Google-direct gemma path (2026-08-02, MODEL_HOSTING_LANDSCAPE §6):

1. litellm drops `extra_body` for the gemini/ provider, so per-model payloads
   must reach litellm as TOP-LEVEL kwargs there — while deepseek et al. keep
   the extra_body route (the arbiter thinking-off fix must not regress).
2. Multiple config/google_api_key*.txt files = multiple free-tier quotas:
   calls round-robin across them and a rate-limited call switches keys.
3. Free-tier-only models (gemini/gemma-4*) treat 429s as pacing: rate errors
   retry patiently without consuming the 3 regular attempts.

Fully offline: _completion is stubbed, key files live in a temp dir,
time.sleep is patched out.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail.llm_client import LLMClient

MINIMAL = {"thinkingConfig": {"thinkingLevel": "MINIMAL"}}


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content, finish_reason):
        self.message = _Msg(content)
        self.finish_reason = finish_reason


class _Resp:
    def __init__(self, content, finish_reason="stop"):
        self.choices = [_Choice(content, finish_reason)]


def _key_dir(*keys):
    """Temp dir with google_api_key.txt, google_api_key2.txt, ... -> file paths."""
    d = tempfile.mkdtemp(prefix="pt-test-keys-")
    paths = []
    for i, key in enumerate(keys):
        name = "google_api_key.txt" if i == 0 else f"google_api_key{i + 1}.txt"
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(key + "\n")
        paths.append(p)
    return paths


class TestGeminiTopLevelKwargs(unittest.TestCase):
    def test_gemma_payload_is_top_level_not_extra_body(self):
        c = LLMClient(model="gemini/gemma-4-31b-it", api_key="k")
        seen = {}

        def fake(**kwargs):
            seen.update(kwargs)
            return _Resp("ok")

        c._completion = fake
        self.assertEqual(c.call("p"), "ok")
        self.assertEqual(seen.get("thinkingConfig"), MINIMAL["thinkingConfig"])
        self.assertNotIn("extra_body", seen)

    def test_deepseek_still_uses_extra_body(self):
        c = LLMClient(model="deepseek/deepseek-v4-flash", api_key="k")
        seen = {}

        def fake(**kwargs):
            seen.update(kwargs)
            return _Resp("ok")

        c._completion = fake
        self.assertEqual(c.call("p"), "ok")
        self.assertEqual(seen.get("extra_body"), {"thinking": {"type": "disabled"}})
        self.assertNotIn("thinking", seen)

    def test_env_override_for_gemini_model_also_top_level(self):
        with mock.patch.dict(os.environ, {"PAPERTRAIL_LLM_EXTRA_BODY":
                                          '{"gemma-4-31b": {"thinkingConfig": {"thinkingLevel": "HIGH"}}}'}):
            c = LLMClient(model="gemini/gemma-4-31b-it", api_key="k")
            seen = {}

            def fake(**kwargs):
                seen.update(kwargs)
                return _Resp("ok")

            c._completion = fake
            c.call("p")
        self.assertEqual(seen.get("thinkingConfig"), {"thinkingLevel": "HIGH"})
        self.assertNotIn("extra_body", seen)


class TestMultiKey(unittest.TestCase):
    def _client(self, *keys, model="gemini/gemma-4-31b-it"):
        paths = _key_dir(*keys)
        with mock.patch("modules.papertrail.llm_client._gemini_key_files",
                        return_value=paths):
            return LLMClient(model=model)   # no explicit key -> file fallback

    def test_all_key_files_loaded_primary_first(self):
        c = self._client("KEY_A", "KEY_B")
        self.assertEqual(c._api_keys, ["KEY_A", "KEY_B"])
        self.assertEqual(c.api_key, "KEY_A")

    def test_duplicate_keys_deduped(self):
        c = self._client("KEY_A", "KEY_A")
        self.assertEqual(c._api_keys, ["KEY_A"])

    def test_explicit_key_disables_rotation(self):
        paths = _key_dir("KEY_A", "KEY_B")
        with mock.patch("modules.papertrail.llm_client._gemini_key_files",
                        return_value=paths):
            c = LLMClient(model="gemini/gemma-4-31b-it", api_key="EXPLICIT")
        self.assertEqual(c._api_keys, ["EXPLICIT"])

    def test_round_robin_across_calls(self):
        c = self._client("KEY_A", "KEY_B")
        used = []

        def fake(**kwargs):
            used.append(kwargs["api_key"])
            return _Resp("ok")

        c._completion = fake
        for _ in range(4):
            c.call("p")
        self.assertEqual(sorted(set(used)), ["KEY_A", "KEY_B"])
        self.assertEqual(used[0], used[2])   # strict alternation
        self.assertEqual(used[1], used[3])
        self.assertNotEqual(used[0], used[1])

    @mock.patch("time.sleep")
    def test_rate_error_switches_key_without_burning_attempts(self, _sleep):
        c = self._client("KEY_A", "KEY_B")
        used = []

        def fake(**kwargs):
            used.append(kwargs["api_key"])
            if len(used) == 1:
                raise RuntimeError("429 rate limit exceeded")
            return _Resp("ok")

        c._completion = fake
        self.assertEqual(c.call("p"), "ok")
        self.assertEqual(len(used), 2)
        self.assertNotEqual(used[0], used[1])   # second try = the other account


class TestFreeTierPacing(unittest.TestCase):
    @mock.patch("time.sleep")
    def test_gemma_survives_many_rate_errors(self, _sleep):
        # 6 consecutive 429s would kill a normal model (3 attempts); the
        # free-tier gemma path waits them out and still returns the answer.
        c = LLMClient(model="gemini/gemma-4-31b-it", api_key="k")
        calls = {"n": 0}

        def fake(**kwargs):
            calls["n"] += 1
            if calls["n"] <= 6:
                raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")
            return _Resp("ok")

        c._completion = fake
        self.assertEqual(c.call("p"), "ok")

    @mock.patch("time.sleep")
    def test_non_free_tier_model_keeps_three_attempts(self, _sleep):
        c = LLMClient(model="gemini/gemini-2.5-flash-lite", api_key="k")
        calls = {"n": 0}

        def fake(**kwargs):
            calls["n"] += 1
            raise RuntimeError("429 rate limit exceeded")

        c._completion = fake
        self.assertIsNone(c.call("p"))
        self.assertEqual(calls["n"], 3)

    @mock.patch("time.sleep")
    def test_gemma_non_rate_errors_still_give_up_fast(self, _sleep):
        c = LLMClient(model="gemini/gemma-4-31b-it", api_key="k")
        calls = {"n": 0}

        def fake(**kwargs):
            calls["n"] += 1
            raise RuntimeError("connection reset by peer")

        c._completion = fake
        self.assertIsNone(c.call("p"))
        self.assertEqual(calls["n"], 3)


if __name__ == "__main__":
    unittest.main()
