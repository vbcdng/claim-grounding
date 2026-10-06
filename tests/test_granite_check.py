"""Task #65: the free local second opinion on APPROVED claims.

Granite Guardian is a freely usable checker model whose weight file sits on this
computer. This pass hands it every claim the judge approved plus a
six-thousand-character excerpt of the cited source, and turns a disagreement
into a caution chip worded as a question. It never changes a verdict.

The tests that matter most here are the DRIFT tests. The measured numbers this
whole feature rests on (it queries 89% of the approved claims the tool got wrong
and 35% of the ones it got right) were produced by one exact prompt on one exact
excerpt geometry, in benchmarks/task54_grounding_checkers/. If either copy
drifts, the numbers silently stop describing the shipped code — so two tests
compare our copies against the benchmark's originals directly.

No network, no API calls, no model loading: the model is replaced by a stub that
returns whatever score the test wants.

Run:  venv/bin/python3 -m unittest tests.test_granite_check -v
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import granite_check as gc
from modules.papertrail import granite_model as gm
from modules.papertrail import viewer, viewer_v2

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(REPO, "benchmarks", "task54_grounding_checkers")


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _analysis(claims):
    return {
        "metadata": {"model": "fake/model", "timestamp": "2026-09-03T00:00:00Z",
                     "text_file": "t.md"},
        "text_claims": claims,
        "sources": [{"paper_id": "p1", "key": "a", "filename": "p1.txt",
                     "title": "Source One", "num_claims": 1}],
        "coverage": {"totals": {"supported": len(claims)}},
        "omitted": [],
    }


def _claim(cid, text, verdict="supported", paper_ids=("p1",), sentence=None):
    return {"id": cid, "text": text, "verdict": verdict, "markers": ["a"],
            "paper_ids": list(paper_ids), "reason": "ok", "cosine": 0.9,
            "evidences": [{"paper_id": pid, "source_title": "Source One",
                           "supported": True, "sentence": sentence or text,
                           "page": 1} for pid in paper_ids]}


class StubRunner:
    """Stands in for the model: answers with a score per source, in order."""

    def __init__(self, scores):
        self.scores = list(scores)
        self.calls = []

    def score(self, claim_text, evidence):
        self.calls.append((claim_text, evidence))
        s = self.scores.pop(0)
        if isinstance(s, Exception):
            raise s
        return {"score": s, "truncated": False, "score_is_approx": False,
                "seconds": 1.0}


def _run_dir(tmp, sources):
    """A folder shaped like a finished run: analysis.json + the sentence index."""
    os.makedirs(os.path.join(tmp, "source_claims"), exist_ok=True)
    for pid, (key, sentences) in sources.items():
        with open(os.path.join(tmp, "source_claims", f"{pid}.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"paper_id": pid, "key": key, "title": f"Title {key}",
                       "sentences": [{"text": s, "page": 1} for s in sentences]}, f)
    return tmp


# ---------------------------------------------------------------------------
# drift: our copies must still equal the benchmark's originals
# ---------------------------------------------------------------------------

@unittest.skipUnless(os.path.exists(os.path.join(BENCH, "run_checkers.py")),
                     "the benchmark folder is private; the public copy has no "
                     "benchmarks/task54_grounding_checkers (card 166)")
class TestNoDriftFromTheBenchmark(unittest.TestCase):
    """If these fail, the measured 89%/35% no longer describe the shipped code."""

    def test_prompt_is_the_benchmark_s_prompt(self):
        env = dict(os.environ)
        try:
            rc = _load_module(os.path.join(BENCH, "run_checkers.py"), "rc_for_test")
        finally:
            os.environ.clear()
            os.environ.update(env)
        claim = "Chimpanzees prepare for two possible outcomes."
        evidence = "In the study, apes covered both holes when either could deliver."
        self.assertEqual(gm.build_prompt(claim, evidence),
                         rc._build_granite_prompt(claim, evidence))
        self.assertEqual(gm.GRANITE_SYSTEM_MESSAGE, rc.GRANITE_SYSTEM_MESSAGE)
        self.assertEqual(gm.GRANITE_GROUNDEDNESS_CRITERIA, rc.GRANITE_GROUNDEDNESS_CRITERIA)
        self.assertEqual(gm.GRANITE_SCORING_SCHEMA_ASSISTANT,
                         rc.GRANITE_SCORING_SCHEMA_ASSISTANT)

    def test_score_reading_is_the_benchmark_s(self):
        env = dict(os.environ)
        try:
            rc = _load_module(os.path.join(BENCH, "run_checkers.py"), "rc_for_test2")
        finally:
            os.environ.clear()
            os.environ.update(env)
        lp = {"tokens": ["<score>", "no", "</score>"],
              "top_logprobs": [{}, {"no": -0.02, "yes": -3.9}, {}],
              "token_logprobs": [-0.1, -0.02, -0.1]}
        text = "<score>no</score>"
        self.assertEqual(gm.parse_output(text, lp), rc._parse_granite_output(text, lp))

    def test_excerpt_geometry_is_the_benchmark_s(self):
        bc = _load_module(os.path.join(BENCH, "build_corpus.py"), "bc_for_test")
        body = ("Alpha beta gamma delta epsilon. " * 400
                + "The apes covered both holes when either could deliver a reward. "
                + "Zeta eta theta iota kappa. " * 400)
        for needle in ["The apes covered both holes when either could deliver a reward.",
                       "apes covered holes reward", "nothing here matches at all",
                       ""]:
            self.assertEqual(gm.slice_for(body, needle),
                             bc.evidence_large_for(body, needle),
                             f"excerpt differs for needle {needle!r}")
        self.assertEqual(gm.SLICE_CHARS, 6000)
        self.assertEqual(gm.THRESHOLD, 0.5)


# ---------------------------------------------------------------------------
# reading the model's answer
# ---------------------------------------------------------------------------

class TestParseOutput(unittest.TestCase):

    def test_no_means_supported_and_yes_means_not(self):
        lp = {"tokens": ["no"], "top_logprobs": [{"no": -0.01, "yes": -5.0}],
              "token_logprobs": [-0.01]}
        score, approx = gm.parse_output("<score>no</score>", lp)
        self.assertGreater(score, 0.9)     # "no problem found" = supported
        self.assertFalse(approx)
        lp2 = {"tokens": ["yes"], "top_logprobs": [{"no": -5.0, "yes": -0.01}],
               "token_logprobs": [-0.01]}
        score2, _ = gm.parse_output("<score>yes</score>", lp2)
        self.assertLess(score2, 0.1)       # "problem found" = not supported

    def test_missing_tag_is_an_error_not_a_guess(self):
        with self.assertRaises(ValueError):
            gm.parse_output("I would rather not say.", None)

    def test_no_probabilities_falls_back_and_says_so(self):
        score, approx = gm.parse_output("<score>no</score>", None)
        self.assertEqual(score, 1.0)
        self.assertTrue(approx)


# ---------------------------------------------------------------------------
# picking claims and building the excerpt
# ---------------------------------------------------------------------------

class TestPickingAndExcerpts(unittest.TestCase):

    def test_only_approved_claims_with_evidence_are_looked_at(self):
        a = _analysis([
            _claim("t0", "approved one"),
            _claim("t1", "rejected one", verdict="unsupported"),
            _claim("t2", "the author's own", verdict="own"),
        ])
        a["text_claims"].append({"id": "t3", "text": "approved, no evidence",
                                 "verdict": "supported", "evidences": []})
        picked = [c["id"] for c in gc.approved_claims(a)]
        self.assertEqual(picked, ["t0"])

    def test_excerpt_is_six_thousand_characters_of_the_cited_source(self):
        long_source = ["Filler sentence number %d about unrelated matters." % i
                       for i in range(300)]
        long_source.insert(150, "The apes covered both holes when either could deliver.")
        with tempfile.TemporaryDirectory() as tmp:
            _run_dir(tmp, {"p1": ("a", long_source)})
            sources = gc.load_sources(tmp)
            c = _claim("t0", "Apes prepared for both outcomes.",
                       sentence="The apes covered both holes when either could deliver.")
            sl = gc.slices_for_claim(c, sources)
            self.assertEqual(len(sl), 1)
            self.assertEqual(len(sl[0]["text"]), 6000)
            self.assertEqual(sl[0]["centred_on"], "evidence sentence")
            # the window really is centred on the evidence, not on the start
            self.assertIn("covered both holes", sl[0]["text"])

    def test_a_short_source_is_handed_over_whole(self):
        with tempfile.TemporaryDirectory() as tmp:
            _run_dir(tmp, {"p1": ("a", ["One short sentence.", "Another one."])})
            sl = gc.slices_for_claim(_claim("t0", "Something."),
                                     gc.load_sources(tmp))
            self.assertEqual(sl[0]["text"], "One short sentence. Another one.")

    def test_the_claim_text_centres_the_window_when_there_is_no_evidence_sentence(self):
        with tempfile.TemporaryDirectory() as tmp:
            _run_dir(tmp, {"p1": ("a", ["Body text about apes and holes."])})
            c = _claim("t0", "Apes and holes.")
            c["evidences"][0]["sentence"] = ""
            sl = gc.slices_for_claim(c, gc.load_sources(tmp))
            self.assertEqual(sl[0]["centred_on"], "claim text")

    def test_a_claim_citing_several_sources_gets_one_excerpt_each_up_to_the_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            _run_dir(tmp, {p: (p, [f"Body of {p}."]) for p in
                           ("p1", "p2", "p3", "p4")})
            c = _claim("t0", "Claim.", paper_ids=("p1", "p2", "p3", "p4"))
            sl = gc.slices_for_claim(c, gc.load_sources(tmp))
            self.assertEqual(len(sl), gc.MAX_SLICES_PER_CLAIM)
            self.assertEqual([s["paper_id"] for s in sl], ["p1", "p2", "p3"])

    def test_a_source_missing_from_the_index_is_skipped_not_invented(self):
        with tempfile.TemporaryDirectory() as tmp:
            _run_dir(tmp, {"p1": ("a", ["Body."])})
            c = _claim("t0", "Claim.", paper_ids=("p9",))
            self.assertEqual(gc.slices_for_claim(c, gc.load_sources(tmp)), [])


# ---------------------------------------------------------------------------
# the answer per claim
# ---------------------------------------------------------------------------

class TestCheckClaim(unittest.TestCase):

    def _slices(self, n=1):
        return [{"paper_id": f"p{i}", "key": f"k{i}", "title": "T",
                 "centred_on": "evidence sentence", "text": "body " * 10,
                 "source_chars": 50} for i in range(1, n + 1)]

    def test_a_low_score_is_a_disagreement_and_a_high_one_is_not(self):
        r = gc.check_claim(_claim("t0", "x"), self._slices(), StubRunner([0.02]))
        self.assertFalse(r["agrees"])
        self.assertAlmostEqual(r["score"], 0.02)
        r2 = gc.check_claim(_claim("t0", "x"), self._slices(), StubRunner([0.97]))
        self.assertTrue(r2["agrees"])

    def test_the_cutoff_is_the_benchmark_s_half(self):
        self.assertTrue(gc.check_claim(_claim("t0", "x"), self._slices(),
                                       StubRunner([0.5]))["agrees"])
        self.assertFalse(gc.check_claim(_claim("t0", "x"), self._slices(),
                                        StubRunner([0.4999]))["agrees"])

    def test_the_best_cited_source_decides(self):
        """The tool's own rule is that any one cited source may carry a claim, so
        Granite only disagrees when NO cited source convinces it."""
        r = gc.check_claim(_claim("t0", "x"), self._slices(3), StubRunner([0.1, 0.9, 0.2]))
        self.assertTrue(r["agrees"])
        self.assertAlmostEqual(r["score"], 0.9)
        self.assertEqual(r["from_source"], "k2")
        self.assertEqual(len(r["per_source"]), 3)
        r2 = gc.check_claim(_claim("t0", "x"), self._slices(3), StubRunner([0.1, 0.2, 0.3]))
        self.assertFalse(r2["agrees"])

    def test_no_excerpt_and_a_broken_model_both_come_back_as_errors(self):
        self.assertIn("error", gc.check_claim(_claim("t0", "x"), [], StubRunner([])))
        r = gc.check_claim(_claim("t0", "x"), self._slices(),
                           StubRunner([RuntimeError("the helper died")]))
        self.assertIn("the helper died", r["error"])


# ---------------------------------------------------------------------------
# the file on disk: never a stale answer beside a fresh verdict (task #57)
# ---------------------------------------------------------------------------

class TestStaleness(unittest.TestCase):

    def _payload(self, analysis, score=0.1):
        return gc.wrap(analysis, {"t0": {"model": gm.MODEL_NAME, "score": score,
                                         "agrees": score >= 0.5, "threshold": 0.5}})

    def test_a_fresh_answer_is_usable(self):
        a = _analysis([_claim("t0", "the claim")])
        usable, rep = gc.validate(self._payload(a), a)
        self.assertEqual(list(usable), ["t0"])
        self.assertEqual(rep["dropped"], 0)
        self.assertTrue(rep["fingerprint_matches"])

    def test_an_answer_is_dropped_when_the_claim_wording_changed(self):
        a = _analysis([_claim("t0", "the claim")])
        p = self._payload(a)
        b = _analysis([_claim("t0", "a different sentence entirely")])
        usable, rep = gc.validate(p, b)
        self.assertEqual(usable, {})
        self.assertEqual(rep["reasons"].get("claim_text_changed"), 1)

    def test_an_answer_is_dropped_when_the_verdict_changed(self):
        a = _analysis([_claim("t0", "the claim")])
        p = self._payload(a)
        b = _analysis([_claim("t0", "the claim", verdict="unsupported")])
        usable, rep = gc.validate(p, b)
        self.assertEqual(usable, {})
        self.assertEqual(rep["reasons"].get("verdict_changed"), 1)

    def test_a_deep_check_file_is_not_mistaken_for_a_local_checker_file(self):
        """The two side-files share the staleness machinery but not the format
        name, so neither can read the other's answers."""
        from modules.papertrail import deep_check_store as dcs
        a = _analysis([_claim("t0", "the claim")])
        dc_payload = dcs.wrap(a, "some/model", {"t0": {"supported": True,
                                                       "agrees": True}})
        usable, rep = gc.validate(dc_payload, a)
        self.assertEqual(usable, {})
        self.assertEqual(rep["reasons"].get("unusable_result"), 1)

    def test_a_re_run_sets_the_old_file_aside(self):
        a = _analysis([_claim("t0", "the claim")])
        with tempfile.TemporaryDirectory() as tmp:
            gc.write_payload(tmp, self._payload(a))
            self.assertTrue(os.path.exists(os.path.join(tmp, gc.FILENAME)))
            moved = gc.archive_previous(tmp)
            self.assertTrue(moved.endswith(gc.PREV_FILENAME))
            self.assertFalse(os.path.exists(os.path.join(tmp, gc.FILENAME)))
            self.assertTrue(os.path.exists(os.path.join(tmp, gc.PREV_FILENAME)))
            self.assertIsNone(gc.archive_previous(tmp))

    def test_load_valid_reads_the_file_and_a_missing_file_is_not_an_error(self):
        a = _analysis([_claim("t0", "the claim")])
        with tempfile.TemporaryDirectory() as tmp:
            usable, rep = gc.load_valid(tmp, a)
            self.assertEqual(usable, {})
            self.assertFalse(rep["present"])
            gc.write_payload(tmp, self._payload(a))
            usable, rep = gc.load_valid(tmp, a)
            self.assertEqual(list(usable), ["t0"])

    def test_the_summary_sentence_reads_as_plain_english(self):
        a = _analysis([_claim("t0", "the claim")])
        _, rep = gc.validate(self._payload(a), a)
        s = gc.report_sentence(rep)
        self.assertIn("local-checker answer", s)
        self.assertNotIn("deep-check", s)


