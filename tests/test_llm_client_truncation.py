"""LLMClient.call() output-cap guards: requests clamp to the model's output
ceiling, and a response cut off at the cap (finish_reason == "length") retries
with a doubled cap instead of returning silently truncated JSON — the 0-edge
bug class (docs/ASSESSMENT_AUDIT.md). Fully offline: _completion is stubbed."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail.llm_client import LLMClient


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


def _client(output_cap=65535):
    c = LLMClient(model="gemini/gemini-2.5-flash-lite", api_key="k")
    c._output_cap = output_cap
    return c


class TestOutputCapClamp(unittest.TestCase):
    def test_known_model_cap_detected(self):
        c = LLMClient(model="gemini/gemini-2.5-flash-lite", api_key="k")
        self.assertEqual(c._output_cap, 65535)

    def test_request_clamped_to_ceiling(self):
        c = _client(output_cap=65535)
        seen = []

        def fake(**kwargs):
            seen.append(kwargs["max_tokens"])
            return _Resp("ok")

        c._completion = fake
        # 128 tokens/claim on a 600-claim doc over-asks the flash-lite ceiling.
        self.assertEqual(c.call("p", max_output_tokens=128 * 600), "ok")
        self.assertEqual(seen, [65535])

    def test_small_request_untouched(self):
        c = _client()
        seen = []

        def fake(**kwargs):
            seen.append(kwargs["max_tokens"])
            return _Resp("ok")

        c._completion = fake
        c.call("p", max_output_tokens=2048)
        self.assertEqual(seen, [2048])


class TestTruncationRetry(unittest.TestCase):
    def test_truncated_response_retried_with_doubled_cap(self):
        c = _client(output_cap=65535)
        seen = []

        def fake(**kwargs):
            seen.append(kwargs["max_tokens"])
            if len(seen) == 1:
                return _Resp('[{"cut": ', finish_reason="length")
            return _Resp('[{"complete": true}]')

        c._completion = fake
        self.assertEqual(c.call("p", max_output_tokens=1024),
                         '[{"complete": true}]')
        self.assertEqual(seen, [1024, 2048])

    def test_truncated_at_ceiling_returns_text_with_warning(self):
        # No headroom left: the truncated text comes back (callers' JSON parse
        # fails loudly) rather than None or an infinite retry.
        c = _client(output_cap=1024)

        def fake(**kwargs):
            return _Resp("cut-off text", finish_reason="length")

        c._completion = fake
        with self.assertLogs("modules.papertrail.llm_client", level="WARNING"):
            out = c.call("p", max_output_tokens=1024)
        self.assertEqual(out, "cut-off text")

    def test_normal_finish_no_retry(self):
        c = _client()
        calls = []

        def fake(**kwargs):
            calls.append(1)
            return _Resp("fine")

        c._completion = fake
        self.assertEqual(c.call("p"), "fine")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
