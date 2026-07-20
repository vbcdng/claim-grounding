"""Generic-paper importer (import_paper.py / paper_importer.py) — fully offline:
every network callable is injected via fetchers=..., no requests patching needed.

Run:  venv/bin/python3 -m unittest tests.test_paper_importer
"""
import os
import sys
import json
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import paper_importer as pi
from modules.papertrail import text_decomposer as td
from modules.papertrail.paper_importer import (AuthorYearRecognizer, PaperImportError,
                                               build_bibliography, identify, make_key,
                                               trim_body)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _db_refs():
    """A neighbors()-shaped reference list (the DATABASE bibliography)."""
    return [
        {"paper_id": "s2a", "title": "Sleep and memory consolidation",
         "year": 2020, "authors": ["Anna Smith", "Bob Lee"],
         "doi": "10.1/smith", "arxiv_id": None, "url": "https://doi.org/10.1/smith",
         "source": "s2"},
        {"paper_id": "s2b", "title": "Economic effects of schooling",
         "year": 2019, "authors": ["Carl Jones"],
         "doi": "10.1/jones", "arxiv_id": None, "url": "https://doi.org/10.1/jones",
         "source": "s2"},
        {"paper_id": "s2c", "title": "A theory of everything",
         "year": 2021, "authors": ["Dana de Vries"],
         "doi": None, "arxiv_id": "2101.00001", "url": "https://arxiv.org/abs/2101.00001",
         "source": "s2"},
        # surname+year twin of the first entry -> (smith, 2020) is ambiguous
        {"paper_id": "s2d", "title": "Sleep loss in adolescents",
         "year": 2020, "authors": ["Eve Smith"],
         "doi": "10.1/smith2", "arxiv_id": None, "url": None, "source": "s2"},
        # no title, no DOI -> dropped
        {"paper_id": "s2e", "title": None, "year": 1999, "authors": ["X Y"],
         "doi": None, "arxiv_id": None, "url": None, "source": "s2"},
    ]


def _fetchers(pages=None, s2_paper=None, refs=None, find=None, crossref=None):
    """Injectable fetcher set; every default is a benign stub."""
    return {
        "pages_reader": lambda path: pages if pages is not None else ["page one text"],
        "s2_get": lambda pid: s2_paper,
        "s2_find": find or (lambda t, year=None: (None, "no_match")),
        "neighbors": lambda pid, direction, cache_dir=None: (
            refs if refs is not None else _db_refs()),
        "crossref_refs": lambda doi: crossref,
        "fetch_target_pdf": lambda record, out: None,
    }


_S2_PAPER = {"paperId": "abc123", "title": "The target paper", "year": 2022,
             "externalIds": {"DOI": "10.9/target", "PubMedCentral": "PMC42"},
             "openAccessPdf": {"url": "https://oa.example/target.pdf"}}


# ---------------------------------------------------------------------------
# Ladder A — identification
# ---------------------------------------------------------------------------

class TestIdentify(unittest.TestCase):

    def test_explicit_doi_enriched_from_s2(self):
        r = identify(doi="10.9/target", fetchers=_fetchers(s2_paper=_S2_PAPER))
        self.assertEqual(r["paper_id"], "DOI:10.9/target")
        self.assertEqual(r["pmc_id"], "PMC42")
        self.assertEqual(r["oa_pdf_url"], "https://oa.example/target.pdf")
        self.assertEqual(r["id_evidence"], "explicit --doi")

    def test_doi_survives_s2_being_down(self):
        r = identify(doi="10.9/target", fetchers=_fetchers(s2_paper=None))
        self.assertEqual(r["paper_id"], "DOI:10.9/target")
        self.assertIsNone(r["s2_paper_id"])

    def test_url_yields_doi(self):
        r = identify(url="https://doi.org/10.9/target",
                     fetchers=_fetchers(s2_paper=None))
        self.assertEqual(r["doi"], "10.9/target")

    def test_unrecognizable_url_is_a_hard_stop(self):
        with self.assertRaises(PaperImportError):
            identify(url="https://example.com/blog/post", fetchers=_fetchers())

    def test_pdf_printed_doi_wins(self):
        # note: the regex requires a real DOI prefix (4+ registrant digits)
        pages = ["Journal of Things 12(3)\nhttps://doi.org/10.9999/target\nThe Title"]
        r = identify(pdf="x.pdf", fetchers=_fetchers(pages=pages, s2_paper=_S2_PAPER))
        self.assertEqual(r["doi"], "10.9999/target")
        self.assertIn("printed on the PDF", r["id_evidence"])

    def test_pdf_arxiv_stamp(self):
        pages = ["arXiv:2101.00001v2  [econ.GN]  1 Jan 2021\nA Great Title Here"]
        r = identify(pdf="x.pdf", fetchers=_fetchers(pages=pages, s2_paper=None))
        self.assertEqual(r["paper_id"], "ARXIV:2101.00001")

    def test_title_gate_refuses_without_confident_match(self):
        pages = ["Some Extracted Title Line That Is Long Enough\nAuthor Name"]
        with self.assertRaises(PaperImportError):
            identify(pdf="x.pdf", fetchers=_fetchers(pages=pages))

    def test_title_match_resolves(self):
        f = _fetchers(pages=["An Interesting Paper About Sleep And Memory\nBy A. Person"],
                      find=lambda t, year=None: (_S2_PAPER, "matched"))
        r = identify(pdf="x.pdf", fetchers=f)
        self.assertEqual(r["doi"], "10.9/target")
        self.assertIn("title match", r["id_evidence"])

    def test_unreadable_pdf_is_a_hard_stop(self):
        with self.assertRaises(PaperImportError):
            identify(pdf="x.pdf", fetchers=_fetchers(pages=[]))


