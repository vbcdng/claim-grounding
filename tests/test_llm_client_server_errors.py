"""LLMClient: a Google-side server error on a free-tier-paced model is waited out
like a rate limit instead of burning the three fast attempts.

Why: the two day75b gate runs of 2026-09-08/09 lost 341 and 52 calls to
`litellm.InternalServerError` / `ServiceUnavailableError` waves (retried 1 s and
2 s later, then abandoned), so every text had unchecked claims and the gate was
unscorable under the task-#37 rule. Fully offline: _completion is stubbed and
sleeps are recorded, not performed."""

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


_SERVER_500 = ("litellm.InternalServerError: GeminiException InternalServerError - "
               '{"error": {"code": 500, "message": "An internal error has occurred. '
               'Please retry or report in https://developers.generativeai.google/guide/troubleshooting", '
               '"status": "INTERNAL"}}')
_SERVER_503 = ("litellm.ServiceUnavailableError: GeminiException - "
               '{"error": {"code": 503, "message": "The service is currently unavailable.", '
               '"status": "UNAVAILABLE"}}')


class _NoSleep:
    def __init__(self):
        self.waits = []

    def __call__(self, seconds):
        self.waits.append(seconds)


class TestServerErrorPacing(unittest.TestCase):
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

    def _flaky(self, failures, message):
        calls = []

        def fake(**kwargs):
            calls.append(kwargs.get("api_key"))
            if len(calls) <= failures:
                raise Exception(message)
            return _Resp("verdict")
        return fake, calls

    def test_a_wave_of_five_500s_is_waited_out_on_the_free_seat(self):
        c = self._client()
        fake, calls = self._flaky(5, _SERVER_500)
        c._completion = fake
        self.assertEqual(c._call_impl("p"), "verdict")
        self.assertEqual(len(calls), 6, "five failures then one success")
        self.assertEqual(self.sleeper.waits, [60] * 5, "each server error waits 60 s, no fast 1 s/2 s retries")

    def test_503_counts_as_a_server_error_and_alternates_keys(self):
        c = self._client(keys=("k1", "k2"))
        fake, calls = self._flaky(3, _SERVER_503)
        c._completion = fake
        self.assertEqual(c._call_impl("p"), "verdict")
        self.assertEqual(len(set(calls)), 2, "both keys are used while waiting")

    def test_gives_up_after_the_cap_and_three_attempts(self):
        c = self._client()
        fake, calls = self._flaky(10_000, _SERVER_500)
        c._completion = fake
        self.assertIsNone(c._call_impl("p"))
        self.assertEqual(len(calls), lc._MAX_SERVER_WAITS + 3,
                         "the cap of patient waits, then the three regular attempts")

    def test_non_paced_model_keeps_the_three_fast_attempts(self):
        c = self._client(model="gemini/gemini-2.5-flash-lite")
        fake, calls = self._flaky(10_000, _SERVER_500)
        c._completion = fake
        self.assertIsNone(c._call_impl("p"))
        self.assertEqual(len(calls), 3)
        self.assertNotIn(60, self.sleeper.waits)

    def test_a_real_bad_request_is_still_not_retried(self):
        c = self._client()
        fake, calls = self._flaky(10_000, "litellm.BadRequestError: invalid argument: unknown field")
        c._completion = fake
        self.assertIsNone(c._call_impl("p"))
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
