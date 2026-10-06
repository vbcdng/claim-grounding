"""Card #141: a second gate arm reuses the earlier arm's answer for every
byte-identical request and asks the model only the rest. All offline — the
network call is stubbed; the gate test drives the real run_gate_gemma.sh with
a stand-in checker (tests/gate_fake_verify_llm.py) that goes through the real
LLMClient.call.

Run:  venv/bin/python3 -m pytest -q tests/test_answer_reuse.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "benchmarks"))

from modules.papertrail import llm_client  # noqa: E402
from modules.papertrail.llm_client import LLMClient  # noqa: E402
import gate_reuse  # noqa: E402

GEMMA = "gemini/gemma-4-31b-it"
ENV_VARS = ("PAPERTRAIL_REUSE_FROM", "PAPERTRAIL_RECORD_REQUESTS",
            "PAPERTRAIL_STABILITY_RUN", "PAPERTRAIL_LLM_EXTRA_BODY",
            "PAPERTRAIL_LOG_PROMPTS")
# The exact field set of a call-log line before card #141 (no claim_id given,
# a stubbed response carries no provider/generation id).
PRE_CARD_LOG_KEYS = {"ts", "seq", "purpose", "model", "prompt_tokens",
                     "completion_tokens", "cached_prompt_tokens", "cost_usd",
                     "latency_s", "api_attempts", "temperature",
                     "max_output_tokens", "prompt_sha256", "prompt_chars",
                     "failed", "response_text"}


def _resp(text):
    choice = MagicMock()
    choice.message.content = text
    choice.finish_reason = "stop"
    r = MagicMock()
    r.choices = [choice]
    r.usage.prompt_tokens = 10
    r.usage.completion_tokens = 2
    return r


class _Base(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.pop(k, None) for k in ENV_VARS}
        llm_client.reset_answer_store()
        llm_client.set_call_log(None)
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        llm_client.set_call_log(None)
        llm_client.reset_answer_store()
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def _log(self, name):
        path = os.path.join(self.dir, name)
        llm_client.set_call_log(path)
        return path

    @staticmethod
    def _lines(path):
        with open(path, encoding="utf-8") as f:
            return [json.loads(ln) for ln in f if ln.strip()]

    def _arm(self, log_name, calls, answers, model=GEMMA):
        """Run `calls` = [(prompt, kwargs)] on a client whose model answers
        from `answers` in order; returns (outputs, the completion mock)."""
        self._log(log_name)
        c = LLMClient(model=model)
        mock = MagicMock(side_effect=[_resp(a) for a in answers])
        outs = []
        with patch.object(c, "_completion", mock), \
             patch("litellm.completion_cost", return_value=0.0):
            for prompt, kw in calls:
                outs.append(c.call(prompt, purpose="t", **kw))
        return outs, mock

    def _donor(self, calls, answers):
        os.environ["PAPERTRAIL_RECORD_REQUESTS"] = "1"
        self._arm("a.jsonl", calls, answers)
        os.environ.pop("PAPERTRAIL_RECORD_REQUESTS")
        return os.path.join(self.dir, "a.jsonl")


class TestOffIsUnchanged(_Base):

    def test_request_and_log_line_as_before(self):
        path = self._log("off.jsonl")
        c = LLMClient(model=GEMMA)
        mock = MagicMock(return_value=_resp("ans"))
        with patch.object(c, "_completion", mock), \
             patch("litellm.completion_cost", return_value=0.0):
            self.assertEqual(c.call("hello", temperature=0.2, max_output_tokens=300), "ans")
        kw = dict(mock.call_args.kwargs)
        self.assertEqual("api_key" in kw, bool(c._api_keys))
        kw.pop("api_key", None)
        self.assertEqual(kw, {
            "model": GEMMA,
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.2,
            "max_tokens": min(300, c._output_cap),
            "thinkingConfig": {"thinkingLevel": "MINIMAL"},
        })
        (line,) = self._lines(path)
        self.assertEqual(set(line), PRE_CARD_LOG_KEYS)
        self.assertIsNone(llm_client.reuse_summary())

    def test_recording_adds_only_the_fingerprint(self):
        os.environ["PAPERTRAIL_RECORD_REQUESTS"] = "1"
        path = self._log("rec.jsonl")
        c = LLMClient(model=GEMMA)
        mock = MagicMock(return_value=_resp("ans"))
        with patch.object(c, "_completion", mock), \
             patch("litellm.completion_cost", return_value=0.0):
            c.call("hello")
        self.assertEqual(mock.call_count, 1)
        (line,) = self._lines(path)
        self.assertEqual(set(line), PRE_CARD_LOG_KEYS | {"request_sha256"})
        body = c._request_body("hello", 0.1, 8000)
        self.assertEqual(line["request_sha256"], llm_client.request_fingerprint(body))

    def test_fingerprint_covers_what_is_sent(self):
        c = LLMClient(model=GEMMA)
        mock = MagicMock(return_value=_resp("ans"))
        with patch.object(c, "_completion", mock), \
             patch("litellm.completion_cost", return_value=0.0):
            c.call("hello", temperature=0.3, max_output_tokens=77)
        sent = dict(mock.call_args.kwargs)
        sent.pop("api_key", None)
        self.assertEqual(sent, c._request_body("hello", 0.3, 77))


class TestReuse(_Base):

    def test_identical_served_changed_asked(self):
        donor = self._donor([("q1", {}), ("q2", {}), ("q3", {})], ["A1", "A2", "A3"])
        os.environ["PAPERTRAIL_REUSE_FROM"] = donor
        outs, mock = self._arm("b.jsonl", [("q1", {}), ("q2 reworded", {}), ("q3", {})],
                               ["B2"])
        self.assertEqual(outs, ["A1", "B2", "A3"])
        self.assertEqual(mock.call_count, 1)
        self.assertEqual(mock.call_args.kwargs["messages"][0]["content"], "q2 reworded")
        lines = self._lines(os.path.join(self.dir, "b.jsonl"))
        self.assertEqual([ln.get("reused", False) for ln in lines], [True, False, True])
        self.assertEqual(lines[0]["reused_from"], donor)
        self.assertEqual(lines[0]["api_attempts"], 0)
        self.assertTrue(all(ln.get("request_sha256") for ln in lines))
        self.assertEqual(llm_client.reuse_summary(),
                         {"loaded": 3, "served": 2, "asked_live": 1})

    def test_every_request_field_is_part_of_the_match(self):
        donor = self._donor([("q", {"temperature": 0.1, "max_output_tokens": 500})], ["A"])
        os.environ["PAPERTRAIL_REUSE_FROM"] = donor
        # same prompt, different temperature / output limit: both asked live
        outs, mock = self._arm("b.jsonl",
                               [("q", {"temperature": 0.7, "max_output_tokens": 500}),
                                ("q", {"temperature": 0.1, "max_output_tokens": 501})],
                               ["L1", "L2"])
        self.assertEqual(outs, ["L1", "L2"])
        self.assertEqual(mock.call_count, 2)

    def test_other_model_is_asked(self):
        donor = self._donor([("q", {})], ["A"])
        os.environ["PAPERTRAIL_REUSE_FROM"] = donor
        outs, mock = self._arm("b.jsonl", [("q", {})], ["L"],
                               model="gemini/gemini-2.5-flash-lite")
        self.assertEqual(outs, ["L"])
        self.assertEqual(mock.call_count, 1)

    def test_provider_setting_change_is_asked(self):
        donor = self._donor([("q", {})], ["A"])
        os.environ["PAPERTRAIL_REUSE_FROM"] = donor
        os.environ["PAPERTRAIL_LLM_EXTRA_BODY"] = json.dumps(
            {"gemma-4": {"thinkingConfig": {"thinkingLevel": "HIGH"}}})
        outs, mock = self._arm("b.jsonl", [("q", {})], ["L"])
        self.assertEqual(outs, ["L"])
        self.assertEqual(mock.call_count, 1)

    def test_repeats_are_served_at_most_as_often_as_recorded(self):
        donor = self._donor([("q", {}), ("q", {})], ["x", "y"])
        os.environ["PAPERTRAIL_REUSE_FROM"] = donor
        outs, mock = self._arm("b.jsonl", [("q", {})] * 3, ["live"])
        self.assertEqual(outs, ["x", "y", "live"])
        self.assertEqual(mock.call_count, 1)

    def test_failed_answer_is_never_reused(self):
        os.environ["PAPERTRAIL_RECORD_REQUESTS"] = "1"
        self._log("a.jsonl")
        c = LLMClient(model=GEMMA)
        with patch.object(c, "_completion", return_value=_resp("")), \
             patch("litellm.completion_cost", return_value=0.0):
            self.assertIsNone(c.call("q"))
        os.environ.pop("PAPERTRAIL_RECORD_REQUESTS")
        os.environ["PAPERTRAIL_REUSE_FROM"] = os.path.join(self.dir, "a.jsonl")
        outs, mock = self._arm("b.jsonl", [("q", {})], ["L"])
        self.assertEqual(outs, ["L"])
        self.assertEqual(mock.call_count, 1)

    def test_donor_without_fingerprints_reuses_nothing(self):
        self._arm("a.jsonl", [("q", {})], ["A"])          # recorded with reuse off
        os.environ["PAPERTRAIL_REUSE_FROM"] = os.path.join(self.dir, "a.jsonl")
        outs, mock = self._arm("b.jsonl", [("q", {})], ["L"])
        self.assertEqual(outs, ["L"])
        self.assertEqual(llm_client.reuse_summary()["loaded"], 0)

    def test_missing_donor_file_is_an_error(self):
        os.environ["PAPERTRAIL_REUSE_FROM"] = os.path.join(self.dir, "nope.jsonl")
        with self.assertRaises(FileNotFoundError):
            llm_client.answer_store_active()

    def test_stability_run_refuses(self):
        donor = self._donor([("q", {})], ["A"])
        os.environ["PAPERTRAIL_REUSE_FROM"] = donor
        os.environ["PAPERTRAIL_STABILITY_RUN"] = "1"
        with self.assertRaises(RuntimeError) as cm:
            self._arm("b.jsonl", [("q", {})], ["L"])
        self.assertIn("stability", str(cm.exception))

    def test_claude_code_backend_untouched(self):
        from modules.papertrail.claude_code_backend import ClaudeCodeClient
        self.assertIsNot(ClaudeCodeClient._call_impl, LLMClient._call_impl)


class TestGateReuseCount(_Base):

    def test_counts_only_lines_after_the_mark(self):
        out = self.dir
        log = os.path.join(out, "llm_calls.jsonl")
        with open(log, "w") as f:
            f.write(json.dumps({"reused": True}) + "\n")      # an earlier test's line
        gate_reuse.mark(out)
        with open(log, "a") as f:
            for r in ({"reused": True}, {}, {"reused": True}):
                f.write(json.dumps(r) + "\n")
        self.assertEqual(gate_reuse.summary(out), "reused 2 of 3 calls")


# ------------------------------------------------- the real gate script, end to end

GATE = os.path.join(ROOT, "benchmarks", "run_gate_gemma.sh")
FAKE = os.path.join(ROOT, "tests", "gate_fake_verify_llm.py")


def _gate(tmp, tag, **extra):
    env = {k: v for k, v in os.environ.items() if k not in ENV_VARS}
    for k in ("RESUME", "PROMPTS", "EXTRA_FLAGS", "REUSE_FROM", "RECORD_REQUESTS",
              "STABILITY_RUN", "FREE_GOOGLE_ONLY"):
        env.pop(k, None)
    rows = []
    for name in ("alpha", "beta"):
        d = os.path.join(tmp, "in", name)
        os.makedirs(os.path.join(d, "sources"), exist_ok=True)
        with open(os.path.join(d, "my_text.md"), "w") as f:
            f.write(f"{name} claim [[a]].\n")
        with open(os.path.join(d, "sources", "a.txt"), "w") as f:
            f.write(f"{name} source.\n")
        rows.append(f"{name}|{d}/my_text.md|{d}/sources|{tmp}/nodonor")
    env.update({"PY": sys.executable, "GATE_TEXTS": ";".join(rows),
                "GATE_OUT_ROOT": os.path.join(tmp, "out"), "GATE_VERIFY_SCRIPT": FAKE,
                "GATE_CODE_COMMIT": "c0ffee"})
    env.update(extra)
    r = subprocess.run(["bash", GATE, tag, "2"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout + r.stderr


def _log_lines(tmp, tag, name):
    p = os.path.join(tmp, "out", f"gate_{tag}_{name}", "llm_calls.jsonl")
    with open(p) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


class TestGateScript(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_second_arm_reuses_identical_calls(self):
        _rc, log_a = _gate(self.tmp, "armA")
        a = _log_lines(self.tmp, "armA", "alpha")
        self.assertEqual(len(a), 3)
        self.assertTrue(all(ln.get("request_sha256") for ln in a))   # gate records by default
        self.assertNotIn("ANSWERS REUSED", log_a)
        _rc, log_b = _gate(self.tmp, "armB", REUSE_FROM="armA", FAKE_CHANGE="1")
        for name in ("alpha", "beta"):
            b = _log_lines(self.tmp, "armB", name)
            self.assertEqual([ln.get("reused", False) for ln in b], [True, False, True])
            self.assertTrue(b[1]["response_text"].endswith("(reworded)"))
            if name == "alpha":
                self.assertEqual(b[0]["response_text"], a[0]["response_text"])
                self.assertEqual(b[0]["request_sha256"], a[0]["request_sha256"])
        self.assertIn("ANSWERS REUSED FROM ARM armA", log_b)
        self.assertIn("alpha      reused 2 of 3 calls", log_b)
        self.assertIn("beta       reused 2 of 3 calls", log_b)

    def test_record_off_logs_as_before(self):
        _gate(self.tmp, "armA", RECORD_REQUESTS="0")
        for ln in _log_lines(self.tmp, "armA", "alpha"):
            self.assertNotIn("request_sha256", ln)

    def test_refuses_own_tag(self):
        rc, log = _gate(self.tmp, "armA", REUSE_FROM="armA")
        self.assertEqual(rc, 2)
        self.assertIn("own tag", log)

    def test_refuses_stability_run(self):
        rc, log = _gate(self.tmp, "armB", REUSE_FROM="armA", STABILITY_RUN="1")
        self.assertEqual(rc, 2)
        self.assertIn("verdict-stability measurement", log)

    def test_refuses_verdict_vote(self):
        rc, log = _gate(self.tmp, "armB", REUSE_FROM="armA",
                        EXTRA_FLAGS="--verdict-vote")
        self.assertEqual(rc, 2)
        self.assertIn("verdict-stability measurement", log)

    def test_missing_donor_text_runs_live(self):
        _rc, log = _gate(self.tmp, "armB", REUSE_FROM="nosuch")
        self.assertIn("every call is asked live", log)
        self.assertIn("alpha      reused 0 of 3 calls", log)


if __name__ == "__main__":
    unittest.main()