# ---------------------------------------------------------------------------
# Ladder B — bibliography + keys
# ---------------------------------------------------------------------------

class TestBibliography(unittest.TestCase):

    def test_keys_are_surname_year_slugs_in_the_marker_charset(self):
        bib, _ = build_bibliography(_db_refs())
        self.assertIn("smith2020", bib)
        self.assertIn("jones2019", bib)
        self.assertIn("vries2021", bib)         # 'Dana de Vries' -> last token
        for k in bib:
            self.assertRegex(k, r"^[A-Za-z0-9_-]+$")

    def test_collision_gets_suffix(self):
        bib, _ = build_bibliography(_db_refs())
        self.assertIn("smith2020_2", bib)       # the Eve Smith 2020 twin

    def test_untitled_undoied_refs_are_dropped(self):
        bib, _ = build_bibliography(_db_refs())
        self.assertEqual(len(bib), 4)           # 5 in, 1 dropped

    def test_entries_carry_rich_ids_for_the_downloader(self):
        bib, _ = build_bibliography(_db_refs())
        self.assertEqual(bib["smith2020"]["s2_paper_id"], "s2a")
        self.assertEqual(bib["vries2021"]["arxiv_id"], "2101.00001")

    def test_make_key_without_authors_or_year(self):
        self.assertEqual(make_key({"authors": [], "year": None}, {}), "ref")

    def test_ay_index_maps_ambiguity(self):
        _, idx = build_bibliography(_db_refs())
        self.assertEqual(idx.resolve("Smith", "2020"), (None, "ambiguous"))
        self.assertEqual(idx.resolve("Jones", "2019"), ("jones2019", "ok"))

    def test_any_author_fallback_handles_flipped_author_order(self):
        # DB record led by Lee; the text cites "(Smith and Lee, 2022)" wait —
        # use a co-author surname the first-author index can't see.
        _, idx = build_bibliography(_db_refs())
        # 'Bob Lee' is the SECOND author of smith2020 (Anna Smith, Bob Lee)
        self.assertEqual(idx.resolve("Lee", "2020"), ("smith2020", "ok"))

    def test_first_author_match_beats_the_any_author_fallback(self):
        # A first-author Lee entry exists -> "(Lee, 2020)" cites THAT work, not
        # the one where Lee is a co-author (citations name first authors).
        refs = _db_refs() + [{"paper_id": "x", "title": "Another Lee work",
                              "year": 2020, "authors": ["Ann Lee"],
                              "doi": "10.1/lee", "arxiv_id": None, "url": None,
                              "source": "s2"}]
        _, idx = build_bibliography(refs)
        self.assertEqual(idx.resolve("Lee", "2020"), ("lee2020", "ok"))

    def test_hyphenated_and_accented_surnames_fold_to_the_same_slug(self):
        # pdftotext de-hyphenates names at line breaks: 'Núñez-Peña' in the DB,
        # 'NúñezPeña' in the extracted text — both must resolve (live psych
        # paper finding, 2026-07-07).
        refs = [{"paper_id": "x", "title": "Math anxiety measures", "year": 2016,
                 "authors": ["M. Isabel Núñez-Peña"], "doi": "10.1/np",
                 "arxiv_id": None, "url": None, "source": "s2"}]
        _, idx = build_bibliography(refs)
        self.assertEqual(idx.resolve("NúñezPeña", "2016")[1], "ok")
        self.assertEqual(idx.resolve("Núñez-Peña", "2016")[1], "ok")

    def test_any_author_fallback_still_refuses_ambiguity(self):
        # Lee is a CO-author of two 2020 works and first author of none ->
        # "(Lee, 2020)" must refuse.
        refs = _db_refs() + [{"paper_id": "x", "title": "Another co-authored work",
                              "year": 2020, "authors": ["Zed Adams", "Bob Lee"],
                              "doi": "10.1/adams", "arxiv_id": None, "url": None,
                              "source": "s2"}]
        _, idx = build_bibliography(refs)
        self.assertEqual(idx.resolve("Lee", "2020"), (None, "ambiguous"))


# ---------------------------------------------------------------------------
# Author-year recognition
# ---------------------------------------------------------------------------

