"""Task #2 — numeric cross-check and direction check.

Offline: no model calls (part 1 makes none by design; part 2's client is faked).
The must-flag rows are the mutation bench's known misses (t8, t29) and its other
digit-bearing corruptions; the must-not-flag rows are the false-alarm controls
named in docs/FINDING_D_NUMERIC_MECHANISM_CHECK_DESIGN.md plus the two classes
the 2026-09-03 sweep over 133 finished runs turned up (a source ratio reported
as a percentage, and a figure the writer rounded).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import number_direction as nd


# Stand-ins for the real source text, keeping the exact typography that broke
# the first prototype: a space thousands separator and a ratio-only report.
SHIN = ("Comparison of the highest category of egg consumption with the lowest "
        "resulted in a pooled HR (95% CI) of 0.96 (0.88, 1.05) for overall CVD "
        "and 1.42 (1.09, 1.86) for type 2 diabetes. Of the studies conducted in "
        "diabetic patients, the pooled HR (95% CI) was 1.69 (1.09, 2.62) for "
        "overall CVD.")
ZHONG = ("Among 29 615 adults pooled from 6 prospective cohort studies with a "
         "median follow-up of 17.5 years, each additional 300 mg of dietary "
         "cholesterol consumed per day was associated with higher risk of "
         "incident CVD (adjusted hazard ratio [HR], 1.17; adjusted absolute risk "
         "difference [ARD], 3.24%) and all-cause mortality (adjusted HR, 1.18; "
         "ARD, 4.43%).")
GILENS = ("Our data set of 1,779 policy cases lets us compare the influence of "
          "average citizens with that of economic elites.")
LITERACY = ("Estimates put adult literacy in the region at 16 percent before the "
            "reforms, rising to 38% a century later.")


def claim(text, key="shin2013", verdict="supported"):
    return {"id": "t1", "text": text, "verdict": verdict,
            "markers": [key], "paper_ids": [key]}


class TestExtraction(unittest.TestCase):
    def test_extracts_percent_ratio_count_and_unit(self):
        figs = nd.extract_figures("a 42% higher risk (relative risk 1.42) among "
                                  "29,615 adults taking 300 mg per day")
        kinds = sorted(f["kind"] for f in figs)
        self.assertEqual(kinds, ["count", "decimal", "percent", "unit"])

    def test_verbal_quantities_are_exempt(self):
        self.assertEqual(nd.extract_figures("more than three million person-years"), [])
        self.assertEqual(nd.extract_figures("roughly three-quarters of the sample"), [])

    def test_bare_years_and_small_integers_are_exempt(self):
        self.assertEqual(nd.extract_figures("the 2013 review of type 2 diabetes"), [])

    def test_citation_markers_are_not_mined(self):
        self.assertEqual(nd.extract_figures("as shown [[smith2019]]"), [])

    def test_approximation_word_is_recorded(self):
        figs = nd.extract_figures("examining nearly 1,800 policy questions")
        self.assertTrue(figs[0]["approximate"])

    def test_limit_word_is_recorded(self):
        figs = nd.extract_figures("literacy stayed below roughly 20% throughout")
        self.assertEqual(figs[0]["bound"], "below")


class TestFigurePresence(unittest.TestCase):
    def present(self, wording, source):
        figs = nd.extract_figures(wording)
        self.assertTrue(figs, f"nothing extracted from {wording!r}")
        return nd.figure_present(figs[0], source)[0]

    # --- rule 1: a figure matches only in its own specific form
    def test_ratio_is_never_expanded_to_a_bare_integer(self):
        # The naive prototype accepted the corrupted "RR 1.22" because a bare
        # "22" appears elsewhere in the source. It must not.
        self.assertFalse(self.present("relative risk 1.22", SHIN))

    def test_real_ratio_is_found(self):
        self.assertTrue(self.present("relative risk 1.42", SHIN))

    # --- rule 2: separator variants
    def test_count_matches_across_thousands_separators(self):
        self.assertTrue(self.present("a pooled sample of 29,615 adults", ZHONG))

    # --- the ratio/percentage conversion a writer legitimately makes
    def test_percentage_matches_the_sources_ratio(self):
        self.assertTrue(self.present("a 69% higher risk", SHIN))
        self.assertTrue(self.present("a 42% higher risk", SHIN))

    def test_corrupted_percentage_still_fails(self):
        self.assertFalse(self.present("a 22% higher risk", SHIN))
        self.assertFalse(self.present("a 70% higher risk", ZHONG))

    def test_a_percent_over_a_hundred_is_not_read_as_the_bare_ratio(self):
        # "154% higher" must NOT be corroborated by the source's real "1.54";
        # allowing that reading hid a planted error in the mutation bench.
        self.assertFalse(self.present("risk 154% higher",
                                      "the relative risk was 1.54 (1.14, 2.08)"))

    # --- rounding
    def test_rounding_allowance_is_relative_not_one_whole_unit(self):
        # "6%" must not be satisfied by an unrelated "6.36%" in the source.
        self.assertFalse(self.present("a 6% higher risk",
                                      "the absolute risk difference was 6.36% overall"))
        # but a percentage the source prints with one decimal still matches.
        self.assertTrue(self.present("a 42% higher risk",
                                     "the increase was 41.8% in that group"))

    def test_approximate_count_matches_the_sources_exact_count(self):
        self.assertTrue(self.present("examining nearly 1,800 policy questions", GILENS))

    def test_exact_count_does_not_match_a_different_count(self):
        self.assertFalse(self.present("examining 1,800 policy questions", GILENS))

    # --- limits
    def test_limit_is_satisfied_by_a_figure_on_the_correct_side(self):
        self.assertTrue(self.present("literacy stayed below roughly 20%", LITERACY))

    def test_limit_flags_when_every_source_figure_is_on_the_wrong_side(self):
        figs = nd.extract_figures("literacy stayed below 10% throughout")
        found, why = nd.figure_present(figs[0], LITERACY)
        self.assertFalse(found)
        self.assertIn("other side", why)


class TestClusters(unittest.TestCase):
    def test_percent_and_its_ratio_are_one_figure(self):
        figs = nd.extract_figures("a 42% higher risk (relative risk 1.42)")
        self.assertEqual(len(nd.build_clusters(figs)), 1)

    def test_lower_risk_percent_and_its_ratio_are_one_figure(self):
        figs = nd.extract_figures("an 11% lower risk (hazard ratio 0.89)")
        self.assertEqual(len(nd.build_clusters(figs)), 1)

    def test_unrelated_figures_stay_separate(self):
        figs = nd.extract_figures("17.5 years of follow-up and a 42% higher risk")
        self.assertEqual(len(nd.build_clusters(figs)), 2)


class TestCheckClaim(unittest.TestCase):
    def payload(self, text, source=SHIN, key="shin2013"):
        return nd.check_claim_numbers(claim(text, key), {key: source})

    def test_mutation_bench_t29_is_caught(self):
        p = self.payload("Egg consumers showed a 22% higher risk of type 2 diabetes "
                         "(relative risk 1.22).")
        self.assertEqual(p["missing"], ["1.22 / 22%"])

    def test_mutation_bench_t29_original_is_silent(self):
        p = self.payload("Egg consumers showed a 42% higher risk of type 2 diabetes "
                         "(relative risk 1.42).")
        self.assertEqual(p["missing"], [])

    def test_mutation_bench_t22_is_caught(self):
        p = self.payload("each additional 300 mg of cholesterol was associated with a "
                         "70% higher risk of incident cardiovascular disease (HR 1.70) "
                         "among 29,615 adults", ZHONG, "zhong2019")
        self.assertEqual(p["missing"], ["1.70 / 70%"])

    def test_mutation_bench_t22_original_is_silent(self):
        p = self.payload("each additional 300 mg of cholesterol was associated with a "
                         "17% higher risk of incident cardiovascular disease (HR 1.17) "
                         "among 29,615 adults over 17.5 years", ZHONG, "zhong2019")
        self.assertEqual(p["missing"], [])

    def test_no_figures_means_no_payload(self):
        self.assertIsNone(self.payload("the liver compensates by upregulating synthesis"))

    def test_missing_source_text_means_no_payload(self):
        self.assertIsNone(nd.check_claim_numbers(claim("a 42% higher risk"), {}))


class TestEligibility(unittest.TestCase):
    def test_only_supported_cited_claims(self):
        self.assertTrue(nd.eligible(claim("a 42% risk")))
        self.assertFalse(nd.eligible(claim("a 42% risk", verdict="unsupported")))
        self.assertFalse(nd.eligible(claim("a 42% risk", verdict="own")))
        c = claim("a 42% risk")
        c["markers"], c["paper_ids"] = [], []
        self.assertFalse(nd.eligible(c))

    def test_author_ruled_claims_are_left_alone(self):
        c = claim("a 42% risk")
        c["owner_flag"] = True
        self.assertFalse(nd.eligible(c))


class TestCheckNumbers(unittest.TestCase):
    def test_tags_and_summarises(self):
        good = claim("a 42% higher risk (relative risk 1.42)")
        good["id"] = "t1"
        bad = claim("a 22% higher risk (relative risk 1.22)")
        bad["id"] = "t2"
        s = nd.check_numbers([good, bad], {"shin2013": {"full_text": SHIN}})
        self.assertEqual(s["checked"], 2)
        self.assertEqual(s["flagged_ids"], ["t2"])
        self.assertEqual(good["number_check"]["missing"], [])
        self.assertEqual(bad["number_check"]["missing"], ["1.22 / 22%"])

    def test_reads_the_sentence_index_when_there_is_no_full_text(self):
        c = claim("a 22% higher risk")
        sources = {"shin2013": {"sentences": [{"text": SHIN}]}}
        nd.check_numbers([c], sources)
        self.assertEqual(c["number_check"]["missing"], ["22%"])

    def test_a_stale_tag_is_always_recomputed(self):
        c = claim("a 42% higher risk (relative risk 1.42)")
        c["number_check"] = {"missing": ["nonsense from an older run"]}
        nd.check_numbers([c], {"shin2013": {"full_text": SHIN}})
        self.assertEqual(c["number_check"]["missing"], [])

    def test_verdicts_are_never_touched(self):
        c = claim("a 22% higher risk (relative risk 1.22)")
        nd.check_numbers([c], {"shin2013": {"full_text": SHIN}})
        self.assertEqual(c["verdict"], "supported")


# --------------------------------------------------------------- part 2
class FakeLLM:
    def __init__(self, response):
        self.model = "fake/judge"
        self.response = response
        self.calls = 0
        self.last_prompt = ""

    def call(self, prompt, **kw):
        self.calls += 1
        self.last_prompt = prompt
        return self.response


def dclaim(text, sentence="The liver responds by suppressing its own synthesis."):
    return {"id": "t8", "text": text, "verdict": "supported",
            "markers": ["mcnamara1987"], "paper_ids": ["mcnamara1987"],
            "evidence": {"sentence": sentence, "window": sentence}}


class TestDirectionCue(unittest.TestCase):
    def test_finds_a_mechanism_word(self):
        cue = nd.find_direction_cue("the liver compensates by upregulating synthesis")
        self.assertEqual(cue["kind"], "increase")

    def test_cause_and_effect_wording_wins(self):
        cue = nd.find_direction_cue("higher intake leads to lower absorption")
        self.assertEqual(cue["kind"], "cause")
        self.assertEqual(cue["cue"], "leads to")

    def test_a_named_mechanism_beats_an_incidental_direction_word(self):
        # The real t8 sentence: "when dietary cholesterol rises, the liver
        # compensates by downregulating its own synthesis". The word that
        # matters is the mechanism, not "rises".
        cue = nd.find_direction_cue("When dietary cholesterol rises, the liver "
                                    "compensates by downregulating its own synthesis")
        self.assertEqual(cue["cue"], "downregulat")

    def test_a_plain_claim_has_no_cue(self):
        self.assertIsNone(nd.find_direction_cue("the sample contained 400 adults"))


class TestCheckDirection(unittest.TestCase):
    def test_not_stated_is_flagged(self):
        c = dclaim("the liver compensates by upregulating its own synthesis",
                   "Responders and non-responders differed widely in absorption.")
        llm = FakeLLM('{"answer": "not_stated", "quote": "", '
                      '"reason": "the passage is about responder variation"}')
        s = nd.check_direction([c], llm, workers=1)
        self.assertEqual(s["flagged_ids"], ["t8"])
        self.assertEqual(c["direction_check"]["answer"], "not_stated")
        self.assertEqual(c["verdict"], "supported")

    def test_reversed_is_flagged(self):
        c = dclaim("the liver compensates by upregulating its own synthesis")
        llm = FakeLLM('{"answer": "reversed", "quote": "The liver responds by '
                      'suppressing its own synthesis.", "reason": "opposite"}')
        s = nd.check_direction([c], llm, workers=1)
        self.assertEqual(s["counts"]["reversed"], 1)
        self.assertTrue(c["direction_check"]["quote_verified"])

    def test_confirmed_is_silent(self):
        c = dclaim("the liver compensates by downregulating its own synthesis")
        llm = FakeLLM('{"answer": "confirmed", "quote": "The liver responds by '
                      'suppressing its own synthesis.", "reason": "same direction"}')
        s = nd.check_direction([c], llm, workers=1)
        self.assertEqual(s["flagged_ids"], [])

    def test_an_unverifiable_quote_is_recorded_not_corrected(self):
        c = dclaim("the liver compensates by downregulating its own synthesis")
        llm = FakeLLM('{"answer": "confirmed", "quote": "a sentence that is not in the '
                      'source at all", "reason": "x"}')
        nd.check_direction([c], llm, workers=1)
        self.assertEqual(c["direction_check"]["answer"], "confirmed")
        self.assertFalse(c["direction_check"]["quote_verified"])

    def test_claims_without_a_cue_cost_nothing(self):
        c = dclaim("the sample contained 400 adults")
        llm = FakeLLM('{"answer": "confirmed", "quote": "", "reason": ""}')
        nd.check_direction([c], llm, workers=1)
        self.assertEqual(llm.calls, 0)

    def test_unparseable_answer_leaves_the_claim_untagged(self):
        c = dclaim("the liver compensates by upregulating its own synthesis")
        llm = FakeLLM("I cannot answer that.")
        s = nd.check_direction([c], llm, workers=1)
        self.assertEqual(s["unparsed"], 1)
        self.assertNotIn("direction_check", c)

    def test_same_model_and_prompt_are_not_re_bought(self):
        c = dclaim("the liver compensates by upregulating its own synthesis")
        llm = FakeLLM('{"answer": "not_stated", "quote": "", "reason": "r"}')
        nd.check_direction([c], llm, workers=1)
        s = nd.check_direction([c], llm, workers=1)
        self.assertEqual(s["reused"], 1)
        self.assertEqual(llm.calls, 1)

    def test_the_prompt_carries_the_claim_and_the_proof(self):
        c = dclaim("the liver compensates by upregulating its own synthesis")
        llm = FakeLLM('{"answer": "confirmed", "quote": "", "reason": "r"}')
        nd.check_direction([c], llm, workers=1)
        self.assertIn("upregulating", llm.last_prompt)
        self.assertIn("suppressing its own synthesis", llm.last_prompt)


class TestViewerChips(unittest.TestCase):
    """The flag has to reach the reader, and it must not disturb the badge."""

    def _flagged_claim(self):
        c = claim("Egg consumers showed a 22% higher risk (relative risk 1.22).")
        nd.check_numbers([c], {"shin2013": {"full_text": SHIN}})
        c["evidence"] = {"paper_id": "shin2013", "sentence": SHIN, "cosine": 0.7}
        c["evidences"] = [c["evidence"]]
        return c

    def test_number_flag_shows_on_the_card_in_viewer_one(self):
        from modules.papertrail import viewer
        html = viewer._claim_card(self._flagged_claim(), {}, {}, {})
        self.assertIn("figure not in source", html)
        self.assertIn("1.22 / 22%", html)
        self.assertIn("SUPPORTED", html)          # the verdict badge is unchanged

    def test_number_flag_shows_on_the_card_in_viewer_two(self):
        from modules.papertrail import viewer_v2
        html = viewer_v2._card_v2(self._flagged_claim(), {}, {}, {})
        self.assertIn("figure not in source", html)

    def test_a_clean_claim_gets_no_chip(self):
        from modules.papertrail import viewer
        c = claim("Egg consumers showed a 42% higher risk (relative risk 1.42).")
        nd.check_numbers([c], {"shin2013": {"full_text": SHIN}})
        c["evidence"] = {"paper_id": "shin2013", "sentence": SHIN, "cosine": 0.7}
        html = viewer._claim_card(c, {}, {}, {})
        self.assertNotIn("figure not in source", html)


# ------------------------------------------------- task #99: where the figure sits

# A paper that prints 43% for the reuse rate and 23% for something else. This
# is the exact hole card #99 exists to close: today's check accepts the wrong
# "23% of articles reused data" because 23 appears somewhere in the paper.
REUSE = ("Of the data-using articles we examined, 43% reused data collected by "
         "someone else. "
         "Funding acknowledgements were present in 23% of the articles, a "
         "separate count that has nothing to do with reuse. "
         "Literacy among the surveyed staff reached 12%, far below the national "
         "figure.")
REUSE_PROOF = ("Of the data-using articles we examined, 43% reused data collected "
               "by someone else.")


def scoped(text, source=REUSE, proof=REUSE_PROOF, key="reuse", window=None,
           covering=None):
    """One supported cited claim whose stored proof is `proof`, checked with
    the task #99 scope rule on."""
    c = claim(text, key)
    if proof is not None:
        c["evidences"] = [{"paper_id": key, "sentence": proof,
                           "window": window or proof, "supported": True}]
    if covering is not None:
        c["covering"] = {"covered": [{"paper_id": key, "component": "x",
                                      "sentence": covering}]}
    payload = nd.check_claim_numbers(c, {key: source}, in_proof=True)
    c[nd.FIELD] = payload
    return c, payload


