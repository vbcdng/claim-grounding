"""Letter-spaced PDF garble (the anthropic2024/macaskill2025 class): PyPDF2
sometimes extracts a text layer one glyph per token, which poisons the sentence
index, the decomposed claims, and every downstream judge. read_source_pages now
detects that shape and falls back to poppler's pdftotext when available;
without poppler the old (garbled) text is returned unchanged, with a warning.
Offline only — no LLM, no network."""

import unittest
from unittest import mock

from modules.papertrail import source_decomposer as sd

GARBLE = ("M e a s u r i n g t h e P e r s u a s i v e n e s s o f "
          "L a n g u a g e M o d e l s " * 20)
CLEAN = ("Anthropic has developed a new evaluation method to measure the "
         "persuasiveness of language models. " * 20)


class LooksLetterSpaced(unittest.TestCase):
    def test_garble_detected(self):
        self.assertTrue(sd._looks_letter_spaced(GARBLE))

    def test_normal_text_passes(self):
        self.assertFalse(sd._looks_letter_spaced(CLEAN))

    def test_short_input_never_flags(self):
        # too little signal to judge — don't trigger the fallback on stubs
        self.assertFalse(sd._looks_letter_spaced("a b c d"))

    def test_spaceless_blob_not_flagged(self):
        # the OTHER PyPDF2 failure shape ("Wewillsoonlive...") is handled by the
        # segmentation guards, not this detector
        self.assertFalse(sd._looks_letter_spaced("Wewillsoonliveinaworld" * 100))


# Space-collapse: spaces dropped after short words (the mcnamara1987 class),
# mean token length ~8–15 while many spaces survive.
COLLAPSED = ("In69%ofthestudies thesubjects compensated fortheincreased cholesterol "
             "intake bydecreasing fractional absorption andor endogenous synthesis. " * 20)


class LooksSpaceCollapsed(unittest.TestCase):
    def test_collapsed_detected(self):
        self.assertTrue(sd._looks_space_collapsed(COLLAPSED))

    def test_normal_text_passes(self):
        self.assertFalse(sd._looks_space_collapsed(CLEAN))

    def test_technical_vocabulary_not_flagged(self):
        # long domain terms among normal prose must NOT trip it (montmorillonite,
        # vulnerabilities) — the false-positive class that killed a naive
        # long-token-ratio detector on the bentonite/darpa gate sources
        tech = ("The sorption of cesium on montmorillonite and illite was investigated "
                "with varying humic acid content to assess decontamination. " * 20)
        self.assertFalse(sd._looks_space_collapsed(tech))

    def test_near_total_collapse_excluded(self):
        # a handful of giant tokens per page (mean well past 20) is a DIFFERENT,
        # more severe failure that is not auto-swapped here (needs a fresh audit)
        self.assertFalse(sd._looks_space_collapsed("Wewillsoonliveinaworldwithout" * 100))

    def test_short_input_never_flags(self):
        self.assertFalse(sd._looks_space_collapsed("In69%ofthestudies thesubjects"))


# Localized glue: the page mostly reads fine (whole-doc mean stays normal, so
# _looks_space_collapsed passes) but one stretch collapses into a 25+-char run
# (the vincent2019 class: "tdescribedthedataacrossthefullspectrumofdietarycholesterol").
LOCAL_GLUE = (("The study described the data across the full spectrum of dietary "
               "cholesterol changes studied over the trial period. ") * 30
              + "results described tdescribedthedataacrossthefullspectrumofdietarycholesterol changes.")