class TestAuthorYearRecognizer(unittest.TestCase):

    def setUp(self):
        _, self.idx = build_bibliography(_db_refs())
        self.rec = AuthorYearRecognizer(self.idx)

    def test_parenthetical_single(self):
        cits, unres = self.rec.scan("Sleep matters (Jones, 2019).")
        self.assertEqual(len(cits), 1)
        self.assertEqual(cits[0].keys, ["jones2019"])
        self.assertEqual(unres, [])

    def test_parenthetical_multi_semicolon(self):
        cits, _ = self.rec.scan("Known effects (Jones, 2019; de Vries, 2021).")
        self.assertEqual(cits[0].keys, ["jones2019", "vries2021"])

    def test_narrative_span_is_only_the_year_paren(self):
        text = "Jones (2019) showed that schooling pays."
        cits, _ = self.rec.scan(text)
        self.assertEqual(len(cits), 1)
        self.assertEqual(text[cits[0].start:cits[0].end], "(2019)")
        self.assertEqual(cits[0].keys, ["jones2019"])

    def test_narrative_et_al(self):
        cits, _ = self.rec.scan("Smith et al. (2020) is ambiguous here.")
        # (smith, 2020) has two bibliography entries -> unresolved, no citation
        self.assertEqual(cits, [])

    def test_ambiguous_surname_year_is_reported_not_guessed(self):
        cits, unres = self.rec.scan("Sleep is key (Smith, 2020).")
        self.assertEqual(cits, [])
        self.assertEqual(unres[0]["why"], "ambiguous")

    def test_unknown_citation_reported(self):
        cits, unres = self.rec.scan("As shown (Nowak, 2018).")
        self.assertEqual(cits, [])
        self.assertEqual(unres[0]["why"], "not_in_bibliography")

    def test_partial_multi_cite_paren_is_all_or_nothing(self):
        cits, unres = self.rec.scan("Effects (Jones, 2019; Nowak, 2018).")
        self.assertEqual(cits, [])
        self.assertEqual(len(unres), 1)

    def test_year_only_paren_is_not_a_citation(self):
        cits, unres = self.rec.scan("The law passed (since 2020) and held.")
        self.assertEqual(cits, [])
        self.assertEqual(unres, [])

    def test_locator_after_year(self):
        cits, _ = self.rec.scan("Quoted (Jones, 2019, p. 12).")
        self.assertEqual(cits[0].keys, ["jones2019"])

    def test_multi_year_segment_cites_one_work_per_year(self):
        cits, unres = self.rec.scan("Search theory (de Vries, 2021, 2019).")
        # 2021 resolves to vries2021; 2019 has no de Vries entry -> whole
        # paren unresolved (all-or-nothing), both attempts recorded.
        self.assertEqual(cits, [])
        self.assertEqual(len(unres), 1)
        cits2, _ = self.rec.scan("One work only (de Vries, 2021).")
        self.assertEqual(cits2[0].keys, ["vries2021"])

    def test_convert_block_places_marker_after_sentence_end(self):
        # INPUT_FORMAT convention: the marker follows the sentence-ending
        # punctuation — "…shedding. [[smith2020]]".
        from modules.papertrail.claude_research_importer import convert_block
        conv, keys = convert_block(
            "Schooling raises wages (Jones, 2019). More text.", self.rec)
        self.assertIn("wages. [[jones2019]] More text.", conv)
        self.assertEqual(keys, ["jones2019"])


# ---------------------------------------------------------------------------
# Numeric recognition + two-witness alignment (v1b)
# ---------------------------------------------------------------------------

def _crossref_refs():
    """Crossref deposited order: 1=jones, 2=smith(Anna), 3=devries."""
    return [
        {"position": 1, "publisher_key": "bib1", "key_number": 1,
         "doi": "10.1/jones", "year": "2019", "author": "Jones", "title": None,
         "raw": None},
        {"position": 2, "publisher_key": "bib2", "key_number": 2,
         "doi": "10.1/smith", "year": "2020", "author": "Smith", "title": None,
         "raw": None},
        {"position": 3, "publisher_key": "bib3", "key_number": 3,
         "doi": None, "year": "2021", "author": "de Vries",
         "title": "A theory of everything", "raw": None},
    ]


_REF_TAIL = """[1] Jones, C. (2019). Economic effects of schooling. J. Econ. 12, 1-20.
[2] Smith, A. and Lee, B. (2020). Sleep and memory consolidation. Nature.
[3] de Vries, D. (2021). A theory of everything. arXiv preprint."""