class TestFigureScope(unittest.TestCase):
    def test_default_payload_is_unchanged(self):
        """With the flag off nothing new appears — the 2026-09-03 payload."""
        c = claim("43% of articles reused data.", "reuse")
        p = nd.check_claim_numbers(c, {"reuse": REUSE})
        self.assertNotIn("where", p["clusters"][0])
        self.assertNotIn("elsewhere", p)

    def test_gap_one_wrong_figure_printed_elsewhere_in_the_paper(self):
        """The writer says 23% for the reuse rate; the paper says 43% for reuse
        and 23% for funding. Today it passes; with scope on it is chipped."""
        c, p = scoped("23% of data-using articles reused data collected by others.")
        self.assertEqual(p["missing"], [])            # still found in the paper
        self.assertEqual(p["elsewhere"], ["23%"])
        self.assertEqual(p["clusters"][0]["where"], "paper")

    def test_the_right_figure_is_in_the_proof_sentence(self):
        c, p = scoped("43% of data-using articles reused data collected by others.")
        self.assertEqual(p["elsewhere"], [])
        self.assertEqual(p["clusters"][0]["where"], "sentence")

    def test_a_figure_in_the_retrieved_window_is_accepted(self):
        """The window is still the passage the claim was judged on, so a figure
        there is not chipped — it is only recorded as window rather than
        sentence."""
        c, p = scoped("23% of data-using articles reused data collected by others.",
                      proof="Of the data-using articles we examined, 43% reused data "
                            "collected by someone else.",
                      window=REUSE)
        self.assertEqual(p["elsewhere"], [])
        self.assertEqual(p["clusters"][0]["where"], "window")

    def test_gap_two_a_limit_satisfied_only_by_an_unrelated_percentage(self):
        """'below 20%' must not pass on the paper's unrelated 12%. The proof
        sentence carries 43%, which is on the wrong side of the limit."""
        c, p = scoped("Data reuse stayed below 20% of articles.")
        self.assertEqual(p["missing"], [])            # 12% satisfies it somewhere
        self.assertEqual(p["elsewhere"], ["20%"])

    def test_a_limit_the_proof_sentence_itself_satisfies_is_silent(self):
        c, p = scoped("Data reuse stayed below 50% of articles.")
        self.assertEqual(p["elsewhere"], [])
        self.assertEqual(p["clusters"][0]["where"], "sentence")

    def test_a_covering_set_proof_sentence_counts_as_proof(self):
        c, p = scoped("23% of data-using articles reused data collected by others.",
                      proof=None,
                      covering="Funding acknowledgements were present in 23% of the "
                               "articles, a separate count that has nothing to do "
                               "with reuse.")
        self.assertEqual(p["elsewhere"], [])
        self.assertEqual(p["clusters"][0]["where"], "sentence")

    def test_a_claim_with_no_quoted_proof_is_left_alone(self):
        """No proof text stored means nothing to compare against, so the check
        stays silent rather than chipping every such card."""
        c, p = scoped("23% of data-using articles reused data.", proof=None)
        self.assertFalse(p["proof_shown"])
        self.assertEqual(p["elsewhere"], [])

    def test_a_figure_in_no_source_at_all_keeps_the_old_chip(self):
        c, p = scoped("77% of data-using articles reused data collected by others.")
        self.assertEqual(p["missing"], ["77%"])
        self.assertEqual(p["elsewhere"], [])          # the stronger chip wins
        self.assertEqual(p["clusters"][0]["where"], "none")

    def test_summary_counts_and_ids(self):
        near = claim("43% of data-using articles reused data.", "reuse")
        near["id"] = "t1"
        near["evidences"] = [{"paper_id": "reuse", "sentence": REUSE_PROOF,
                              "window": REUSE_PROOF}]
        away = claim("23% of data-using articles reused data.", "reuse")
        away["id"] = "t2"
        away["evidences"] = [{"paper_id": "reuse", "sentence": REUSE_PROOF,
                              "window": REUSE_PROOF}]
        s = nd.check_numbers([near, away], {"reuse": {"full_text": REUSE}},
                             in_proof=True)
        self.assertEqual(s["flagged_ids"], [])
        self.assertEqual(s["elsewhere_ids"], ["t2"])
        self.assertEqual(s["elsewhere"], 1)

    def test_flag_off_reports_no_scope_counts(self):
        c = claim("23% of data-using articles reused data.", "reuse")
        s = nd.check_numbers([c], {"reuse": {"full_text": REUSE}})
        self.assertNotIn("elsewhere", s)
        self.assertNotIn("elsewhere_ids", s)

    def test_verdict_and_covering_are_untouched(self):
        """Both gate scorers read only `verdict` and `covering`."""
        c, p = scoped("23% of data-using articles reused data.")
        self.assertEqual(c["verdict"], "supported")
        self.assertEqual(c.get("covering"), None)


