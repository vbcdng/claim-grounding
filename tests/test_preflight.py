"""API-key preflight tests — main() must fail fast (exit 2) when the judge
client's tiny test call returns None, BEFORE any embedding/judging work, and
must proceed past the preflight when the call succeeds. No API/network needed.

Run:  venv/bin/python3 -m unittest tests.test_preflight -v
"""
import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import verify_my_text


class FakeLLM:
    """Stands in for LLMClient at the preflight seam."""
    ping_response = None  # class attr so main()'s internal construction sees it

    def __init__(self, model=None, api_key=None, api_base=None):
        self.model = model or "fake/model"
        self.api_key = api_key
        self.api_base = api_base

    @staticmethod
    def _normalize_model(model):
        return model if model and "/" in model else f"gemini/{model or 'x'}"

    def call(self, prompt, temperature=0.1, max_output_tokens=8000):
        return self.ping_response


class PreflightBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pt-preflight-")
        self.out = os.path.join(self.dir, "out")
        text = os.path.join(self.dir, "my_text.md")
        with open(text, "w", encoding="utf-8") as f:
            f.write("Printing spread fast across Europe [[k1]].\n")
        with open(text + ".refs.txt", "w", encoding="utf-8") as f:
            f.write("k1 = source1.txt\n")
        src_dir = os.path.join(self.dir, "sources")
        os.mkdir(src_dir)
        with open(os.path.join(src_dir, "source1.txt"), "w", encoding="utf-8") as f:
            f.write("Printing presses appeared in many European cities. "
                    "The spread of printing was rapid.\n")
        self.argv = ["verify_my_text.py",
                     "--text", text, "--sources", src_dir,
                     "--references", text + ".refs.txt",
                     "--output-dir", self.out,
                     "--model", "gemini/gemini-2.5-flash-lite",
                     "--api-key", "fake-key", "--no-arbiter", "--yes"]

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _run_main(self):
        with patch.object(sys, "argv", self.argv), \
             patch.object(verify_my_text, "LLMClient", FakeLLM), \
             redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            verify_my_text.main()


class TestPreflightFailFast(PreflightBase):
    def test_none_ping_exits_2_before_any_work(self):
        FakeLLM.ping_response = None
        with self.assertRaises(SystemExit) as ctx:
            self._run_main()
        self.assertEqual(ctx.exception.code, 2)
        # Exit happened before Stage 1/3: no analysis was written.
        self.assertFalse(os.path.exists(os.path.join(self.out, "analysis.json")))

    def test_ok_ping_proceeds_past_preflight(self):
        FakeLLM.ping_response = "ok"
        try:
            self._run_main()
        except SystemExit as e:
            self.fail(f"main() exited at the preflight despite a working ping: {e.code}")
        except Exception:
            # Downstream stages run against the fake client and may fail —
            # irrelevant here; the preflight seam let the run through.
            pass


if __name__ == "__main__":
    unittest.main()