class TestNumericAlignment(unittest.TestCase):

    def setUp(self):
        self.bib, _ = build_bibliography(_db_refs())

    def test_both_witnesses_agree(self):
        idx, table = pi.align_numeric(self.bib, _crossref_refs(), _REF_TAIL)
        self.assertEqual(idx[1], "jones2019")
        self.assertEqual(idx[3], "vries2021")
        self.assertEqual(table[0]["witnesses"], "crossref+pdf")

    def test_uncorroborated_crossref_only_is_left_unmapped(self):
        # No PDF tail at all -> zero two-witness agreements -> the crossref
        # order is unvalidated, its rows must NOT mint mappings (round-1
        # import audit: RSOS deposited order != printed numbering produced
        # the only wrong markers).
        idx, table = pi.align_numeric(self.bib, _crossref_refs(), "")
        self.assertEqual(idx, {})
        self.assertTrue(all(t["witnesses"] == "crossref-only-uncorroborated"
                            for t in table))
        self.assertTrue(all(t["key"] is None for t in table))
        self.assertEqual(table[0]["candidate"], "jones2019")

    def test_uncorroborated_pdf_only_is_left_unmapped(self):
        idx, table = pi.align_numeric(self.bib, None, _REF_TAIL)
        self.assertEqual(idx, {})
        self.assertTrue(all(t["witnesses"] == "pdf-only-uncorroborated"
                            for t in table))

    def test_corroborated_single_witness_is_aligned(self):
        # Ref tail covers 1-3 and agrees with crossref 3 times (= the
        # MIN_SINGLE_WITNESS_CORROBORATION threshold) -> the crossref-only
        # row for index 4 is trusted.
        cr = _crossref_refs() + [
            {"position": 4, "publisher_key": "bib4", "key_number": 4,
             "doi": "10.1/smith2", "year": "2020", "author": "Smith",
             "title": "Sleep loss in adolescents", "raw": None}]
        idx, table = pi.align_numeric(self.bib, cr, _REF_TAIL)
        self.assertEqual(idx[1], "jones2019")
        self.assertEqual(idx[4], "smith2020_2")
        row4 = [t for t in table if t["index"] == 4][0]
        self.assertEqual(row4["witnesses"], "crossref-only")

    def test_disagreement_unmaps_the_index(self):
        # Crossref says 1=smith, PDF says 1=jones -> conflict, no mapping.
        cr = _crossref_refs()
        cr[0]["doi"] = "10.1/smith"
        cr[0]["author"], cr[0]["year"] = "Smith", "2020"
        idx, table = pi.align_numeric(self.bib, cr[:1], _REF_TAIL.splitlines()[0])
        self.assertNotIn(1, idx)
        self.assertEqual(table[0]["witnesses"], "DISAGREE")

    def test_blob_matching_never_guesses_between_twins(self):
        # A blob naming only "Smith (2020)" matches BOTH smith2020 entries ->
        # scores tie -> None.
        blob = "Smith, X. (2020). Some other work entirely."
        self.assertIsNone(pi._match_blob_to_bib(blob, self.bib))


class TestNumericBracketRecognizer(unittest.TestCase):

    def setUp(self):
        bib, _ = build_bibliography(_db_refs())
        self.idx, _ = pi.align_numeric(bib, _crossref_refs(), _REF_TAIL)
        self.rec = pi.NumericBracketRecognizer(self.idx)

    def test_single_and_comma_list(self):
        cits, unres = self.rec.scan("Shown in [1] and later [1,3].")
        self.assertEqual([c.keys for c in cits],
                         [["jones2019"], ["jones2019", "vries2021"]])
        self.assertEqual(unres, [])

    def test_range_expansion(self):
        cits, _ = self.rec.scan("Reviewed in [1-3].")
        self.assertEqual(cits[0].keys, ["jones2019", "smith2020", "vries2021"])

    def test_unaligned_number_is_all_or_nothing(self):
        cits, unres = self.rec.scan("See [1,9].")
        self.assertEqual(cits, [])
        self.assertEqual(unres[0]["why"], "unaligned_index")
        self.assertEqual(unres[0]["detail"], [9])

    def test_non_citation_brackets_ignored(self):
        cits, unres = self.rec.scan("Matrix [A] and equation [x+1] and [2019].")
        self.assertEqual(cits, [])          # [2019] is 4 digits -> no match
        self.assertEqual(unres, [])

    def test_convert_block_integration(self):
        from modules.papertrail.claude_research_importer import convert_block
        conv, keys = convert_block("Schooling raises wages [1]. More.", self.rec)
        self.assertIn("wages. [[jones2019]] More.", conv)


# ---------------------------------------------------------------------------
# Body trimming
# ---------------------------------------------------------------------------

_PAPER_TEXT = """Journal of Examples 12(3) 2024
The Target Paper Title
A. Author and B. Author
Abstract
This paper restates everything briefly.
Keywords: things, stuff
1. Introduction
Schooling raises wages (Jones, 2019). It matters.

Figure 1: A diagram of the model.
2. Results
Sleep helps memory too (de Vries, 2021). Strong effects.
Acknowledgments
We thank everyone.
References
Jones, C. (2019). Economic effects of schooling. J. Econ.
de Vries, D. (2021). A theory of everything. arXiv."""


