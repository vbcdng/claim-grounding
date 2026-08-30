"""LLMClient retry classification: a rate/quota error must never be mistaken for
a permanently-bad request.

Google's free tier returns RESOURCE_EXHAUSTED with code 429 in the body, and
litellm surfaces it as `litellm.BadRequestError` — whose text matches the
non-retryable "bad request" guard. Before 2026-08-02 that guard ran first and
abandoned the call, which cost 4 claims of the old-100 gemma run two full retry
rounds (they stayed judge_error = a false 'unsupported'). Rate detection now runs
FIRST, and its substrings are precise: a bare "rate" also occurs inside Google's
own `generate_content` quota-metric names. Fully offline: _completion is stubbed
and sleeps are neutralised."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import llm_client as lc
from modules.papertrail.llm_client import LLMClient


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)
        self.finish_reason = "stop"


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]


# The real thing, trimmed: a 429 body wrapped in litellm's BadRequestError class.
_QUOTA_AS_BADREQUEST = (
    "litellm.BadRequestError: VertexAIException BadRequestError - "
    '{"error": {"code": 429, "message": "You exceeded your current quota ... '
    "Quota exceeded for metric: "
    "generativelanguage.googleapis.com/generate_content_free_tier_input_token_count, "
    'limit: 16000, model: gemma-4-31b", "status": "RESOURCE_EXHAUSTED"}}'
)


class _NoSleep:
    """Neutralise the pacing waits so the test runs instantly, but record them."""

    def __init__(self):
        self.waits = []

    def __call__(self, seconds):
        self.waits.append(seconds)


class TestRateVsNonRetryable(unittest.TestCase):
    def setUp(self):
        self._real_sleep = lc.time.sleep
        self.sleeper = _NoSleep()
        lc.time.sleep = self.sleeper

    def tearDown(self):
        lc.time.sleep = self._real_sleep

    def _client(self, model="gemini/gemma-4-31b-it", keys=("k1",)):
        c = LLMClient(model=model, api_key=keys[0])
        c._api_keys = list(keys)
        return c

    def test_quota_wrapped_as_badrequest_is_retried_not_abandoned(self):
        c = self._client()
        calls = []

        def fake(**kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise Exception(_QUOTA_AS_BADREQUEST)
            return _Resp("verdict")

        c._completion = fake
        self.assertEqual(c._call_impl("p"), "verdict")
        self.assertEqual(len(calls), 2)
        self.assertTrue(self.sleeper.waits, "a rate error must pace, not return")

    def test_quota_wrapped_as_badrequest_hops_keys_when_a_second_key_exists(self):
        c = self._client(keys=("k1", "k2"))
        seen_keys = []

        def fake(**kwargs):
            seen_keys.append(kwargs.get("api_key"))
            if len(seen_keys) == 1:
                raise Exception(_QUOTA_AS_BADREQUEST)
            return _Resp("verdict")

        c._completion = fake
        self.assertEqual(c._call_impl("p"), "verdict")
        self.assertEqual(len(set(seen_keys)), 2, "second attempt must use the other key")

    def test_real_auth_error_stays_non_retryable(self):
        c = self._client()
        calls = []

        def fake(**kwargs):
            calls.append(1)
            raise Exception("litellm.AuthenticationError: invalid api key")

        c._completion = fake
        self.assertIsNone(c._call_impl("p"))
        self.assertEqual(len(calls), 1, "auth failures must not be retried")

    def test_generate_content_in_a_bad_request_is_not_read_as_rate_limited(self):
        """Precision guard: the substring 'rate' lives inside 'generate_content'."""
        c = self._client()
        calls = []

        def fake(**kwargs):
            calls.append(1)
            raise Exception("litellm.BadRequestError: models/generate_content: "
                            "invalid argument — unsupported field")

        c._completion = fake
        self.assertIsNone(c._call_impl("p"))
        self.assertEqual(len(calls), 1, "a genuine bad request must not be paced")


if __name__ == "__main__":
    unittest.main()
