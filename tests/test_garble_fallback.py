"""Letter-spaced PDF garble (the anthropic2024/macaskill2025 class): the PDF reader
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
        # the OTHER reader failure shape ("Wewillsoonlive...") is handled by the
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


# Intra-word line-break garble (drouinchartier2020/t18): the reader splits a word
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
        with mock.patch("pypdf.PdfReader", return_value=self._reader_with([GARBLE])), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"")), \
             mock.patch.object(sd, "_pdftotext_pages", return_value=[CLEAN]):
            self.assertEqual(sd.read_source_pages("x.pdf"), [CLEAN])

    def test_garbled_pdf_without_poppler_keeps_old_behavior(self):
        with mock.patch("pypdf.PdfReader", return_value=self._reader_with([GARBLE])), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"")), \
             mock.patch.object(sd, "_pdftotext_pages", return_value=None):
            self.assertEqual(sd.read_source_pages("x.pdf"), [GARBLE])

    def test_clean_pdf_never_calls_fallback(self):
        with mock.patch("pypdf.PdfReader", return_value=self._reader_with([CLEAN])), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"")), \
             mock.patch.object(sd, "_pdftotext_pages") as fb:
            self.assertEqual(sd.read_source_pages("x.pdf"), [CLEAN])
            fb.assert_not_called()

    def test_fallback_still_garbled_keeps_reader_text(self):
        # pdftotext can also fail to de-garble (image-layer OCR PDFs) — keep the
        # original rather than swap one garble for another
        other_garble = "X y z w " * 200
        with mock.patch("pypdf.PdfReader", return_value=self._reader_with([GARBLE])), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"")), \
             mock.patch.object(sd, "_pdftotext_pages", return_value=[other_garble]):
            self.assertEqual(sd.read_source_pages("x.pdf"), [GARBLE])

    def test_localized_glue_swaps_when_fallback_is_cleaner(self):
        # whole-doc detectors pass, but a 25+-char run triggers the swap — and
        # only because pdftotext removes the glued run.
        # The fallback text is sized to match LOCAL_GLUE: this test is about the
        # glue-reduction condition, and since task #71 a fallback that has lost
        # most of the letters is refused on its own (see FallbackTextLossGuard),
        # so a much shorter stand-in would fail for an unrelated reason.
        clean_same_size = CLEAN * 2
        with mock.patch("pypdf.PdfReader", return_value=self._reader_with([LOCAL_GLUE])), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"")), \
             mock.patch.object(sd, "_pdftotext_pages", return_value=[clean_same_size]):
            self.assertEqual(sd.read_source_pages("x.pdf"), [clean_same_size])

    def test_localized_glue_kept_when_fallback_not_cleaner(self):
        # if pdftotext still has the glued run (no improvement), don't swap —
        # never trade the reader text for a differently-broken reflow
        with mock.patch("pypdf.PdfReader", return_value=self._reader_with([LOCAL_GLUE])), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"")), \
             mock.patch.object(sd, "_pdftotext_pages", return_value=[LOCAL_GLUE]):
            self.assertEqual(sd.read_source_pages("x.pdf"), [LOCAL_GLUE])


class SourceReaderId(unittest.TestCase):
    """Task #71: the reader identity recorded in analysis.json metadata."""

    def test_names_the_library_and_version(self):
        got = sd.source_reader_id()
        self.assertTrue(got.startswith("pypdf "), got)
        # a version, not a placeholder — the reuse guard compares this string
        self.assertRegex(got, r"^pypdf \d+\.\d+")

    def test_falls_back_to_the_bare_name(self):
        with mock.patch.dict("sys.modules", {"pypdf": None}):
            self.assertEqual(sd.source_reader_id(), "pypdf")


class FallbackTextLossGuard(unittest.TestCase):
    """Task #71: the pdftotext fallback must not swap in a truncated document.

    Found on data/loop_rounds/round_5/app/sources/unodc2023.pdf, a 162-page
    report: its text trips the space-collapse detector, and the pdftotext the
    tool swapped in held 19% of the letters (108,465 of 573,914). The garble
    detectors cannot see this — they measure token shapes, never how much of the
    document survived — so a separate check is needed. Whitespace is excluded
    because letter-spaced garble inflates the raw count with spaces alone."""

    def _reader_with(self, page_texts):
        pages = [mock.Mock(extract_text=mock.Mock(return_value=t)) for t in page_texts]
        return mock.Mock(pages=pages)

    def test_truncated_fallback_is_refused(self):
        # GARBLE fires a detector; the fallback is clean but holds a fraction
        truncated = CLEAN[:len(CLEAN) // 10]
        with mock.patch("pypdf.PdfReader", return_value=self._reader_with([GARBLE])), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"")), \
             mock.patch.object(sd, "_pdftotext_pages", return_value=[truncated]):
            self.assertEqual(sd.read_source_pages("x.pdf"), [GARBLE])

    def test_truncated_fallback_says_why(self):
        truncated = CLEAN[:len(CLEAN) // 10]
        with mock.patch("pypdf.PdfReader", return_value=self._reader_with([GARBLE])), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"")), \
             mock.patch.object(sd, "_pdftotext_pages", return_value=[truncated]), \
             self.assertLogs(sd.logger, level="WARNING") as cm:
            sd.read_source_pages("x.pdf")
        self.assertTrue(any("lost most of the document" in m for m in cm.output),
                        cm.output)

    def test_letter_spaced_swap_still_happens_despite_a_shorter_count(self):
        # the real anthropic2024 case: de-garbling removes SPACES, so the raw
        # character count falls while the letters are all still there. This is
        # exactly the swap the guard must not block.
        spaced = " ".join("the quick brown fox jumps over the lazy dog " * 40)
        degarbled = spaced.replace(" ", "")
        self.assertLess(len(degarbled), len(spaced) * 0.6)          # raw count collapses
        self.assertEqual(sd._nonspace_len(degarbled), sd._nonspace_len(spaced))
        with mock.patch("pypdf.PdfReader", return_value=self._reader_with([spaced])), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"")), \
             mock.patch.object(sd, "_pdftotext_pages", return_value=[CLEAN]):
            self.assertEqual(sd.read_source_pages("x.pdf"), [CLEAN])

    def test_nonspace_len_ignores_whitespace_only(self):
        self.assertEqual(sd._nonspace_len("a b\tc\nd"), 4)
        self.assertEqual(sd._nonspace_len("   \n\t "), 0)
        self.assertEqual(sd._nonspace_len(""), 0)