class LooksLocallyGlued(unittest.TestCase):
    def test_localized_run_detected(self):
        self.assertTrue(sd._looks_locally_glued(LOCAL_GLUE))

    def test_whole_doc_detectors_miss_it(self):
        # the point of the new detector: mean-length & letter-spacing both pass
        self.assertFalse(sd._looks_space_collapsed(LOCAL_GLUE))
        self.assertFalse(sd._looks_letter_spaced(LOCAL_GLUE))

    def test_clean_text_has_no_glued_runs(self):
        self.assertFalse(sd._looks_locally_glued(CLEAN))

    def test_long_technical_word_not_a_run(self):
        # real words top out ~20 chars; "electroencephalographic" (~23) must not trip 25+
        self.assertFalse(sd._looks_locally_glued("The electroencephalographic pattern was normal. " * 30))


# Intra-word line-break garble (drouinchartier2020/t18): PyPDF2 splits a word
# across a wrapped line into a stray leading letter + remainder ("p articipants",
# "r esults"). Whole-doc detectors pass; the signal is standalone single-CONSONANT
# tokens (real single-letter words are only a/A/I/O).
LINEBREAK = (("Over up to 32 years of follow-up , 14 806 p articipants with incident "
              "cardiova scular disease were r esults identified in the three c ohorts. ") * 25)


class LooksLinebreakSplit(unittest.TestCase):
    def test_linebreak_garble_detected(self):
        self.assertTrue(sd._looks_linebreak_split(LINEBREAK))

    def test_clean_text_not_flagged(self):
        self.assertFalse(sd._looks_linebreak_split(CLEAN))

    def test_single_vowel_words_dont_trip(self):
        # "a" and "I" are legitimate single-letter words; only consonants count
        self.assertFalse(sd._looks_linebreak_split("I saw a cat and a dog. " * 60))


class AbbreviationMerge(unittest.TestCase):
    def test_et_al_not_a_sentence_boundary(self):
        # the t8/t11 fragment class: punkt breaks after "al."
        out = sd.sentence_split("Studies by Clarkson et al. (85) on the regression of "
                                "atherosclerosis demonstrated a clear effect.")
        self.assertEqual(len(out), 1)
        self.assertIn("regression", out[0])

    def test_author_bracket_ref_stays_attached(self):
        out = sd.sentence_split("Berger et al. [29] examined the serum lipid responses "
                                "across 19 trials.")
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("Berger"))

    def test_real_etc_sentence_end_not_merged(self):
        # next piece starts with a capital → genuine boundary, must NOT merge
        out = sd.sentence_split("We measured LDL, HDL, etc. The next sentence is separate.")
        self.assertEqual(len(out), 2)

    def test_eg_and_ie_merge_when_continued(self):
        out = sd.sentence_split("Several markers (e.g. serum lipids) were tracked over time.")
        self.assertEqual(len(out), 1)


