"""_extra_body_for: the built-in thinking-off default for deepseek-v4-flash
(DeepSeek's 2026-07-31 refresh returns EMPTY on arbiter-length prompts with
thinking on — the production arbiter silently annotated nothing) and its
interaction with the PAPERTRAIL_LLM_EXTRA_BODY env override. Fully offline."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail.llm_client import _extra_body_for

THINKING_OFF = {"thinking": {"type": "disabled"}}


class TestBuiltinExtraBody(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("PAPERTRAIL_LLM_EXTRA_BODY", None)

    def test_default_arbiter_gets_thinking_off(self):
        self.assertEqual(_extra_body_for("deepseek/deepseek-v4-flash"),
                         THINKING_OFF)

    def test_explicit_refresh_name_also_covered(self):
        self.assertEqual(_extra_body_for("deepseek/deepseek-v4-flash-0731"),
                         THINKING_OFF)

    def test_openrouter_route_untouched(self):
        # OpenRouter has its own reasoning switch; the native `thinking`
        # param must not ride along on a routed model string.
        self.assertIsNone(_extra_body_for("openrouter/deepseek/deepseek-v4-flash"))

    def test_other_models_untouched(self):
        self.assertIsNone(_extra_body_for("gemini/gemini-2.5-flash-lite"))
        self.assertIsNone(_extra_body_for("deepseek/deepseek-v4-pro"))

    def test_google_direct_gemma_gets_thinking_minimal(self):
        # Free-tier gemma on Google's API: hidden thinking must be off or long
        # answers come back empty. Prefix covers 31b-it and 26b-a4b-it.
        expected = {"thinkingConfig": {"thinkingLevel": "MINIMAL"}}
        self.assertEqual(_extra_body_for("gemini/gemma-4-31b-it"), expected)
        self.assertEqual(_extra_body_for("gemini/gemma-4-26b-a4b-it"), expected)

    def test_openrouter_gemma_untouched(self):
        # OpenRouter's gemma serving has no Google thinkingConfig field.
        self.assertIsNone(_extra_body_for("openrouter/google/gemma-4-31b-it"))

    def test_env_override_wins(self):
        os.environ["PAPERTRAIL_LLM_EXTRA_BODY"] = \
            '{"deepseek/deepseek-v4-flash": {"reasoning": {"enabled": false}}}'
        self.assertEqual(_extra_body_for("deepseek/deepseek-v4-flash"),
                         {"reasoning": {"enabled": False}})

    def test_env_empty_payload_disables_builtin(self):
        os.environ["PAPERTRAIL_LLM_EXTRA_BODY"] = \
            '{"deepseek/deepseek-v4-flash": {}}'
        self.assertIsNone(_extra_body_for("deepseek/deepseek-v4-flash"))

    def test_env_for_other_model_leaves_builtin(self):
        os.environ["PAPERTRAIL_LLM_EXTRA_BODY"] = \
            '{"qwen3.7-flash": {"reasoning": {"enabled": false}}}'
        self.assertEqual(_extra_body_for("deepseek/deepseek-v4-flash"),
                         THINKING_OFF)

    def test_invalid_env_json_falls_back_to_builtin(self):
        os.environ["PAPERTRAIL_LLM_EXTRA_BODY"] = "{not json"
        self.assertEqual(_extra_body_for("deepseek/deepseek-v4-flash"),
                         THINKING_OFF)


if __name__ == "__main__":
    unittest.main()
