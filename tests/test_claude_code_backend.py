"""Stream E: the $0 claude-code backend. Everything here is offline — the
`claude` CLI is mocked (shutil.which + subprocess.run); no API, no network.

The contract under test:
- LLMClient(model="claude-code/...") transparently constructs ClaudeCodeClient
  (the __new__ dispatch), so every existing call site gets the free backend by
  model string alone; other models still get the plain litellm client.
- ClaudeCodeClient.call shells out to `claude -p`, retries transient failures,
  and returns None (the pipeline's "treat as unsupported" safe direction) after
  the retry budget.
- verify_my_text.apply_backend canonicalizes the two spellings and installs the
  Haiku-tuned combined-judgment rubric via matcher.PROMPT_OVERRIDES without
  touching the production prompt file.
"""

import argparse
import os
import unittest
from unittest import mock

from modules.papertrail import matcher
from modules.papertrail.llm_client import LLMClient
from modules.papertrail.claude_code_backend import ClaudeCodeClient, canonical_model


def _cc(model="claude-code/haiku"):
    """A ClaudeCodeClient with the CLI 'installed' (mocked)."""
    with mock.patch("shutil.which", return_value="/usr/bin/claude"):
        return LLMClient(model=model)


class CanonicalModel(unittest.TestCase):
    def test_spellings(self):
        for given, want in [(None, "claude-code/haiku"),
                            ("claude-code", "claude-code/haiku"),
                            ("claude-code/", "claude-code/haiku"),
                            ("claude-code/sonnet", "claude-code/sonnet"),
                            ("haiku", "claude-code/haiku"),
                            ("sonnet", "claude-code/sonnet")]:
            self.assertEqual(canonical_model(given), want)


class NewDispatch(unittest.TestCase):
    def test_claude_code_model_builds_the_subclass(self):
        c = _cc("claude-code/haiku")
        self.assertIsInstance(c, ClaudeCodeClient)
        self.assertEqual(c.model, "claude-code/haiku")
        self.assertEqual(c.provider, "claude-code")

    def test_bare_claude_code_defaults_to_haiku(self):
        self.assertEqual(_cc("claude-code").cli_model, "haiku")

    def test_normalize_model_never_prefixes_gemini(self):
        self.assertEqual(LLMClient._normalize_model("claude-code"), "claude-code/haiku")

    def test_provider_models_still_get_the_plain_client(self):
        c = LLMClient(model="gemini/gemini-2.5-flash-lite")
        self.assertNotIsInstance(c, ClaudeCodeClient)
        self.assertEqual(c.provider, "gemini")

    def test_missing_cli_raises_a_clear_error(self):
        with mock.patch("shutil.which", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                LLMClient(model="claude-code/haiku")
            self.assertIn("claude", str(ctx.exception))


class CallBehavior(unittest.TestCase):
    def test_call_returns_stdout_and_call_json_parses_fences(self):
        c = _cc()
        proc = mock.Mock(returncode=0,
                         stdout='```json\n{"supported": true, "reason": "ok"}\n```',
                         stderr="")
        with mock.patch("subprocess.run", return_value=proc) as run:
            self.assertIn("supported", c.call("PROMPT"))
            obj = c.call_json("PROMPT")
        self.assertEqual(obj, {"supported": True, "reason": "ok"})
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[:2], ["/usr/bin/claude", "-p"])
        self.assertIn("haiku", cmd)
        self.assertEqual(run.call_args.kwargs["input"], "PROMPT")

    def test_transient_failure_is_retried_then_succeeds(self):
        c = _cc()
        bad = mock.Mock(returncode=1, stdout="", stderr="overloaded")
        good = mock.Mock(returncode=0, stdout="fine", stderr="")
        with mock.patch("subprocess.run", side_effect=[bad, good]), \
             mock.patch("time.sleep"):
            self.assertEqual(c.call("P"), "fine")

    def test_exhausted_retries_return_none(self):
        c = _cc()
        bad = mock.Mock(returncode=1, stdout="", stderr="boom")
        with mock.patch("subprocess.run", return_value=bad), \
             mock.patch("time.sleep") as slept:
            self.assertIsNone(c.call("P"))
        self.assertTrue(slept.called)