# ---------------------------------------------------------------------------
# the card
# ---------------------------------------------------------------------------

class TestTheCard(unittest.TestCase):

    def _html(self, gen, granite):
        a = _analysis([_claim("t0", "Apes prepared for both outcomes.")])
        if granite is not None:
            a["text_claims"][0]["granite_check"] = granite
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "v.html")
            gen(a, out, title="t", source_texts={})
            with open(out, encoding="utf-8") as f:
                return f.read()

    def _flagged(self):
        return {"model": gm.MODEL_NAME, "score": 0.03, "threshold": 0.5,
                "agrees": False, "slice_chars": 6000, "from_source": "smith2021",
                "centred_on": "evidence sentence"}

    # The legend explains the chip on every page, so a card's own chip is
    # recognised by the title text only the card carries.
    CARD_CHIP = 'title="a free checker model running on this'

    def test_a_disagreement_shows_as_a_question_in_both_viewers(self):
        for gen in (viewer.generate, viewer_v2.generate):
            html = self._html(gen, self._flagged())
            self.assertIn(self.CARD_CHIP, html)
            self.assertIn("worth a second look?", html)
            # worded as a question, and it says how often it is wrong
            self.assertIn("question, not a finding", html)
            self.assertIn("2.3", html)
            # and it admits what it cannot see
            self.assertIn("right paper", html)
            # the verdict itself is untouched
            self.assertIn("unchanged", html)

    def test_agreement_and_absence_both_show_nothing(self):
        for gen in (viewer.generate, viewer_v2.generate):
            agreed = dict(self._flagged(), agrees=True, score=0.95)
            self.assertNotIn(self.CARD_CHIP, self._html(gen, agreed))
            self.assertNotIn(self.CARD_CHIP, self._html(gen, None))

    def test_the_legend_explains_the_chip(self):
        html = self._html(viewer.generate, self._flagged())
        self.assertIn("free checker model running on this computer", html)

    def test_attaching_answers_only_touches_the_matching_claims(self):
        a = _analysis([_claim("t0", "one"), _claim("t1", "two")])
        n = gc.attach(a, {"t1": {"score": 0.1, "agrees": False}})
        self.assertEqual(n, 1)
        self.assertNotIn("granite_check", a["text_claims"][0])
        self.assertIn("granite_check", a["text_claims"][1])


