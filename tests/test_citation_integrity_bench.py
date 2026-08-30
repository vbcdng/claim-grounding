"""Citation-Integrity harness (benchmarks/citation_integrity_bench.py): the
citing-sentence normaliser and the two-label scorer. No API calls, no network,
no dataset needed — every row here is synthetic.

The scorer is what turns a run into a published number, so the arithmetic is
pinned here: which fine label lands on which side of the pass/flag line under
each of the two mappings, and that the three error counts (major false-support,
minor false-support, false-flag) stay separate measurements.

Run:  venv/bin/python3 -m unittest tests.test_citation_integrity_bench -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "benchmarks"))

import citation_integrity_bench as ci


def _row(label, **kw):
    """One ground-truth entry."""
    g = {"ci_id": f"dev/ref/{label}", "label": label,
         "evidence_segments": [], "n_evidence": 0}
    g.update(kw)
    return g


def _claim(key, verdict="supported", **kw):
    c = {"id": key, "markers": [key], "verdict": verdict}
    c.update(kw)
    return c


def _run(gt, claims, **meta):
    m = {"split": "dev", "batch": 1, "claims": gt}
    m.update(meta)
    return ci.evaluate({"text_claims": claims}, m)


class TestClaimNormalisation(unittest.TestCase):
    def _mk(self, par, span_text, start=None, end=None, unit="span"):
        start = par.index(span_text) if start is None else start
        end = start + len(span_text) if end is None else end
        row = {"citing_paragraph": par,
               "citation_context": [{"text": span_text, "start": start,
                                     "end": end}]}
        return ci._claim_text(row, "k1", unit)

    def test_bracketed_citation_becomes_the_marker(self):
        par = "Vpx degrades SAMHD1 [<|cit|>]. Other work disagrees."
        text, why = self._mk(par, "Vpx degrades SAMHD1 [<|cit|>]")
        self.assertIsNone(why)
        self.assertEqual(text, "Vpx degrades SAMHD1 [[k1]].")

    def test_co_citations_are_dropped_brackets_and_parens(self):
        par = ("SAMHD1 blocks reverse transcription in THP-1 cells "
               "[<|other_cit|>,9] and macrophages [<|cit|>] (Figure "
               "<|other_cit|>).")
        text, why = self._mk(par, par)
        self.assertIsNone(why)
        self.assertNotIn("<|", text)
        self.assertNotIn("()", text)
        self.assertEqual(text.count("[[k1]]"), 1)
        self.assertTrue(text.endswith("."), text)

    def test_span_missing_the_marker_is_rejected_with_a_reason(self):
        par = "Bulk RNA-seq data from patients [<|cit|>]."
        text, why = self._mk(par, "Bulk RNA-seq data fro")
        self.assertIsNone(text)
        self.assertIn("does not contain the citation marker", why)

    def test_half_cut_co_citation_token_is_rejected(self):
        par = "A result was reported [<|cit|>] and elsewhere [<|other_cit|>]."
        text, why = self._mk(par, "A result was reported [<|cit|>] and "
                                  "elsewhere [<|other_ci")
        self.assertIsNone(text)
        self.assertIn("cuts a co-citation token", why)

    def test_sentence_unit_widens_a_fragment_to_its_sentence(self):
        par = ("Together, these studies suggested that SAMHD1 restricts HIV-1 "
               "[<|cit|>]. A later report disagreed.")
        frag = "SAMHD1 restricts HIV-1 [<|cit|>]"
        span, _ = self._mk(par, frag)
        sent, _ = self._mk(par, frag, unit="sentence")
        self.assertTrue(span.startswith("SAMHD1 restricts"))
        self.assertTrue(sent.startswith("Together, these studies"))
        self.assertNotIn("A later report", sent)

    def test_sentence_unit_does_not_swallow_the_next_sentence(self):
        par = ("Online surveys are recommended [<|cit|>]. This method has "
               "limitations but was preferred.")
        sent, _ = self._mk(par, "Online surveys are recommended [<|cit|>].",
                           unit="sentence")
        self.assertNotIn("This method has limitations", sent)

    def test_abbreviation_is_not_a_sentence_end(self):
        par = ("As shown by Smith et al. the effect is large [<|cit|>]. "
               "Next point.")
        sent, _ = self._mk(par, "the effect is large [<|cit|>]",
                           unit="sentence")
        self.assertIn("Smith et al.", sent)
        self.assertNotIn("Next point", sent)

    def test_et_al_period_does_not_truncate_the_forward_widen(self):
        # cidev0023/cidev0038 shape: the annotated span already ends right
        # after "X et al." — the forward widen must not stop there, it has
        # to keep going to find the real end of the sentence.
        par = ("Using preliminary county data, Abedi et al. [<|cit|>] found "
               "that poverty predicted higher infection. A separate study "
               "disagreed.")
        frag = "Abedi et al. [<|cit|>]"
        sent, _ = self._mk(par, frag, unit="sentence")
        self.assertTrue(sent.startswith("Using preliminary county data"))
        self.assertTrue(sent.endswith("higher infection."), sent)
        self.assertNotIn("A separate study", sent)

    def test_et_al_marker_glued_to_the_period_is_not_a_fragment(self):
        # cidev0078 shape: the citation marker sits directly against the
        # abbreviation's period ("et al.[<|cit|>]", no space) at the START
        # of the widened sentence -- this must not get chopped down to just
        # "Kim et al."
        par = ("Prior work is limited. Kim et al.[<|cit|>] demonstrated "
               "airborne transmission in ferrets. Subsequent work agreed.")
        frag = "Kim et al.[<|cit|>] demonstrated airborne transmission in ferrets."
        sent, _ = self._mk(par, frag, unit="sentence")
        self.assertEqual(sent, "Kim et al.[[k1]] demonstrated airborne "
                                "transmission in ferrets.")

    def test_et_al_with_no_period_still_widens_to_a_full_sentence(self):
        # cidev0079 shape: the source text has NO period at all between "al"
        # and the marker ("Kim et al[<|cit|>] are positive") -- a different
        # path than the abbreviation regex (there's no period to misjudge),
        # so this checks the widening loop doesn't collapse to a 2-word
        # fragment when the span itself is short and unpunctuated there.
        par = ("The ward tested negative. Kim et al[<|cit|>] are positive. "
               "Thus we suspected airborne spread.")
        frag = "Kim et al[<|cit|>] are positive"
        sent, _ = self._mk(par, frag, unit="sentence")
        self.assertGreater(len(sent.split()), 2)
        self.assertIn("Kim et al[[k1]] are positive", sent)
        self.assertTrue(sent.rstrip().endswith((".", "!", "?")))

    def test_eg_ie_cf_vs_fig_no_and_initial_are_not_sentence_ends(self):
        cases = [
            ("e.g.", "Several artifacts were noted, e.g. mislabeled samples "
                     "[<|cit|>]. Later review caught most errors.",
             "mislabeled samples [<|cit|>]", "e.g."),
            ("i.e.", "Only the primary endpoint mattered, i.e. survival at "
                     "90 days [<|cit|>]. Secondary endpoints were exploratory.",
             "survival at 90 days [<|cit|>]", "i.e."),
            ("cf.", "The result differs from prior work, cf. Jones 2019 "
                    "[<|cit|>]. Replication later resolved the discrepancy.",
             "Jones 2019 [<|cit|>]", "cf."),
            ("vs.", "The comparison was treatment vs. placebo [<|cit|>]. "
                    "A significant difference was found.",
             "placebo [<|cit|>]", "vs."),
            ("Fig.", "The trend is visible in Fig. 3 above [<|cit|>]. "
                     "Later panels show the same pattern.",
             "3 above [<|cit|>]", "Fig."),
            ("Figs.", "The trend is visible in Figs. 3 and 4 [<|cit|>]. "
                      "Later panels show the same pattern.",
             "3 and 4 [<|cit|>]", "Figs."),
            ("No.", "The excluded case was No. 5 in the registry [<|cit|>]. "
                    "Later cases were all included.",
             "5 in the registry [<|cit|>]", "No."),
            ("initial", "This point was raised by J. Smith in review "
                        "[<|cit|>]. Later authors agreed with the critique.",
             "Smith in review [<|cit|>]", "J."),
        ]
        for label, par, frag, expect_kept in cases:
            sent, _ = self._mk(par, frag, unit="sentence")
            self.assertIn(expect_kept, sent, label)
            self.assertNotIn("Later", sent, label)
            self.assertNotIn("Secondary", sent, label)
            self.assertNotIn("Replication", sent, label)
            self.assertNotIn("significant difference", sent, label)

    def test_bare_lowercase_no_is_not_treated_as_the_number_abbreviation(self):
        # Regression: "no" (lowercase) used to be in the abbreviation list
        # alongside "No", so an ordinary sentence ending in the word "no."
        # was mistaken for the "No." (number) abbreviation and merged with
        # whatever came before it.
        par = ("We asked whether treatment helped; the answer was no. "
               "Subsequent analysis confirmed this null result [<|cit|>] "
               "across every cohort studied.")
        frag = "confirmed this null result [<|cit|>]"
        sent, _ = self._mk(par, frag, unit="sentence")
        self.assertTrue(sent.startswith("Subsequent analysis"), sent)
        self.assertNotIn("the answer was no", sent)

    def test_capitalized_No_abbreviation_is_still_recognised(self):
        par = ("The excluded case was No. 5 in the registry [<|cit|>]. "
               "Later cases were all included.")
        frag = "5 in the registry [<|cit|>]"
        sent, _ = self._mk(par, frag, unit="sentence")
        self.assertTrue(sent.startswith("The excluded case was No. 5"), sent)


class TestLabelBands(unittest.TestCase):
    def test_strict_line_passes_only_accurate(self):
        self.assertEqual(ci.strict_side("ACCURATE"), "pass")
        for lab in ci.ALL_LABELS - {"ACCURATE"}:
            self.assertEqual(ci.strict_side(lab), "flag", lab)

    def test_grounding_line_passes_accurate_and_provenance(self):
        for lab in {"ACCURATE"} | ci.PROVENANCE:
            self.assertEqual(ci.grounding_side(lab), "pass", lab)
        for lab in ci.MAJOR | ci.MINOR_CONTENT:
            self.assertEqual(ci.grounding_side(lab), "flag", lab)

    def test_etiquette_is_excluded_from_the_grounding_tally(self):
        self.assertIsNone(ci.grounding_side("ETIQUETTE"))
        gt = {"k1": _row("ETIQUETTE"), "k2": _row("ACCURATE")}
        res = _run(gt, [_claim("k1"), _claim("k2")])
        self.assertEqual(res["strict"]["total"], 2)
        self.assertEqual(res["grounding"]["total"], 1)
        self.assertEqual(res["etiquette_rows"], 1)


class TestToolSideCollapse(unittest.TestCase):
    def test_clean_supported_is_a_pass(self):
        res = _run({"k1": _row("ACCURATE")}, [_claim("k1")])
        self.assertEqual(res["rows"][0]["tool"], "pass")
        self.assertEqual(res["strict"], {"ok": 1, "total": 1})

    def test_unsupported_is_a_flag(self):
        res = _run({"k1": _row("CONTRADICT")},
                   [_claim("k1", verdict="unsupported")])
        self.assertEqual(res["rows"][0]["tool"], "flag")
        self.assertEqual(res["strict"], {"ok": 1, "total": 1})

    def test_supported_with_a_coverage_gap_is_a_flag(self):
        res = _run({"k1": _row("OVERSIMPLIFY")},
                   [_claim("k1", covering={"covered": [], "uncovered": ["dose"]})])
        self.assertEqual(res["rows"][0]["tool"], "flag")

    def test_partial_support_flag_is_a_flag(self):
        res = _run({"k1": _row("MISQUOTE")},
                   [_claim("k1", partial_support={"missing": "13 weeks"})])
        self.assertEqual(res["rows"][0]["tool"], "flag")

    def test_missing_claim_counts_as_neither_side(self):
        res = _run({"k1": _row("ACCURATE")}, [])
        self.assertEqual(res["strict"], {"ok": 0, "total": 0})
        self.assertEqual(res["missing"], ["k1"])


class TestErrorCountsStaySeparate(unittest.TestCase):
    def setUp(self):
        # 2 major errors (1 wrongly passed), 2 minor content errors (both
        # wrongly passed), 2 ACCURATE rows (1 wrongly flagged)
        self.gt = {
            "k1": _row("CONTRADICT"), "k2": _row("IRRELEVANT"),
            "k3": _row("MISQUOTE"), "k4": _row("OVERSIMPLIFY"),
            "k5": _row("ACCURATE"), "k6": _row("ACCURATE"),
        }
        self.claims = [
            _claim("k1"),                              # missed major error
            _claim("k2", verdict="unsupported"),       # caught
            _claim("k3"), _claim("k4"),                # missed minor errors
            _claim("k5"),                              # correct pass
            _claim("k6", verdict="unsupported"),       # false flag
        ]
        self.res = _run(self.gt, self.claims)

    def test_the_three_counts(self):
        self.assertEqual(self.res["false_support_major"], {"k": 1, "n": 2})
        self.assertEqual(self.res["false_support_minor"], {"k": 2, "n": 2})
        self.assertEqual(self.res["false_flag_accurate"], {"k": 1, "n": 2})

    def test_strict_tally_counts_every_row(self):
        # correct: k2 (flag), k5 (pass) -> 2 of 6
        self.assertEqual(self.res["strict"], {"ok": 2, "total": 6})

    def test_report_signals_a_major_false_support(self):
        self.assertEqual(ci.report(self.res), 1)

    def test_report_is_clean_when_no_major_row_was_passed(self):
        res = _run({"k1": _row("CONTRADICT")},
                   [_claim("k1", verdict="unsupported")])
        self.assertEqual(ci.report(res), 0)


class TestSpanContextClasses(unittest.TestCase):
    """The span is carved out of a citing sentence; these classes record what it
    left behind, so a disagreement can be checked against span shape."""

    def _ctx(self, par, span_text):
        start = par.index(span_text)
        row = {"citing_paragraph": par,
               "citation_context": [{"text": span_text, "start": start,
                                     "end": start + len(span_text)}]}
        return ci._span_context(row)

    def test_whole_sentence_span_is_self_contained(self):
        par = "Vpx degrades SAMHD1 [<|cit|>]. Other work disagrees."
        c = self._ctx(par, "Vpx degrades SAMHD1 [<|cit|>].")
        self.assertEqual(c["classes"], [])
        self.assertTrue(c["starts_at_sentence_start"])
        self.assertTrue(c["ends_at_sentence_end"])

    def test_attribution_frame_left_behind_is_flagged(self):
        par = ("Vpx lowers viral DNA [9]. Together, these studies suggested "
               "that SAMHD1 restricts HIV-1 in myeloid cells [<|cit|>].")
        c = self._ctx(par, "SAMHD1 restricts HIV-1 in myeloid cells [<|cit|>].")
        self.assertIn("frame_dropped", c["classes"])
        self.assertTrue(c["dropped_frame"].endswith("suggested that"))

    def test_pronoun_opener_is_flagged(self):
        par = ("Ferrets are a standard model [7]. However, they recapitulate "
               "only mild infection [<|cit|>].")
        c = self._ctx(par, "they recapitulate only mild infection [<|cit|>].")
        self.assertIn("pronoun_start", c["classes"])
        self.assertEqual(c["pronoun_start"], "they")

    def test_a_sentence_ending_in_a_superscript_cite_still_counts_as_ending(self):
        # "...cirrhosis.20 Marjot's study found..." — the bare 20 is a citation,
        # not mid-sentence text, so the next span starts a sentence cleanly.
        par = ("Patients face adverse outcomes following COVID-19.20 "
               "Marjot found mortality to be high in cirrhosis [<|cit|>].")
        c = self._ctx(par, "Marjot found mortality to be high in cirrhosis "
                           "[<|cit|>].")
        self.assertTrue(c["starts_at_sentence_start"])
        self.assertEqual(c["classes"], [])

    def test_same_sentence_tail_dropped_is_counted(self):
        par = ("Transmission was shown in ferrets [<|cit|>], hamsters [8], "
               "and rhesus macaques [9].")
        c = self._ctx(par, "Transmission was shown in ferrets [<|cit|>]")
        self.assertIn("tail_dropped", c["classes"])
        self.assertGreaterEqual(c["tail_words_dropped"], 3)

    def test_a_short_tail_is_not_flagged(self):
        par = "Transmission occurs in ferrets [<|cit|>] and mice."
        c = self._ctx(par, "Transmission occurs in ferrets [<|cit|>]")
        self.assertNotIn("tail_dropped", c["classes"])

    def test_mid_clause_start_when_no_frame_or_pronoun(self):
        par = ("Risk rises with age [7]. Other large registries of cirrhosis "
               "patients reported a 38% fatality rate [<|cit|>].")
        c = self._ctx(par, "registries of cirrhosis patients reported a 38% "
                           "fatality rate [<|cit|>].")
        self.assertEqual(c["classes"], ["mid_clause_start"])

    def test_disagreements_are_sliced_by_class(self):
        frame = {"classes": ["frame_dropped"], "dropped_frame": "found that"}
        gt = {
            # a false support sitting in the frame_dropped class
            "k1": _row("CONTRADICT", span_context=frame),
            # a false flag on a self-contained span
            "k2": _row("ACCURATE", span_context={"classes": []}),
            # a correct row, also self-contained
            "k3": _row("ACCURATE", span_context={"classes": []}),
        }
        res = _run(gt, [_claim("k1"), _claim("k2", verdict="unsupported"),
                        _claim("k3")])
        ctx = res["by_context_class"]
        self.assertEqual(ctx["frame_dropped"]["n"], 1)
        self.assertEqual(ctx["frame_dropped"]["strict_false_support"], 1)
        self.assertEqual(ctx["self_contained"]["n"], 2)
        self.assertEqual(ctx["self_contained"]["strict_false_flag"], 1)
        self.assertNotIn("strict_false_support", ctx["self_contained"])
        self.assertEqual(res["rows"][0]["context_classes"], ["frame_dropped"])

    def test_an_older_ground_truth_reports_unclassified_not_self_contained(self):
        res = _run({"k1": _row("ACCURATE")}, [_claim("k1")])   # no span_context
        self.assertEqual(res["by_context_class"]["unclassified"]["n"], 1)
        self.assertNotIn("self_contained", res["by_context_class"])


class TestCoCitationClasses(unittest.TestCase):
    """Whether the paper cited the statement to one article or several decides
    whether the question we ask the tool is a fair one (task #17): the benchmark
    hands us ONE cited article, so on a multi-cited row a red card can be the
    converter's doing rather than the tool's."""

    def test_a_lone_citation_is_single(self):
        c = ci._co_citation("Vpx degrades SAMHD1 [<|cit|>].")
        self.assertEqual(c["class"], "single")
        self.assertTrue(c["is_single_cited"])
        self.assertEqual(c["siblings_in_span"], 0)

    def test_a_shared_bracket_is_shared_spot(self):
        c = ci._co_citation("Vpx degrades SAMHD1 [<|multi_cit|>,9,11].")
        self.assertEqual(c["class"], "shared_spot")
        self.assertFalse(c["is_single_cited"])

    def test_siblings_elsewhere_in_the_span_are_counted(self):
        c = ci._co_citation("Shown in ferrets [<|other_cit|>], hamsters "
                            "[<|other_cit|>] and macaques [<|cit|>].")
        self.assertEqual(c["class"], "siblings_in_span")
        self.assertEqual(c["siblings_in_span"], 2)

    def test_both_kinds_at_once(self):
        c = ci._co_citation("Shown in ferrets [<|other_cit|>] and macaques "
                            "[<|multi_cit|>,4].")
        self.assertEqual(c["class"], "both")
        self.assertFalse(c["is_single_cited"])

    def test_conversion_records_the_class_on_the_emitted_text(self):
        par = ("Transmission was shown in ferrets [<|other_cit|>] and "
               "macaques [<|cit|>].")
        row = {"citing_paragraph": par,
               "citation_context": [{"text": par, "start": 0, "end": len(par)}]}
        c = ci._co_citation(ci._raw_claim_text(row, "span"))
        self.assertEqual(c["class"], "siblings_in_span")

    def test_older_ground_truth_is_recomputed_from_the_stored_span(self):
        g = {"annotated_span": "Vpx degrades SAMHD1 [<|multi_cit|>,9]."}
        info, recomputed = ci._row_co_citation(g)
        self.assertTrue(recomputed)
        self.assertEqual(info["class"], "shared_spot")

    def test_a_recorded_class_is_used_as_is(self):
        g = {"annotated_span": "Vpx degrades SAMHD1 [<|multi_cit|>,9].",
             "co_citation": {"class": "single", "is_single_cited": True,
                             "shared_spot": False, "siblings_in_span": 0}}
        info, recomputed = ci._row_co_citation(g)
        self.assertFalse(recomputed)
        self.assertEqual(info["class"], "single")

    def test_the_single_cited_subset_is_tallied_separately(self):
        single = {"class": "single", "is_single_cited": True,
                  "shared_spot": False, "siblings_in_span": 0}
        multi = {"class": "shared_spot", "is_single_cited": False,
                 "shared_spot": True, "siblings_in_span": 0}
        gt = {"k1": _row("ACCURATE", co_citation=single),
              "k2": _row("ACCURATE", co_citation=multi),
              "k3": _row("ACCURATE", co_citation=multi)}
        # every row wrongly flagged: 3 false alarms overall, only 1 on a row
        # the tool was asked about fairly
        res = _run(gt, [_claim(k, verdict="unsupported") for k in gt])
        self.assertEqual(res["false_flag_accurate"], {"k": 3, "n": 3})
        self.assertEqual(res["single_cited"]["false_flag_accurate"],
                         {"k": 1, "n": 1})
        self.assertEqual(res["single_cited"]["n"], 1)
        self.assertEqual(res["single_cited"]["strict"], {"ok": 0, "total": 1})
        self.assertEqual(res["by_co_citation"]["shared_spot"]["n"], 2)
        self.assertEqual(res["by_co_citation"]["single"]["strict_false_flag"], 1)

    def test_co_citation_classes_sum_to_n(self):
        # unlike span-context classes, a row has exactly one co-citation class
        gt = {"k1": _row("ACCURATE"), "k2": _row("CONTRADICT")}
        res = _run(gt, [_claim("k1"), _claim("k2", verdict="unsupported")])
        self.assertEqual(sum(d["n"] for d in res["by_co_citation"].values()),
                         res["n"])
        self.assertEqual(res["co_citation_recomputed"], 2)


class TestEscalationRate(unittest.TestCase):
    """Share of claims that fell through to full-text component checking — a
    label-free quality proxy that tracked false alarms across the five arms."""

    def test_rate_overall_and_on_accurate_rows(self):
        gt = {"k1": _row("ACCURATE"), "k2": _row("ACCURATE"),
              "k3": _row("CONTRADICT")}
        res = _run(gt, [_claim("k1", method="llm"),
                        _claim("k2", method="llm_fulltext"),
                        _claim("k3", verdict="unsupported",
                               method="llm_fulltext")])
        self.assertEqual(res["escalation"]["llm_fulltext"], 2)
        self.assertEqual(res["escalation"]["judged"], 3)
        self.assertEqual(res["escalation_accurate"],
                         {"llm_fulltext": 1, "judged": 2,
                          "by_method": {"llm": 1, "llm_fulltext": 1}})

    def test_a_claim_with_no_method_is_judged_but_not_escalated(self):
        res = _run({"k1": _row("ACCURATE")}, [_claim("k1")])
        self.assertEqual(res["escalation"],
                         {"llm_fulltext": 0, "judged": 1,
                          "by_method": {"unknown": 1}})

    def test_missing_claims_are_not_counted_as_judged(self):
        res = _run({"k1": _row("ACCURATE")}, [])
        self.assertEqual(res["escalation"]["judged"], 0)


class TestOwnPaperReading(unittest.TestCase):
    """The scored reading since 2026-08-10 (author ruling, task #32 Option B):
    the tool's verdict for the ONE paper the answer key is about, read out of
    the per-source list. The whole-sentence reading stays as a secondary view."""

    def test_sibling_support_passes_the_sentence_but_not_the_paper(self):
        # the key says this citation is faulty; the tool agrees about the paper
        # but passes the sentence because another cited paper covers it
        gt = {"k1": _row("NOT_SUBSTANTIATE")}
        claim = _claim("k1", evidences=[
            {"source_title": "k1", "supported": False},
            {"source_title": "k1_s1", "supported": True}])
        res = _run(gt, [claim])
        self.assertEqual(res["rows"][0]["tool"], "pass")     # whole sentence
        self.assertEqual(res["rows"][0]["own"], "flag")      # that one paper
        self.assertEqual(res["strict"], {"ok": 0, "total": 1})
        self.assertEqual(res["own_paper"]["strict"], {"ok": 1, "total": 1})

    def test_a_warning_chip_flags_the_sentence_but_not_the_paper(self):
        # the paper under test supports the claim; a coverage warning still
        # flags the whole sentence — only the whole-sentence reading complains
        gt = {"k1": _row("ACCURATE")}
        claim = _claim("k1", covering={"covered": [], "uncovered": ["dose"]},
                       evidences=[{"source_title": "k1", "supported": True}])
        res = _run(gt, [claim])
        self.assertEqual(res["rows"][0]["tool"], "flag")
        self.assertEqual(res["rows"][0]["own"], "pass")
        self.assertEqual(res["own_paper"]["false_flag_accurate"],
                         {"k": 0, "n": 1})
        self.assertEqual(res["false_flag_accurate"], {"k": 1, "n": 1})

    def test_the_singular_evidence_field_is_read_too(self):
        gt = {"k1": _row("ACCURATE")}
        claim = _claim("k1", evidence={"source_title": "k1", "supported": True})
        res = _run(gt, [claim])
        self.assertEqual(res["rows"][0]["own"], "pass")

    def test_a_missing_per_source_entry_is_scored_under_neither_side(self):
        gt = {"k1": _row("ACCURATE"), "k2": _row("ACCURATE")}
        res = _run(gt, [_claim("k1"),
                        _claim("k2", evidences=[{"source_title": "k2",
                                                 "supported": True}])])
        self.assertEqual(res["rows"][0]["own"], "NOT_LISTED")
        self.assertEqual(res["own_paper"]["not_listed"], 1)
        self.assertEqual(res["own_paper"]["strict"], {"ok": 1, "total": 1})
        self.assertEqual(res["strict"], {"ok": 2, "total": 2})

    def test_own_reading_false_support_also_stops_the_report(self):
        # the sentence is flagged, but the per-source list wrongly passes the
        # paper on a major-error row — the exit signal must fire on EITHER
        gt = {"k1": _row("CONTRADICT")}
        claim = _claim("k1", verdict="unsupported",
                       evidences=[{"source_title": "k1", "supported": True}])
        res = _run(gt, [claim])
        self.assertEqual(res["false_support_major"], {"k": 0, "n": 1})
        self.assertEqual(res["own_paper"]["false_support_major"],
                         {"k": 1, "n": 1})
        self.assertEqual(ci.report(res), 1)

    def test_missing_claim_is_missing_under_the_own_reading_too(self):
        res = _run({"k1": _row("ACCURATE")}, [])
        self.assertEqual(res["rows"][0]["own"], "MISSING")
        self.assertEqual(res["own_paper"]["strict"], {"ok": 0, "total": 0})


class TestArbiterAdjudication(unittest.TestCase):
    def test_arbiter_proof_flips_the_adjudicated_side_only(self):
        gt = {"k1": _row("ACCURATE")}
        claim = _claim("k1", verdict="unsupported",
                       arbiter={"action": "wrong_or_insufficient_evidence",
                                "proofs": [{"quote": "the source says so"}]})
        res = _run(gt, [claim])
        self.assertEqual(res["rows"][0]["tool"], "flag")
        self.assertEqual(res["rows"][0]["adj"], "pass")
        self.assertEqual(res["strict"], {"ok": 0, "total": 1})
        self.assertEqual(res["strict_adj"], {"ok": 1, "total": 1})
        self.assertTrue(res["has_arbiter"])

    def test_no_arbiter_means_no_adjudicated_section(self):
        res = _run({"k1": _row("ACCURATE")}, [_claim("k1")])
        self.assertFalse(res["has_arbiter"])


class TestEvidenceOverlap(unittest.TestCase):
    GOLD = ("Chronic dietary L-carnitine supplementation in mice markedly "
            "enhanced synthesis of TMAO and increased atherosclerosis.")

    def test_exact_shown_sentence_counts_as_a_hit(self):
        gt = {"k1": _row("ACCURATE", evidence_segments=[self.GOLD],
                         n_evidence=1)}
        claim = _claim("k1", covering={"covered": [{"sentence": self.GOLD}]})
        res = _run(gt, [claim])
        self.assertEqual(res["evidence_overlap"], {"hits": 1, "total": 1})

    def test_unrelated_sentence_is_not_a_hit(self):
        gt = {"k1": _row("ACCURATE", evidence_segments=[self.GOLD],
                         n_evidence=1)}
        claim = _claim("k1", evidence="Participants completed an online survey "
                                      "about job stress during the pandemic.")
        res = _run(gt, [claim])
        self.assertEqual(res["evidence_overlap"], {"hits": 0, "total": 1})

    def test_rows_without_gold_evidence_are_not_counted(self):
        res = _run({"k1": _row("IRRELEVANT")},
                   [_claim("k1", verdict="unsupported")])
        self.assertEqual(res["evidence_overlap"], {"hits": 0, "total": 0})


class TestCitationScopeReporting(unittest.TestCase):
    def test_scoped_rows_are_listed_separately(self):
        gt = {"k1": _row("ETIQUETTE"), "k2": _row("ACCURATE")}
        claims = [_claim("k1", verdict="unsupported",
                         citation_scope={"scope": "methods"}),
                  _claim("k2", citation_scope={"scope": "full"})]
        res = _run(gt, claims)
        self.assertEqual([s["key"] for s in res["scoped"]], ["k1"])


if __name__ == "__main__":
    unittest.main()