class ThrottleBackoff(unittest.TestCase):
    """P2.1: rc!=0 with empty output (the subscription rate/concurrency ceiling)
    gets its own longer, more-patient retry budget so a burst isn't mislabeled
    unsupported by exhausting the tiny generic budget."""

    def test_empty_output_rc1_is_treated_as_throttle_then_succeeds(self):
        from modules.papertrail import claude_code_backend as ccb
        c = _cc()
        throttle = mock.Mock(returncode=1, stdout="", stderr="")   # the ceiling signature
        good = mock.Mock(returncode=0, stdout="fine", stderr="")
        with mock.patch("subprocess.run", side_effect=[throttle, throttle, good]), \
             mock.patch("time.sleep") as slept, \
             mock.patch.object(ccb.random, "uniform", return_value=0.0):
            self.assertEqual(c.call("P"), "fine")
        # both throttle waits used the LONG base, not the generic 2^0=1s
        self.assertEqual([a.args[0] for a in slept.call_args_list],
                         [ccb._THROTTLE_BASE_S, ccb._THROTTLE_BASE_S * 2])

    def test_throttle_budget_outlasts_the_generic_budget(self):
        from modules.papertrail import claude_code_backend as ccb
        c = _cc()
        throttle = mock.Mock(returncode=1, stdout="", stderr="")
        with mock.patch("subprocess.run", return_value=throttle) as run, \
             mock.patch("time.sleep"), \
             mock.patch.object(ccb.random, "uniform", return_value=0.0):
            self.assertIsNone(c.call("P"))
        # ran the full throttle budget, well past the 3 generic attempts
        self.assertEqual(run.call_count, ccb._THROTTLE_MAX_RETRIES)

    def test_rate_limit_message_on_stdout_is_throttle_not_generic(self):
        from modules.papertrail import claude_code_backend as ccb
        c = _cc()
        limited = mock.Mock(returncode=1, stdout="Error: 429 too many requests", stderr="")
        good = mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch("subprocess.run", side_effect=[limited, good]), \
             mock.patch("time.sleep") as slept, \
             mock.patch.object(ccb.random, "uniform", return_value=0.0):
            self.assertEqual(c.call("P"), "ok")
        self.assertEqual(slept.call_args_list[0].args[0], ccb._THROTTLE_BASE_S)

    def test_auth_error_still_fails_fast_without_throttle_retries(self):
        c = _cc()
        auth = mock.Mock(returncode=1, stdout="", stderr="Please log in to continue")
        with mock.patch("subprocess.run", return_value=auth) as run, \
             mock.patch("time.sleep"):
            self.assertIsNone(c.call("P"))
        self.assertEqual(run.call_count, 1)      # non-retryable, no backoff loop