# A ciphered text layer, shaped like the real one. A broken embedded font maps
# every letter to another byte and the inter-word space to a control byte, so
# word LENGTHS survive intact and all four garble detectors above stay silent on
# the characters themselves. Pairs of words share a control byte here because
# that is what the real file does — which is what makes the mean token length
# 8-20 letters and fires _looks_space_collapsed, the only reason the fallback is
# attempted at all. Plain-shifted text with ordinary spaces fires nothing and is
# therefore still not repaired; see ARCHITECTURE.md for that known limit.
def _ciphered(n_words: int = 400) -> str:
    words = ("Anthropic has developed a new evaluation method to measure the "
             "persuasiveness of language models".split() * 40)[:n_words]
    shift = lambda w: "".join(chr(ord(c) + 3) for c in w)
    return " ".join(shift(words[i]) + "\x03" + shift(words[i + 1])
                    for i in range(0, len(words) - 1, 2))


class ReadabilityBeatsLength(unittest.TestCase):
    """Task #71 follow-up: the text-loss guard must not protect UNREADABLE text.

    The guard above refuses a pdftotext fallback that holds too few of the
    letters, which is right when the letters are real. It is wrong when they are
    not: on data/loop_rounds/round_5/app/sources/unodc2023.pdf the pypdf read is
    a shift-3 cipher with a control byte for the space, and the guard kept
    609,542 characters of it (16.1% control characters) in preference to
    pdftotext's 137,330 characters of clean prose (0.0%). Length cannot make up
    for characters that are wrong, so when the primary read is unreadable and the
    fallback is readable, readability decides regardless of the ratio."""

    def _reader_with(self, page_texts):
        pages = [mock.Mock(extract_text=mock.Mock(return_value=t)) for t in page_texts]
        return mock.Mock(pages=pages)

    def test_the_fixture_reproduces_the_real_shape(self):
        cipher = _ciphered()
        # unreadable by the control-char measure, and invisible to the detectors
        # that look at letters rather than at bytes
        self.assertGreaterEqual(sd._control_char_rate(cipher), sd.GARBLED_CONTROL_RATE)
        self.assertFalse(sd._looks_letter_spaced(cipher))
        self.assertEqual(sd._count_glued_runs(cipher), 0)
        # ...but the glued token lengths do fire the space-collapse test, which is
        # what makes the tool try pdftotext in the first place
        self.assertTrue(sd._looks_space_collapsed(cipher))
        self.assertLess(sd._control_char_rate(CLEAN), sd.GARBLED_CONTROL_RATE)

    def test_shorter_readable_prose_replaces_a_ciphered_read(self):
        cipher = _ciphered()
        short = CLEAN[:len(CLEAN) // 5]          # ~19% of the letters, as in the real file
        self.assertLess(sd._nonspace_len(short),
                        sd._nonspace_len(cipher) * sd._FALLBACK_MIN_TEXT_RATIO)
        with mock.patch("pypdf.PdfReader", return_value=self._reader_with([cipher])), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"")), \
             mock.patch.object(sd, "_pdftotext_pages", return_value=[short]):
            self.assertEqual(sd.read_source_pages("x.pdf"), [short])

    def test_it_says_why_it_accepted_the_loss(self):
        cipher = _ciphered()
        short = CLEAN[:len(CLEAN) // 5]
        with mock.patch("pypdf.PdfReader", return_value=self._reader_with([cipher])), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"")), \
             mock.patch.object(sd, "_pdftotext_pages", return_value=[short]), \
             self.assertLogs(sd.logger, level="WARNING") as cm:
            sd.read_source_pages("x.pdf")
        self.assertTrue(any("not readable language" in m for m in cm.output), cm.output)
        # and it must NOT also claim it kept the original
        self.assertFalse(any("KEEPING the original read" in m for m in cm.output), cm.output)

    def test_an_unreadable_fallback_never_replaces_an_unreadable_read(self):
        # swapping one cipher for a shorter cipher gains nothing and loses text
        cipher = _ciphered()
        short_cipher = _ciphered(60)
        with mock.patch("pypdf.PdfReader", return_value=self._reader_with([cipher])), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"")), \
             mock.patch.object(sd, "_pdftotext_pages", return_value=[short_cipher]):
            self.assertEqual(sd.read_source_pages("x.pdf"), [cipher])

    def test_a_readable_primary_read_keeps_its_length_protection(self):
        # the unodc guard itself must survive: GARBLE has no control characters,
        # so a truncated fallback is still refused exactly as before
        truncated = CLEAN[:len(CLEAN) // 10]
        self.assertLess(sd._control_char_rate(GARBLE), sd.GARBLED_CONTROL_RATE)
        with mock.patch("pypdf.PdfReader", return_value=self._reader_with([GARBLE])), \
             mock.patch("builtins.open", mock.mock_open(read_data=b"")), \
             mock.patch.object(sd, "_pdftotext_pages", return_value=[truncated]):
            self.assertEqual(sd.read_source_pages("x.pdf"), [GARBLE])