class TestTrimBody(unittest.TestCase):

    def test_strips_frontmatter_refs_backmatter_and_captions(self):
        body, ref_tail, stripped = trim_body(_PAPER_TEXT)
        self.assertIn("1. Introduction", body)
        self.assertIn("Schooling raises wages", body)
        self.assertNotIn("Abstract", body)
        self.assertNotIn("A. Author", body)
        self.assertNotIn("Figure 1:", body)
        self.assertNotIn("We thank everyone", body)
        self.assertNotIn("J. Econ.", body)
        self.assertIn("Economic effects of schooling", ref_tail)
        self.assertTrue(any("references" in s for s in stripped))
        self.assertTrue(any("front matter" in s for s in stripped))

    def test_keep_abstract(self):
        body, _, _ = trim_body(_PAPER_TEXT, keep_abstract=True)
        self.assertIn("restates everything", body)
        self.assertNotIn("Journal of Examples", body)   # header still stripped

    def test_no_headings_at_all_passes_through(self):
        body, ref_tail, stripped = trim_body("Just prose.\nMore prose.")
        self.assertIn("Just prose.", body)
        self.assertEqual(ref_tail, "")


# ---------------------------------------------------------------------------
# End-to-end (offline) — artifacts parse through the real consumer
# ---------------------------------------------------------------------------