class ApplyBackend(unittest.TestCase):
    def setUp(self):
        self._saved = dict(matcher.PROMPT_OVERRIDES)
        matcher.PROMPT_OVERRIDES.clear()

    def tearDown(self):
        matcher.PROMPT_OVERRIDES.clear()
        matcher.PROMPT_OVERRIDES.update(self._saved)

    def _args(self, model=None, backend="api", concurrency=None):
        return argparse.Namespace(model=model, backend=backend, concurrency=concurrency)

    def test_api_backend_is_untouched(self):
        import verify_my_text as v
        a = self._args(model="gemini/gemini-2.5-flash-lite")
        self.assertFalse(v.apply_backend(a))
        self.assertEqual(a.model, "gemini/gemini-2.5-flash-lite")
        self.assertEqual(matcher.PROMPT_OVERRIDES, {})

    def test_backend_flag_installs_rubric_and_canonicalizes(self):
        import verify_my_text as v
        a = self._args(backend="claude-code")
        self.assertTrue(v.apply_backend(a))
        self.assertEqual(a.model, "claude-code/haiku")
        path = matcher.PROMPT_OVERRIDES["pt_combined_judgment_prompt.txt"]
        self.assertTrue(path.endswith("pt_combined_judgment_haiku_v1.txt"))
        self.assertTrue(os.path.exists(path))

    def test_model_prefix_alone_selects_the_backend(self):
        import verify_my_text as v
        a = self._args(model="claude-code/sonnet")
        self.assertTrue(v.apply_backend(a))
        self.assertEqual(a.backend, "claude-code")
        self.assertEqual(a.model, "claude-code/sonnet")

    def test_conflicting_provider_model_falls_back_to_haiku(self):
        import verify_my_text as v
        a = self._args(model="openai/gpt-4o-mini", backend="claude-code")
        self.assertTrue(v.apply_backend(a))
        self.assertEqual(a.model, "claude-code/haiku")

    def test_bare_second_opinion_follows_the_zero_dollar_backend(self):
        # `--backend claude-code --second-opinion` (bare flag = the paid Gemini
        # default) must not silently spend under the "$0 API spend" banner.
        import verify_my_text as v
        from modules.papertrail import second_opinion
        a = self._args(backend="claude-code")
        a.second_opinion = second_opinion.DEFAULT_MODEL
        v.apply_backend(a)
        self.assertEqual(a.second_opinion, "claude-code/sonnet")

    def test_explicit_second_opinion_model_is_kept(self):
        import verify_my_text as v
        a = self._args(backend="claude-code")
        a.second_opinion = "deepseek/deepseek-chat"   # user's explicit choice
        v.apply_backend(a)
        self.assertEqual(a.second_opinion, "deepseek/deepseek-chat")

    def test_api_backend_keeps_the_second_opinion_default(self):
        import verify_my_text as v
        from modules.papertrail import second_opinion
        a = self._args(model="gemini/gemini-2.5-flash-lite")
        a.second_opinion = second_opinion.DEFAULT_MODEL
        v.apply_backend(a)
        self.assertEqual(a.second_opinion, second_opinion.DEFAULT_MODEL)

    def test_error_injection_is_deterministic_and_safe(self):
        # queue-#2 groundwork: the injector must produce a guaranteed-false
        # variant (number far outside rounding, or a direction flip) or nothing
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "error_injection_eval",
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "benchmarks", "error_injection_eval.py"))
        eie = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(eie)
        bad, kind = eie.inject("Use grew by 34 percent in 2024.")
        self.assertIn("125", bad)                  # 34 * 3.7 -> 125, digits replaced
        self.assertTrue(kind.startswith("number"))
        bad, kind = eie.inject("Adoption grew sharply worldwide.")
        self.assertIn("shrank", bad)
        self.assertTrue(kind.startswith("flip"))
        # no number, no flippable word -> no unsafe fabrication
        self.assertEqual(eie.inject("The report describes policy."), (None, None))
        # whole-word only: 'morecambe' must not flip 'more'
        bad, _ = eie.inject("Morecambe Bay hosts wind turbines.")
        self.assertIsNone(bad)

    def test_high_concurrency_is_clamped_for_the_shared_subscription(self):
        # P2.1b: a big --concurrency trips the CLI's rate ceiling → clamp + warn.
        import verify_my_text as v
        from modules.papertrail.claude_code_backend import RECOMMENDED_MAX_CONCURRENCY
        a = self._args(backend="claude-code", concurrency=12)
        v.apply_backend(a)
        self.assertEqual(a.concurrency, RECOMMENDED_MAX_CONCURRENCY)

    def test_safe_concurrency_is_left_alone(self):
        import verify_my_text as v
        a = self._args(backend="claude-code", concurrency=4)
        v.apply_backend(a)
        self.assertEqual(a.concurrency, 4)

    def test_api_backend_concurrency_is_never_clamped(self):
        import verify_my_text as v
        a = self._args(model="gemini/gemini-2.5-flash-lite", concurrency=12)
        v.apply_backend(a)
        self.assertEqual(a.concurrency, 12)

    def test_override_reaches_load_prompt(self):
        import verify_my_text as v
        v.apply_backend(self._args(backend="claude-code"))
        text = matcher._load_prompt("pt_combined_judgment_prompt.txt")
        self.assertIn("NUMBERS AND MAGNITUDES", text)     # the Haiku-tuned rubric
        # other prompts keep coming from config/prompts
        other = matcher._load_prompt("pt_extract_evidence_prompt.txt")
        self.assertNotIn("NUMBERS AND MAGNITUDES", other)