# ---------------------------------------------------------------------------
# the flag
# ---------------------------------------------------------------------------

class TestTheFlag(unittest.TestCase):

    def _verify_source_one_line(self):
        with open(os.path.join(REPO, "verify_my_text.py"), encoding="utf-8") as f:
            return " ".join(f.read().split())

    def test_it_is_off_unless_asked_for_and_its_help_states_the_time_cost(self):
        src = self._verify_source_one_line()
        self.assertIn('"--granite-check", action="store_true"', src)
        self.assertIn("OFF BY DEFAULT", src)
        self.assertIn("Costs no money at all", src)
        # the time cost is stated in the help text itself, in minutes and hours
        self.assertIn("COSTS TIME", src)
        self.assertIn("so twenty approved claims add about an hour to the run", src)

    def test_the_pass_is_wired_in_after_judging_and_cannot_break_a_run(self):
        src = self._verify_source_one_line()
        self.assertIn("granite_check.run_and_write(args.output_dir, analysis)", src)
        self.assertIn("granite_check.archive_previous(args.output_dir)", src)

    def test_a_missing_model_is_a_note_not_a_crash(self):
        """No weights, or no interpreter that can run them: the run survives,
        nothing is written, and no claim gets a chip."""
        a = _analysis([_claim("t0", "the claim")])
        with tempfile.TemporaryDirectory() as tmp:
            _run_dir(tmp, {"p1": ("a", ["Body about apes."])})
            with unittest.mock.patch.object(gm, "have_llama_cpp", return_value=False):
                s = gc.run_and_write(tmp, a, worker_python="/nonexistent/python3")
            self.assertEqual(s["checked"], 0)
            self.assertIn("error", s)
            self.assertNotIn("granite_check", a["text_claims"][0])
            self.assertFalse(os.path.exists(os.path.join(tmp, gc.FILENAME)))


if __name__ == "__main__":
    unittest.main()