class TestNoiseClassesFoundByTheScopeSweep(unittest.TestCase):
    """The five false-alarm classes the 2026-09-10 sweep over 564 runs turned
    up, each fixed in the matcher rather than tolerated in the chip."""

    def test_a_confidence_interval_label_is_not_a_figure(self):
        """'95% CI' names a statistic; 24 of the first 55 flags were this."""
        figs = nd.extract_figures("presence of HCC was not associated with mortality "
                                  "(OR 1.46, 95% CI 0.67–3.18, p = 0.346)")
        self.assertNotIn("95%", [f["raw"] for f in figs])
        self.assertIn("1.46", [f["number"] for f in figs])

    def test_a_real_percentage_is_still_mined(self):
        figs = nd.extract_figures("43% of articles reused data")
        self.assertEqual([f["number"] for f in figs], ["43"])

    def test_a_scale_word_makes_the_figure_its_full_value(self):
        figs = nd.extract_figures("pooled from 1.7 million participants")
        self.assertEqual(figs[0]["kind"], "count")
        self.assertEqual(figs[0]["value"], 1700000.0)

    def test_the_source_count_matches_the_claim_s_scale_word(self):
        c = {"text": "pooled from 1.7 million participants", "verdict": "supported",
             "markers": ["s"], "paper_ids": ["s"]}
        out = nd.check_claim_numbers(c, {"s": "A total of 1 720 108 participants "
                                              "were followed."})
        self.assertEqual(out["missing"], [])

    def test_a_stray_matching_decimal_no_longer_corroborates_a_scale_word(self):
        c = {"text": "pooled from 1.7 million participants", "verdict": "supported",
             "markers": ["s"], "paper_ids": ["s"]}
        out = nd.check_claim_numbers(c, {"s": "The hazard ratio was 1.7 in the "
                                              "highest category."})
        self.assertEqual(out["missing"], ["1.7 million"])

    def test_the_source_s_own_scale_word_figure_counts(self):
        """The claim says 'over 5.5 million person-years'; the proof says
        '>5.54 million person years'. One figure, rounded by the writer."""
        c = {"text": "spanning over 5.5 million person-years", "verdict": "supported",
             "markers": ["s"], "paper_ids": ["s"]}
        out = nd.check_claim_numbers(
            c, {"s": "Over up to 32 years of follow-up (>5.54 million person years), "
                     "14 806 participants were identified."})
        self.assertEqual(out["missing"], [])

    def test_a_hyphenated_scale_word_in_the_source_counts(self):
        c = {"text": "the probe travelled 483 million km", "verdict": "supported",
             "markers": ["s"], "paper_ids": ["s"]}
        out = nd.check_claim_numbers(
            c, {"s": "InSight is on a 300-million-mile (483-million-kilometer) trip."})
        self.assertEqual(out["missing"], [])

    def test_the_figure_s_own_form_beats_the_limit_rule(self):
        """'more than 12 million observations' with the source printing exactly
        that: the limit rule used to answer first and call it missing because
        the paper's other counts were smaller."""
        c = {"text": "linked to more than 12 million publication observations",
             "verdict": "supported", "markers": ["s"], "paper_ids": ["s"]}
        out = nd.check_claim_numbers(
            c, {"s": "We link rosters to more than 12 million OpenAlex-indexed "
                     "observations from 2011 to 2020, covering 1,200 departments."})
        self.assertEqual(out["missing"], [])

    def test_an_amount_printed_more_precisely_still_counts(self):
        c = {"text": "cholesterol fell by 4.5 mg/dL", "verdict": "supported",
             "markers": ["s"], "paper_ids": ["s"]}
        out = nd.check_claim_numbers(c, {"s": "Serum cholesterol fell by 4.52 mg/dL "
                                              "on average."})
        self.assertEqual(out["missing"], [])

    def test_a_different_unit_of_the_same_value_does_not_count(self):
        c = {"text": "the dose was 4.5 mg/day", "verdict": "supported",
             "markers": ["s"], "paper_ids": ["s"]}
        out = nd.check_claim_numbers(c, {"s": "Cholesterol fell by 4.52 mg/dL."})
        self.assertEqual(out["missing"], ["4.5 mg/day"])

    def test_pdf_spacing_inside_a_figure_is_repaired(self):
        self.assertEqual(nd.normalize_source("above0 .80 of the total"),
                         "above0.80 of the total")
        self.assertEqual(nd.normalize_source("100 ,000 households"),
                         "100,000 households")

    def test_a_sentence_boundary_after_a_year_is_left_alone(self):
        self.assertEqual(nd.normalize_source("published in 2021 . 30 cases followed"),
                         "published in 2021 . 30 cases followed")

    def test_a_figure_broken_by_pdf_spacing_is_found(self):
        c = {"text": "the survey covered 100,000 households", "verdict": "supported",
             "markers": ["s"], "paper_ids": ["s"]}
        out = nd.check_claim_numbers(c, {"s": "We surveyed 100 ,000 households."})
        self.assertEqual(out["missing"], [])

    def test_a_percentage_the_source_spells_out_is_found(self):
        c = {"text": "literacy stayed below roughly 20% of the population",
             "verdict": "supported", "markers": ["s"], "paper_ids": ["s"]}
        out = nd.check_claim_numbers(
            c, {"s": "Literacy rates in Western European countries during the Middle "
                     "Ages were below twenty percent of the population."})
        self.assertEqual(out["missing"], [])

    def test_a_spelled_out_percentage_is_read_as_its_value(self):
        self.assertEqual(nd._word_percent_values("below twenty percent"), [20.0])
        self.assertEqual(nd._word_percent_values("twenty-five per cent"), [25.0])
        self.assertEqual(nd._word_percent_values("one hundred percent"), [100.0])


