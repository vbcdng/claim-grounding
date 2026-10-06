"""Card #85 follow-up 3: the PAID gate script (run_gate_openrouter.sh) takes the
free script's EXTRA_FLAGS and REUSE_FROM switches, keeps every money guard,
and leaves reused answers out of its spending check.

All offline: the script runs with a stand-in checker (tests/gate_fake_verify_llm.py)
that goes through the real LLMClient.call with the network call replaced, a
dummy key in the environment, and its output folders under a temp root. No
request leaves the machine.

Run:  venv/bin/python3 -m pytest -q tests/test_paid_gate_reuse.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(ROOT, "benchmarks", "run_gate_openrouter.sh")
FAKE = os.path.join(ROOT, "tests", "gate_fake_verify_llm.py")
GO = "test 2026-10-01: offline test of the paid script, nothing is sent"
CLEARED = ("PAPERTRAIL_REUSE_FROM", "PAPERTRAIL_RECORD_REQUESTS",
           "PAPERTRAIL_STABILITY_RUN", "PAPERTRAIL_LLM_EXTRA_BODY",
           "PAPERTRAIL_LOG_PROMPTS", "PROMPTS", "EXTRA_FLAGS", "REUSE_FROM",
           "RECORD_REQUESTS", "STABILITY_RUN", "FREE_GOOGLE_ONLY", "AUTHOR_GO",
           "ESTIMATE_ONLY", "SCORE_ONLY", "MAX_USD", "MODEL")


def _gate(tmp, tag, go=GO, **extra):
    env = {k: v for k, v in os.environ.items() if k not in CLEARED}
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
                "OPENROUTER_API_KEY": "sk-test-not-a-real-key"})
    if go is not None:
        env["AUTHOR_GO"] = go
    env.update(extra)
    r = subprocess.run(["bash", GATE, tag, "2"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout + r.stderr


def _dir(tmp, tag, name):
    return os.path.join(tmp, "out", f"gate_{tag}_{name}")


def _log_lines(tmp, tag, name):
    with open(os.path.join(_dir(tmp, tag, name), "llm_calls.jsonl")) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


class TestPaidGateReuse(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    # ---------------------------------------------------- the two switches work

    def test_second_arm_reuses_identical_calls(self):
        _rc, log_a = _gate(self.tmp, "paidA")
        a = _log_lines(self.tmp, "paidA", "alpha")
        self.assertEqual(len(a), 3)
        self.assertTrue(all(ln.get("request_sha256") for ln in a))  # records by default
        self.assertNotIn("ANSWERS REUSED", log_a)
        _rc, log_b = _gate(self.tmp, "paidB", REUSE_FROM="paidA", FAKE_CHANGE="1")
        for name in ("alpha", "beta"):
            b = _log_lines(self.tmp, "paidB", name)
            self.assertEqual([ln.get("reused", False) for ln in b], [True, False, True])
        self.assertIn("ANSWERS REUSED FROM ARM paidA", log_b)
        self.assertIn("alpha      reused 2 of 3 calls", log_b)
        self.assertIn("beta       reused 2 of 3 calls", log_b)

    def test_extra_flags_reach_both_passes_and_are_stamped(self):
        _rc, log = _gate(self.tmp, "paidA", EXTRA_FLAGS="--aida-grounder --aida-field-tools")
        self.assertIn("extra flags: --aida-grounder --aida-field-tools", log)
        with open(os.path.join(_dir(self.tmp, "paidA", "alpha"), ".extra_flags")) as f:
            self.assertEqual(f.read(), "--aida-grounder --aida-field-tools")

    def test_one_tag_one_set_of_flags(self):
        _gate(self.tmp, "paidA")
        rc, log = _gate(self.tmp, "paidA", EXTRA_FLAGS="--aida-grounder")
        self.assertEqual(rc, 2)
        self.assertIn("different EXTRA_FLAGS", log)

    def test_folder_from_before_the_stamp_reads_as_no_flags(self):
        _gate(self.tmp, "paidA")
        os.remove(os.path.join(_dir(self.tmp, "paidA", "alpha"), ".extra_flags"))
        rc, log = _gate(self.tmp, "paidA")
        self.assertNotIn("different EXTRA_FLAGS", log)
        rc, log = _gate(self.tmp, "paidA", EXTRA_FLAGS="--aida-grounder")
        self.assertEqual(rc, 2)

    # ------------------------------------------- the spending check after reuse

    def test_reused_calls_are_not_priced(self):
        _gate(self.tmp, "paidA")
        _rc, log_b = _gate(self.tmp, "paidB", REUSE_FROM="paidA", FAKE_CHANGE="1")
        # Inflate the reused lines' token counts: if the check priced them, the
        # money line would jump far above the one live call per text.
        for name in ("alpha", "beta"):
            p = os.path.join(_dir(self.tmp, "paidB", name), "llm_calls.jsonl")
            lines = _log_lines(self.tmp, "paidB", name)
            with open(p, "w") as f:
                for ln in lines:
                    if ln.get("reused"):
                        ln["prompt_tokens"] = 10_000_000
                    f.write(json.dumps(ln) + "\n")
        _rc, log = _gate(self.tmp, "paidB", SCORE_ONLY="1", REUSE_FROM="paidA")
        self.assertIn("=== MONEY: about $0.0000", log)

    def test_ceiling_counts_live_calls(self):
        _gate(self.tmp, "paidA")
        p = os.path.join(_dir(self.tmp, "paidA", "alpha"), "llm_calls.jsonl")
        lines = _log_lines(self.tmp, "paidA", "alpha")
        lines[0]["prompt_tokens"] = 100_000_000   # $14 at the dearest seller's rate
        with open(p, "w") as f:
            for ln in lines:
                f.write(json.dumps(ln) + "\n")
        rc, log = _gate(self.tmp, "paidA")
        # The check before each text prices every folder of the arm so far,
        # the one about to be run included, so it stops at once.
        self.assertIn("STOPPED before alpha", log)

    # ------------------------------------------------------------ the refusals

    def test_refuses_own_tag(self):
        rc, log = _gate(self.tmp, "paidA", REUSE_FROM="paidA")
        self.assertEqual(rc, 2)
        self.assertIn("own tag", log)

    def test_refuses_stability_run(self):
        rc, log = _gate(self.tmp, "paidB", REUSE_FROM="paidA", STABILITY_RUN="1")
        self.assertEqual(rc, 2)
        self.assertIn("verdict-stability measurement", log)

    def test_refuses_verdict_vote(self):
        rc, log = _gate(self.tmp, "paidB", REUSE_FROM="paidA", EXTRA_FLAGS="--verdict-vote")
        self.assertEqual(rc, 2)
        self.assertIn("verdict-stability measurement", log)

    def test_refuses_missing_donor_before_sending(self):
        rc, log = _gate(self.tmp, "paidB", REUSE_FROM="nosuch")
        self.assertEqual(rc, 2)
        self.assertIn("cannot donate for alpha", log)
        self.assertFalse(os.path.exists(_dir(self.tmp, "paidB", "alpha")))

    def test_refuses_donor_without_fingerprints(self):
        _gate(self.tmp, "paidA", RECORD_REQUESTS="0")
        for ln in _log_lines(self.tmp, "paidA", "alpha"):
            self.assertNotIn("request_sha256", ln)
        rc, log = _gate(self.tmp, "paidB", REUSE_FROM="paidA")
        self.assertEqual(rc, 2)
        self.assertIn("no request fingerprints", log)

    def test_refuses_donor_from_another_host(self):
        _gate(self.tmp, "paidA")
        for name in ("alpha", "beta"):
            with open(os.path.join(_dir(self.tmp, "paidA", name), ".judge_host"), "w") as f:
                f.write("free-google")
        rc, log = _gate(self.tmp, "paidB", REUSE_FROM="paidA")
        self.assertEqual(rc, 2)
        self.assertIn("not judged on the paid full-precision host", log)

    # ------------------------------------------- the money guards are unchanged

    def test_no_go_still_refused(self):
        rc, log = _gate(self.tmp, "paidA", go=None, REUSE_FROM="other",
                        EXTRA_FLAGS="--aida-grounder")
        self.assertEqual(rc, 3)
        self.assertIn("REFUSED", log)

    def test_canonical_still_refused(self):
        rc, _log = _gate(self.tmp, "canonical")
        self.assertEqual(rc, 2)

    def test_go_written_into_every_folder(self):
        _gate(self.tmp, "paidA", EXTRA_FLAGS="--aida-grounder")
        for name in ("alpha", "beta"):
            with open(os.path.join(_dir(self.tmp, "paidA", name),
                                   ".paid_run_authorization")) as f:
                self.assertEqual(f.read().strip(), GO)

    def test_estimate_only_sends_nothing_and_reports_donors(self):
        rc, log = _gate(self.tmp, "paidB", go=None, ESTIMATE_ONLY="1",
                        REUSE_FROM="paidA", EXTRA_FLAGS="--aida-grounder --aida-field-tools")
        self.assertEqual(rc, 0)
        self.assertIn("ESTIMATE ONLY", log)
        self.assertIn("EXTRA_FLAGS adds the calls", log)
        self.assertIn("donor NOT ready", log)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "out")))


if __name__ == "__main__":
    unittest.main()