class GarbledCacheRedecomposed(unittest.TestCase):
    """A cache written before the pdftotext fallback holds claims extracted
    from garble; sentence-only schema upgrades can't fix those — the cache hit
    must fall through to a full re-decomposition."""

    def _run(self, cached_claims, tmp):
        import json, os
        path = os.path.join(tmp, "src.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(CLEAN)
        pid = "p" * 40
        cache_path = os.path.join(tmp, f"{pid}.json")
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"paper_id": pid, "file_hash": sd.file_hash(path),
                       "schema": sd.CACHE_SCHEMA,
                       "sentences": [{"text": "x", "page": 1}],
                       "claims": cached_claims}, f)
        llm = mock.MagicMock()
        llm.call_json.return_value = ["A clean extracted claim about persuasion."]
        out = sd.decompose_source(path, pid, "k", tmp, llm)
        return out, llm

    def test_garbled_cached_claims_force_redecomposition(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            garbled = [{"id": "c0", "text": GARBLE, "evidence": []}]
            out, llm = self._run(garbled, tmp)
            llm.call_json.assert_called()          # LLM re-extraction happened
            self.assertNotIn("M e a s", out["claims"][0]["text"])

    def test_clean_cached_claims_stay_cached(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            clean = [{"id": "c0", "text": CLEAN, "evidence": []}]
            out, llm = self._run(clean, tmp)
            llm.call_json.assert_not_called()      # cache hit, zero LLM
            self.assertEqual(out["claims"][0]["text"], CLEAN)


class PdftotextPages(unittest.TestCase):
    def test_missing_binary_returns_none(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertIsNone(sd._pdftotext_pages("x.pdf"))

    def test_pages_split_on_formfeed_trailing_dropped(self):
        proc = mock.Mock(returncode=0, stdout="Page one.\fPage two.\f".encode())
        with mock.patch("shutil.which", return_value="/usr/bin/pdftotext"), \
             mock.patch("subprocess.run", return_value=proc):
            self.assertEqual(sd._pdftotext_pages("x.pdf"), ["Page one.", "Page two."])

    def test_failure_returns_none(self):
        proc = mock.Mock(returncode=1, stdout=b"")
        with mock.patch("shutil.which", return_value="/usr/bin/pdftotext"), \
             mock.patch("subprocess.run", return_value=proc):
            self.assertIsNone(sd._pdftotext_pages("x.pdf"))


class ReadSourcePagesFallback(unittest.TestCase):
    def _reader_with(self, page_texts):
        pages = [mock.Mock(extract_text=mock.Mock(return_value=t)) for t in page_texts]
        return mock.Mock(pages=pages)

    def test_garbled_pdf_uses_pdftotext(self):
        with mock.patch("PyPDF2.PdfReader", return_value=self._reader_with([GARBLE])), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"")), \
             mock.patch.object(sd, "_pdftotext_pages", return_value=[CLEAN]):
            self.assertEqual(sd.read_source_pages("x.pdf"), [CLEAN])

    def test_garbled_pdf_without_poppler_keeps_old_behavior(self):
        with mock.patch("PyPDF2.PdfReader", return_value=self._reader_with([GARBLE])), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"")), \
             mock.patch.object(sd, "_pdftotext_pages", return_value=None):
            self.assertEqual(sd.read_source_pages("x.pdf"), [GARBLE])

    def test_clean_pdf_never_calls_fallback(self):
        with mock.patch("PyPDF2.PdfReader", return_value=self._reader_with([CLEAN])), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"")), \
             mock.patch.object(sd, "_pdftotext_pages") as fb:
            self.assertEqual(sd.read_source_pages("x.pdf"), [CLEAN])
            fb.assert_not_called()

    def test_fallback_still_garbled_keeps_pypdf2_text(self):
        # pdftotext can also fail to de-garble (image-layer OCR PDFs) — keep the
        # original rather than swap one garble for another
        other_garble = "X y z w " * 200
        with mock.patch("PyPDF2.PdfReader", return_value=self._reader_with([GARBLE])), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"")), \
             mock.patch.object(sd, "_pdftotext_pages", return_value=[other_garble]):
            self.assertEqual(sd.read_source_pages("x.pdf"), [GARBLE])

    def test_localized_glue_swaps_when_fallback_is_cleaner(self):
        # whole-doc detectors pass, but a 25+-char run triggers the swap — and
        # only because pdftotext removes the glued run
        with mock.patch("PyPDF2.PdfReader", return_value=self._reader_with([LOCAL_GLUE])), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"")), \
             mock.patch.object(sd, "_pdftotext_pages", return_value=[CLEAN]):
            self.assertEqual(sd.read_source_pages("x.pdf"), [CLEAN])

    def test_localized_glue_kept_when_fallback_not_cleaner(self):
        # if pdftotext still has the glued run (no improvement), don't swap —
        # never trade PyPDF2 for a differently-broken reflow
        with mock.patch("PyPDF2.PdfReader", return_value=self._reader_with([LOCAL_GLUE])), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"")), \
             mock.patch.object(sd, "_pdftotext_pages", return_value=[LOCAL_GLUE]):
            self.assertEqual(sd.read_source_pages("x.pdf"), [LOCAL_GLUE])