class TestScopeChips(unittest.TestCase):
    def _elsewhere_claim(self):
        c, _ = scoped("23% of data-using articles reused data collected by others.")
        return c

    def test_weaker_chip_in_viewer_one(self):
        from modules.papertrail import viewer
        html = viewer._claim_card(self._elsewhere_claim(), {}, {}, {})
        self.assertIn("figure not in the quoted proof", html)
        self.assertNotIn("figure not in source", html)
        self.assertIn("SUPPORTED", html)

    def test_weaker_chip_in_viewer_two(self):
        from modules.papertrail import viewer_v2
        html = viewer_v2._card_v2(self._elsewhere_claim(), {}, {}, {})
        self.assertIn("figure not in the quoted proof", html)

    def test_no_chip_when_the_figure_is_in_the_proof(self):
        from modules.papertrail import viewer
        c, _ = scoped("43% of data-using articles reused data collected by others.")
        html = viewer._claim_card(c, {}, {}, {})
        self.assertNotIn("figure not in the quoted proof", html)


if __name__ == "__main__":
    unittest.main()


class TestDecimalAtSentenceEnd(unittest.TestCase):
    """Review 2026-09-04, second check: a ratio right before a full stop or a
    comma is a figure like any other, in the claim and in the source."""

    def test_claim_decimal_before_full_stop_is_extracted(self):
        figs = nd.extract_figures("The odds ratio was 0.72.")
        self.assertEqual([f["number"] for f in figs], ["0.72"])
        figs = nd.extract_figures("The odds ratio was 0.72, and it held.")
        self.assertEqual([f["number"] for f in figs], ["0.72"])

    def test_source_decimal_before_full_stop_is_found(self):
        c = {"text": "Smoking raised the hazard ratio to 1.42 overall.", "paper_ids": ["s"],
             "markers": ["s"], "verdict": "supported"}
        out = nd.check_claim_numbers(c, {"s": "Smoking raised the hazard ratio to 1.42."})
        self.assertEqual(out["missing"], [])

    def test_thousands_separator_still_not_a_decimal(self):
        figs = nd.extract_figures("The trial enrolled 29,615 people.")
        self.assertEqual([(f["kind"], f["number"]) for f in figs], [("count", "29,615")])