class TestRunPaperImport(unittest.TestCase):

    def _run(self, **kw):
        out = tempfile.mkdtemp()
        f = _fetchers(pages=[_PAPER_TEXT], s2_paper=_S2_PAPER)
        s = pi.run_paper_import(out, pdf="paper.pdf", doi="10.9/target",
                                fetchers=f, **kw)
        return out, s

    def test_artifacts_written_and_parse_through_text_decomposer(self):
        out, s = self._run()
        with open(s["text"], encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("[[jones2019]]", text)
        self.assertIn("[[vries2021]]", text)
        refs_map, body = td.parse_references(text, s["refs"], s["text"])
        self.assertIn("jones2019", refs_map)
        claims = td.extract_claims(body)
        cited = [c for c in claims if c["markers"]]
        self.assertGreaterEqual(len(cited), 2)

    def test_manifest_has_paper_record_and_rich_ids(self):
        out, s = self._run()
        with open(s["manifest"], encoding="utf-8") as fh:
            m = json.load(fh)
        self.assertEqual(m["paper"]["doi"], "10.9/target")
        self.assertEqual(m["citation_syntax"], "author-year")
        self.assertEqual(m["reference_list_source"], "s2")
        by_key = {e["key"]: e for e in m["sources"]}
        self.assertEqual(by_key["vries2021"]["arxiv_id"], "2101.00001")
        self.assertEqual(by_key["jones2019"]["status"], "has_link")

    def test_report_written_with_next_steps(self):
        out, s = self._run()
        with open(s["report"], encoding="utf-8") as fh:
            rep = fh.read()
        self.assertIn("download_sources.py", rep)
        self.assertIn("database-first", rep)

    def test_no_reference_list_is_a_hard_stop(self):
        f = _fetchers(pages=[_PAPER_TEXT], s2_paper=_S2_PAPER, refs=[])
        with self.assertRaises(PaperImportError):
            pi.run_paper_import(tempfile.mkdtemp(), pdf="p.pdf", doi="10.9/t",
                                fetchers=f)

    def test_zero_resolved_citations_is_a_hard_stop(self):
        f = _fetchers(pages=["1. Introduction\nBare claims [12] with numerals [3]."],
                      s2_paper=_S2_PAPER)
        with self.assertRaises(PaperImportError):
            pi.run_paper_import(tempfile.mkdtemp(), pdf="p.pdf", doi="10.9/t",
                                fetchers=f)

    def test_missing_pdf_and_unfetchable_oa_is_a_hard_stop(self):
        f = _fetchers(s2_paper=_S2_PAPER)
        with self.assertRaises(PaperImportError):
            pi.run_paper_import(tempfile.mkdtemp(), doi="10.9/t", fetchers=f)


_TEI = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
 <teiHeader><profileDesc><abstract>
   <p>We restate everything briefly.</p>
 </abstract></profileDesc></teiHeader>
 <text><body>
  <div><p>Schooling raises wages <ref type="bibr" target="#b0">(Jones, 2019)</ref>.
    It matters a great deal.</p></div>
  <div><p>Sleep helps memory <ref type="bibr" target="#b1">(de Vries 2021)</ref>
    and an unknown work says so too <ref type="bibr" target="#b2">(Mystery, 1900)</ref>.</p></div>
  <back><listBibl>
   <biblStruct xml:id="b0"><analytic><title>Economic effects of schooling</title>
     <author><persName><surname>Jones</surname></persName></author></analytic>
     <monogr><imprint><date when="2019">2019</date></imprint></monogr></biblStruct>
   <biblStruct xml:id="b1"><analytic><title>A theory of everything</title>
     <author><persName><surname>de Vries</surname></persName></author></analytic>
     <monogr><imprint><date when="2021">2021</date></imprint></monogr></biblStruct>
   <biblStruct xml:id="b2"><analytic><title>Utterly absent from databases</title>
     </analytic><monogr><imprint><date>1900</date></imprint></monogr></biblStruct>
  </listBibl></back>
 </body></text>
</TEI>"""


class TestGrobidPath(unittest.TestCase):

    def test_tei_parse_yields_placeholders_and_bibl(self):
        paras, abstract, bibl = pi.parse_grobid_tei(_TEI)
        self.assertEqual(len(paras), 2)
        self.assertIn("⟦b0|(Jones, 2019)⟧", paras[0])
        self.assertEqual(abstract, ["We restate everything briefly."])
        self.assertIn("Economic effects of schooling", bibl["b0"])

    def test_end_to_end_grobid_bypasses_recognizers(self):
        out = tempfile.mkdtemp()
        f = _fetchers(pages=["irrelevant — grobid path wins"], s2_paper=_S2_PAPER)
        f["grobid_tei"] = lambda pdf: _TEI
        s = pi.run_paper_import(out, pdf="paper.pdf", doi="10.9/target", fetchers=f)
        self.assertEqual(s["citation_syntax"], "grobid")
        with open(s["text"], encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("[[jones2019]]", text)
        self.assertIn("[[vries2021]]", text)
        self.assertNotIn("⟦", text)                     # no placeholder leaks
        self.assertIn("(Mystery, 1900)", text)          # unresolved cite restored
        self.assertNotIn("restate everything", text)    # abstract dropped by default
        # the unresolved biblStruct is reported
        self.assertTrue(any(u["why"] == "biblstruct_not_in_db_bibliography"
                            for u in s["unresolved_mentions"]))

    def test_grobid_absent_falls_through_to_plain_path(self):
        out = tempfile.mkdtemp()
        f = _fetchers(pages=[_PAPER_TEXT], s2_paper=_S2_PAPER)
        f["grobid_tei"] = lambda pdf: None
        s = pi.run_paper_import(out, pdf="paper.pdf", doi="10.9/target", fetchers=f)
        self.assertEqual(s["citation_syntax"], "author-year")

    def test_broken_tei_falls_through(self):
        out = tempfile.mkdtemp()
        f = _fetchers(pages=[_PAPER_TEXT], s2_paper=_S2_PAPER)
        f["grobid_tei"] = lambda pdf: "<not-tei"
        s = pi.run_paper_import(out, pdf="paper.pdf", doi="10.9/target", fetchers=f)
        self.assertEqual(s["citation_syntax"], "author-year")


_NUMERIC_PAPER_TEXT = """The Numeric Paper Title
1. Introduction
Schooling raises wages [1]. Sleep also helps [3]. Both matter [1,3].
References
[1] Jones, C. (2019). Economic effects of schooling. J. Econ. 12, 1-20.
[2] Smith, A. and Lee, B. (2020). Sleep and memory consolidation. Nature.
[3] de Vries, D. (2021). A theory of everything. arXiv preprint."""


class TestNumericEndToEnd(unittest.TestCase):

    def test_numeric_paper_imports_with_alignment_in_manifest_and_report(self):
        out = tempfile.mkdtemp()
        f = _fetchers(pages=[_NUMERIC_PAPER_TEXT], s2_paper=_S2_PAPER,
                      crossref=_crossref_refs())
        s = pi.run_paper_import(out, pdf="paper.pdf", doi="10.9/target", fetchers=f)
        self.assertEqual(s["citation_syntax"], "numeric")
        with open(s["text"], encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("[[jones2019]]", text)
        self.assertIn("[[vries2021]]", text)
        self.assertNotIn("[1]", text)                     # brackets consumed
        with open(s["manifest"], encoding="utf-8") as fh:
            m = json.load(fh)
        self.assertEqual(m["citation_syntax"], "numeric")
        self.assertTrue(any(a["witnesses"] == "crossref+pdf"
                            for a in m["numeric_alignment"]))
        with open(s["report"], encoding="utf-8") as fh:
            rep = fh.read()
        self.assertIn("Numeric alignment", rep)
        self.assertIn("crossref+pdf", rep)

    def test_numeric_without_any_witness_is_a_hard_stop(self):
        # No crossref deposit AND an unmatchable ref section -> nothing aligns.
        text = ("1. Introduction\nClaims here [1]. More [2].\nReferences\n"
                "[1] Unrelated blob.\n[2] Another unrelated blob.")
        f = _fetchers(pages=[text], s2_paper=_S2_PAPER)
        with self.assertRaises(PaperImportError):
            pi.run_paper_import(tempfile.mkdtemp(), pdf="p.pdf", doi="10.9/t",
                                fetchers=f)


if __name__ == "__main__":
    unittest.main()


class TestReflowFootnoteGlue(unittest.TestCase):
    """Footnote-marker glue (ml_who_to_nudge, 2026-07-12): pdftotext renders a
    superscript footnote digit flush against the sentence-final period."""

    def test_footnote_digit_is_removed(self):
        self.assertEqual(pi._reflow("estimates CATEs as follows.3 First, the "
                                    "algorithm draws bootstrap samples."),
                         "estimates CATEs as follows. First, the "
                         "algorithm draws bootstrap samples.")

    def test_two_digit_footnote(self):
        self.assertEqual(pi._reflow("from the previous section.12 We then "
                                    "estimate the effect."),
                         "from the previous section. We then "
                         "estimate the effect.")

    def test_decimals_are_untouched(self):
        s = "The share rose by 0.3 Percentage growth followed."
        self.assertEqual(pi._reflow(s), s)          # digit before period
        s2 = "Rates increased 6.4 and 12.1 points in 2017."
        self.assertEqual(pi._reflow(s2), s2)

    def test_lowercase_continuation_untouched(self):
        s = "see eq.2 for details."                 # next word not capitalized
        self.assertEqual(pi._reflow(s), s)


# ---------------------------------------------------------------------------
# Input-type sniff (round-1 import-loop fix F3)
# ---------------------------------------------------------------------------

class TestPaperFileSniff(unittest.TestCase):

    def _tmp(self, name, data):
        import tempfile, os
        d = tempfile.mkdtemp()
        p = os.path.join(d, name)
        with open(p, "wb") as fh:
            fh.write(data)
        return p

    def test_docx_zip_is_an_instant_actionable_stop(self):
        p = self._tmp("essay.docx", b"PK\x03\x04" + b"\x00garbage" * 50)
        with self.assertRaises(pi.PaperImportError) as cm:
            pi._read_paper_pages(p)
        self.assertIn("docx", str(cm.exception))

    def test_pdf_named_file_without_pdf_magic_stops(self):
        p = self._tmp("paper.pdf", b"MZ\x90\x00 not a pdf at all")
        with self.assertRaises(pi.PaperImportError) as cm:
            pi._read_paper_pages(p)
        self.assertIn("no PDF header", str(cm.exception))

    def test_other_binary_stops(self):
        p = self._tmp("mystery.bin", b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0d")
        with self.assertRaises(pi.PaperImportError):
            pi._read_paper_pages(p)

    def test_plain_text_still_allowed(self):
        p = self._tmp("paper.txt", "A perfectly normal paper body.\n\n"
                      "Second paragraph with content.\n".encode())
        pages = pi._read_paper_pages(p)
        self.assertTrue(pages and "normal paper body" in pages[0])

    def test_title_candidates_drop_control_char_garbage(self):
        page1 = ("M||g}w~\x07DJ%$nOh<W\x122++M&tJ\x08t binary garbage line\n"
                 "A Real And Perfectly Plausible Paper Title Here\n")
        cands = pi._title_candidates(page1)
        self.assertEqual(
            cands, ["A Real And Perfectly Plausible Paper Title Here"])


# ---------------------------------------------------------------------------
# Printed-DOI hardening + ladder fall-through (round-1 import-loop fix F2)
# ---------------------------------------------------------------------------

_PNAS_LIKE_PAGE = (
    "Downloaded from https://www.pnas.org by somebody\n"
    "https://www.pnas.org/lookup/suppl/doi:10.1073/pnas.​\n"
    "2416708122/-/DCSupplemental\n"
    "A Nice Long Paper Title About Loan Repayment Nudges\n"
    "https://doi.org/10.1073/pnas.2416708122 1 of 8\n")


class TestPrintedDoiHardening(unittest.TestCase):

    def test_truncated_suppl_doi_loses_to_resolving_doi_org_candidate(self):
        # The suppl URL's DOI is line-wrap-truncated; only the full doi.org
        # form resolves in S2. The old code took the first match and stopped.
        def s2_get(pid):
            return _S2_PAPER if pid == "DOI:10.1073/pnas.2416708122" else None
        f = _fetchers(pages=[_PNAS_LIKE_PAGE])
        f["s2_get"] = s2_get
        r = identify(pdf="x.pdf", fetchers=f)
        self.assertEqual(r["doi"], "10.1073/pnas.2416708122")
        self.assertEqual(r["id_evidence"], "DOI printed on the PDF's first pages")

    def test_zero_width_chars_are_stripped_before_doi_matching(self):
        page = "https://doi.org/10.9999/tar​get\nSome Title Line Here OK\n"
        seen = []
        def s2_get(pid):
            seen.append(pid)
            return _S2_PAPER if pid == "DOI:10.9999/target" else None
        f = _fetchers(pages=[page]); f["s2_get"] = s2_get
        r = identify(pdf="x.pdf", fetchers=f)
        self.assertEqual(r["doi"], "10.9999/target")
        self.assertIn("DOI:10.9999/target", seen)

    def test_unresolvable_printed_doi_falls_through_to_title_gate(self):
        f = _fetchers(pages=[_PNAS_LIKE_PAGE],
                      find=lambda t, year=None: (_S2_PAPER, "matched"))
        f["s2_get"] = lambda pid: None
        r = identify(pdf="x.pdf", fetchers=f)
        self.assertIn("title match", r["id_evidence"])
        self.assertEqual(r["doi"], "10.9/target")

    def test_unresolvable_printed_doi_and_no_title_match_keeps_best_doi(self):
        # Truly-unindexed paper: identification survives with the best
        # (doi.org-ranked) candidate, marked unverified, so the later
        # "may be unindexed" stop stays accurate.
        f = _fetchers(pages=[_PNAS_LIKE_PAGE])
        f["s2_get"] = lambda pid: None
        r = identify(pdf="x.pdf", fetchers=f)
        self.assertEqual(r["doi"], "10.1073/pnas.2416708122")
        self.assertIn("no S2 record", r["id_evidence"])


# ---------------------------------------------------------------------------
# -layout ref-section witness variant (round-1 import-loop fix F1-companion)
# ---------------------------------------------------------------------------

class TestLayoutRefTail(unittest.TestCase):

    def test_heading_with_trailing_page_number_is_found(self):
        # -layout puts the running page number on the SAME line as the heading
        txt = ("body text here\n"
               "References                                              12\n"
               "[1] Jones, C. (2019). Economic effects of schooling.\n")
        tail = pi._layout_ref_tail(txt)
        self.assertIn("Jones", tail)

    def test_no_heading_returns_empty(self):
        self.assertEqual(pi._layout_ref_tail("no refs heading anywhere"), "")

    def test_alt_tail_wins_only_when_it_reads_more_entries(self):
        bib, _ = build_bibliography(_db_refs())
        # plain tail unreadable, alt (layout) tail readable -> alt used;
        # but with 0 crossref agreements the rows stay uncorroborated.
        idx, table = pi.align_numeric(bib, None, "", alt_ref_tail=_REF_TAIL)
        self.assertEqual(idx, {})
        self.assertTrue(all(t["witnesses"] == "pdf-only-uncorroborated"
                            for t in table))
        # with a healthy crossref witness the same alt tail yields agreements
        idx2, table2 = pi.align_numeric(bib, _crossref_refs(), "",
                                        alt_ref_tail=_REF_TAIL)
        self.assertEqual(idx2[1], "jones2019")
        self.assertEqual(table2[0]["witnesses"], "crossref+pdf")


# ---------------------------------------------------------------------------
# Bibliography union, author-year scope (round-1 import-loop fix F7a)
# ---------------------------------------------------------------------------

class TestCrossrefUnion(unittest.TestCase):

    def test_structured_missing_work_is_added(self):
        cr = [{"position": 1, "publisher_key": "r1", "key_number": 1,
               "doi": None, "year": "1954", "author": "Duverger",
               "title": "Political Parties", "raw": None}]
        extra = pi._crossref_only_refs(cr, _db_refs())
        self.assertEqual(len(extra), 1)
        self.assertEqual(extra[0]["authors"], ["Duverger"])
        self.assertEqual(extra[0]["source"], "crossref")

    def test_same_title_different_work_is_not_a_dupe(self):
        # Duverger's and Michels' "Political Parties" are different books —
        # title equality alone must not suppress the addition.
        db = _db_refs() + [{"paper_id": "s2f", "title": "Political Parties",
                            "year": 2018, "authors": ["R. Michels"],
                            "doi": None, "arxiv_id": None, "url": None,
                            "source": "s2"}]
        cr = [{"position": 1, "publisher_key": "r1", "key_number": 1,
               "doi": None, "year": "1954", "author": "Duverger",
               "title": "Political Parties", "raw": None}]
        self.assertEqual(len(pi._crossref_only_refs(cr, db)), 1)

    def test_same_work_same_author_is_a_dupe(self):
        cr = [{"position": 1, "publisher_key": "r1", "key_number": 1,
               "doi": None, "year": "2021", "author": "de Vries",
               "title": "A theory of everything", "raw": None}]
        self.assertEqual(pi._crossref_only_refs(cr, _db_refs()), [])

    def test_doi_dupe_is_skipped(self):
        cr = [{"position": 1, "publisher_key": "r1", "key_number": 1,
               "doi": "10.1/JONES", "year": "2019", "author": "Jones",
               "title": "Economic effects of schooling", "raw": None}]
        self.assertEqual(pi._crossref_only_refs(cr, _db_refs()), [])

    def test_unstructured_and_bare_doi_rows_never_enter(self):
        cr = [{"position": 1, "publisher_key": "r1", "key_number": 1,
               "doi": None, "year": None, "author": None, "title": None,
               "raw": "Some unparsed reference string 1999"},
              {"position": 2, "publisher_key": "r2", "key_number": 2,
               "doi": "10.9/bare", "year": None, "author": None,
               "title": None, "raw": None}]
        self.assertEqual(pi._crossref_only_refs(cr, _db_refs()), [])


class TestMakeKeyTitleFallback(unittest.TestCase):

    def test_junk_author_falls_back_to_title_token(self):
        # user-facing keys: 'giving2021' beats 'ref'/'ref2001_2' (F8)
        k = make_key({"authors": [], "year": 2021,
                      "title": "Giving USA: The Annual Report"}, {})
        self.assertEqual(k, "giving2021")
