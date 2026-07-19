"""
Generic-paper importer (ROADMAP item 1, the "later target"): any published
scientific paper -> this tool's input format, so a paper's claims can be
verified against its own cited sources.

Strategy (owner directive, 2026-07-07): DATABASE-FIRST, PDF-PARSING LAST.
  Ladder A — identify the paper: explicit DOI/arXiv/URL -> printed DOI/arXiv
             stamp on the PDF's first pages -> title lookup with the never-guess
             S2 gate (>=0.90 similarity). No confident identity => hard stop.
  Ladder B — reference list: paper_search.neighbors() (S2 -> OpenAlex, cached,
             structured, has the ids the downloader needs) -> Crossref deposited
             order as the numbering witness -> the PDF's own reference section
             only ever as a LIGHT alignment aid, never the primary bibliography.
  Ladder C — body text: the user's PDF (or the OA copy fetched by the existing
             downloader cascade), trimmed to Introduction..Conclusion, citations
             converted to [[key]] markers via the shared convert_block machinery.

Reuses claude_research_importer's seams: Citation, convert_block (sentence-end
marker relocation), write_artifacts (the four INPUT_FORMAT.md artifacts).
An in-text citation that cannot be resolved WITH CONFIDENCE gets NO marker and
is listed in import_report.md instead — a missing marker just becomes an
uncited claim the own-split pass nudges about; a wrong marker manufactures a
false verdict (same asymmetry as the rest of the tool).

All network access goes through injectable callables (fetchers=...) so the
whole module is offline-testable; no LLM calls anywhere.
"""

import os
import re
import logging
import unicodedata
from typing import Dict, List, Optional, Tuple

from .claude_research_importer import Citation, convert_block, write_artifacts

logger = logging.getLogger(__name__)


class PaperImportError(Exception):
    """A hard stop with a user-facing message (never-guess policy)."""


# --------------------------------------------------------------------------
# Ladder A — identify the paper
# --------------------------------------------------------------------------

# A DOI printed on the paper itself ("https://doi.org/10.x/y", "DOI: 10.x/y",
# or bare). Trailing punctuation is layout, not DOI.
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>)\];]+)")
# Zero-width/invisible characters inside extracted text silently truncate a
# DOI match (round-1 audit: a PNAS manuscript's supplementary URL carried
# U+200B mid-DOI and hijacked identification with "10.1073/pnas."). Soft
# hyphen included — it's a layout artifact, never DOI content.
_ZW_RE = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad]")


def _printed_doi_candidates(first_pages: str) -> List[str]:
    """ALL DOI-shaped strings on the first pages, ranked: candidates printed
    as a doi.org link first (the paper's own canonical stamp), supplementary-
    material URLs (`lookup/suppl/doi:`) last — the round-1 failure took the
    FIRST match unconditionally and it was a line-wrap-truncated suppl URL.
    Callers must still VERIFY a candidate resolves before trusting it."""
    scored = []
    for i, m in enumerate(_DOI_RE.finditer(first_pages)):
        cand = m.group(1).rstrip(".,;")
        ctx = first_pages[max(0, m.start() - 40):m.start()].lower()
        score = (1 if "doi.org/" in ctx else 0) - (1 if "suppl" in ctx else 0)
        scored.append((-score, i, cand))
    scored.sort()
    return list(dict.fromkeys(c for _, _, c in scored))
_ARXIV_RE = re.compile(r"arXiv:\s*(\d{4}\.\d{4,5})(?:v\d+)?", re.IGNORECASE)
# Lines that are never a title: URLs, emails, journal furniture, dates.
_NOT_TITLE_RE = re.compile(
    r"https?://|@|\bdoi\b|©|\bissn\b|\bvol\.?\s*\d|\bpp\.?\s*\d|\breceived\b|"
    r"\baccepted\b|\bpublished\b|\bjournal\b|\bpreprint\b|\buniversity\b|"
    r"\bdepartment\b|^\d+$", re.IGNORECASE)
# Control characters mark extraction garbage (binary read as text) — such a
# line must never become an S2 title query (round-1 docx failure mode).
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _read_paper_pages(path: str) -> List[str]:
    """PDF pages for the importer: poppler pdftotext FIRST (it preserves the
    blank-line paragraph structure the claim splitter needs — PyPDF2 collapsed
    a 64-page paper into ONE paragraph in live testing), PyPDF2 as fallback.
    Non-PDFs go through the normal reader."""
    from .source_decomposer import _pdftotext_pages, read_source_pages
    _sniff_paper_file(path)
    if path.lower().endswith(".pdf"):
        pages = _pdftotext_pages(path)
        if pages:
            return pages
    return read_source_pages(path)


def _sniff_paper_file(path: str) -> None:
    """Magic-byte check BEFORE any extraction. Round-1 import audit: a .docx
    passed as --pdf was read as raw bytes, its binary garbage became S2 title
    queries, and the run ground through 429 backoffs for minutes — the sniff
    turns that into an instant, actionable stop. Plain text stays allowed
    (a .txt paper body is legitimate)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(2048)
    except OSError as exc:
        raise PaperImportError(f"Cannot read {path}: {exc}")
    if head.startswith(b"%PDF"):
        return
    if head.startswith(b"PK\x03\x04"):
        raise PaperImportError(
            f"{path} is a zip container (docx/epub?), not a PDF — docx isn't "
            "supported: export the document to PDF, or use the /import-paper "
            "command")
    if path.lower().endswith(".pdf"):
        raise PaperImportError(
            f"{path} has a .pdf name but no PDF header — the file is corrupt "
            "or mislabeled; re-download it or use the /import-paper command")
    if b"\x00" in head:
        raise PaperImportError(
            f"{path} looks binary (not a PDF, not plain text) — unsupported "
            "input; export to PDF or use the /import-paper command")


def _default_fetchers() -> Dict:
    """The real network/IO callables — replaced wholesale in tests."""
    from . import crossref_api, paper_search, semantic_scholar_api
    return {
        "pages_reader": _read_paper_pages,
        "s2_get": semantic_scholar_api.get_paper,
        "s2_find": semantic_scholar_api.find_paper_by_title,
        "neighbors": paper_search.neighbors,
        "crossref_refs": crossref_api.get_references,
        "fetch_target_pdf": _fetch_target_pdf,
        "grobid_tei": _fetch_grobid_tei,
        "layout_text": _pdftotext_layout_text,
    }


def _title_candidates(page1: str, max_candidates: int = 4) -> List[str]:
    """Plausible title strings from a PDF's first page: early, sentence-length
    lines that aren't journal furniture; adjacent pairs joined for wrapped
    titles. The S2 confidence gate does the real filtering."""
    lines = [ln.strip() for ln in page1.splitlines() if ln.strip()][:15]
    good = [ln for ln in lines if 20 <= len(ln) <= 250
            and not _NOT_TITLE_RE.search(ln) and not _CTRL_RE.search(ln)]
    cands = list(good[:4])
    for a, b in zip(good, good[1:]):        # wrapped two-line titles
        if len(a) + len(b) <= 300:
            cands.append(f"{a} {b}")
    return list(dict.fromkeys(cands))[:max_candidates]


def identify(pdf: Optional[str] = None, doi: Optional[str] = None,
             arxiv: Optional[str] = None, url: Optional[str] = None,
             title: Optional[str] = None, fetchers: Optional[Dict] = None) -> Dict:
    """Resolve the paper to a canonical id record or raise PaperImportError.

    Returns {"paper_id": "DOI:..."|"ARXIV:...", "doi", "arxiv_id", "pmc_id",
             "s2_paper_id", "oa_pdf_url", "title", "year", "id_evidence"}."""
    f = fetchers or _default_fetchers()
    evidence = None

    if url and not (doi or arxiv):
        from .direct_downloader import extract_arxiv_id, extract_doi_from_url
        doi = extract_doi_from_url(url)
        arxiv = extract_arxiv_id(url)
        if doi or arxiv:
            evidence = f"id extracted from URL {url}"
        else:
            raise PaperImportError(
                f"No DOI/arXiv id recognizable in URL: {url} — pass --doi or --title")
    elif doi:
        evidence = "explicit --doi"
    elif arxiv:
        evidence = "explicit --arxiv"

    first_pages = ""
    prefetched = None            # S2 record already fetched while validating
    unverified_doi = None        # printed DOI no candidate of which resolved
    if pdf and not (doi or arxiv or title):
        pages = f["pages_reader"](pdf)
        if not pages:
            raise PaperImportError(
                f"No text extractable from {pdf} (scanned image?) — "
                f"pass --doi, or use the /import-paper command")
        # Invisible chars truncate DOI matches (U+200B mid-DOI, round-1 audit)
        first_pages = _ZW_RE.sub("", "\n".join(pages[:2]))
        doi_cands = _printed_doi_candidates(first_pages)
        for cand in doi_cands:
            prefetched = f["s2_get"](f"DOI:{cand}")
            if prefetched:
                doi = cand
                evidence = "DOI printed on the PDF's first pages"
                break
        if not doi:
            m = _ARXIV_RE.search(first_pages)
            if m:
                arxiv = m.group(1)
                evidence = "arXiv stamp on the PDF's first pages"
            elif doi_cands:
                # No candidate resolves — remember the best one, but let the
                # title rung try first ("identified" must mean RESOLVABLE,
                # else the ladder's next rung gets its turn).
                unverified_doi = doi_cands[0]

    paper = None
    if doi:
        paper_id = f"DOI:{doi}"
        paper = prefetched or f["s2_get"](paper_id)
    elif arxiv:
        paper_id = f"ARXIV:{arxiv}"
        paper = f["s2_get"](paper_id)
    else:
        # Title rung — the never-guess gate does the matching.
        candidates = [title] if title else _title_candidates(first_pages)
        for cand in candidates:
            paper, status = f["s2_find"](cand)
            if status == "matched":
                evidence = f"title match: '{cand}'"
                break
            paper = None
        if paper is None:
            if unverified_doi:
                # Truly-unindexed papers keep their printed DOI (and the
                # accurate "may be unindexed" stop later) instead of dying
                # here on the title gate.
                doi = unverified_doi
                paper_id = f"DOI:{doi}"
                evidence = ("DOI printed on the PDF's first pages "
                            "(no S2 record; title gate found no match either)")
            else:
                raise PaperImportError(
                    "Could not identify the paper with confidence "
                    f"(tried: {candidates or ['nothing usable']}) — pass --doi or an exact "
                    "--title, or use the /import-paper command")
        else:
            ext = paper.get("externalIds") or {}
            doi, arxiv = ext.get("DOI"), ext.get("ArXiv")
            paper_id = (f"DOI:{doi}" if doi else f"ARXIV:{arxiv}" if arxiv
                        else paper.get("paperId"))
            if not paper_id:
                raise PaperImportError("Matched paper has no usable id — pass --doi")

    record = {"paper_id": paper_id, "doi": doi, "arxiv_id": arxiv, "pmc_id": None,
              "s2_paper_id": None, "oa_pdf_url": None, "title": None, "year": None,
              "id_evidence": evidence}
    if paper:                                    # S2 enrichment (tolerate absence)
        ext = paper.get("externalIds") or {}
        oa = paper.get("openAccessPdf") or {}
        record.update({"doi": record["doi"] or ext.get("DOI"),
                       "arxiv_id": record["arxiv_id"] or ext.get("ArXiv"),
                       "pmc_id": ext.get("PubMedCentral"),
                       "s2_paper_id": paper.get("paperId"),
                       "oa_pdf_url": oa.get("url"),
                       "title": paper.get("title"), "year": paper.get("year")})
    logger.info(f"Paper identified: {record['paper_id']} ({evidence})")
    return record


def _fetch_target_pdf(record: Dict, output_dir: str) -> Optional[str]:
    """Fetch the paper itself via the existing OA downloader cascade (used when
    the user gave only an id). Returns the local path or None."""
    from . import direct_downloader as dd
    entry = dd.normalize_entry({"key": "_paper", "title": record.get("title"),
                                "url": None, "doi": record.get("doi"),
                                "arxiv_id": record.get("arxiv_id"),
                                "pmc_id": record.get("pmc_id"),
                                "s2_paper_id": record.get("s2_paper_id"),
                                "oa_pdf_url": record.get("oa_pdf_url")})
    result = dd.download_source(entry, output_dir, dd.setup_session())
    if result.get("outcome") in ("pdf", "already_present"):
        return os.path.join(output_dir, result["filename"])
    logger.warning(f"Could not fetch the paper's own PDF ({result.get('outcome')}); "
                   f"landing page: {result.get('landing')}")
    return None


# --------------------------------------------------------------------------
# Ladder B — bibliography from the databases + key generation
# --------------------------------------------------------------------------

def _surname(author_name: str) -> str:
    """Last whitespace token of a display name ('Jan de Vries' -> 'Vries')."""
    parts = (author_name or "").strip().split()
    return parts[-1] if parts else ""


def _ascii_slug(s: str) -> str:
    """Accent-fold + drop EVERYTHING non-alphanumeric: 'Núñez-Peña' and the
    de-hyphenated 'NúñezPeña' a PDF line-break produces both become
    'nunezpena', so surname matching survives extraction damage."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]", "", s).lower()


def make_key(entry: Dict, taken: Dict[str, Dict]) -> str:
    """'<firstauthorsurname><year>' in the strict [A-Za-z0-9_-] charset the
    marker parser requires; collisions get _2/_3 (merge_sources convention).
    Junk/missing author metadata falls back to the first meaningful title
    token — keys are user-facing (viewer chips, refs file), and a DB record
    with a garbled author minted 'ref'/'ref2001_2' keys in the round-1 audit
    (the Giving USA report, Thaler/Sunstein's Nudge as 'arney2015')."""
    authors = entry.get("authors") or []
    base = _ascii_slug(_surname(authors[0]) if authors else "")
    if not base:
        title_toks = re.findall(r"[A-Za-z]{4,}", str(entry.get("title") or ""))
        base = _ascii_slug(title_toks[0].lower()) if title_toks else "ref"
    year = str(entry.get("year") or "").strip()
    key = f"{base}{year}" if year else base
    if key not in taken:
        return key
    n = 2
    while f"{key}_{n}" in taken:
        n += 1
    return f"{key}_{n}"


def build_bibliography(db_refs: List[Dict]) -> Tuple[Dict[str, Dict], "AuthorYearIndex"]:
    """neighbors() output -> ({key: manifest-ready entry}, AuthorYearIndex).

    Entries with no author or year can only be reached by numeric alignment.
    Reference entries with neither title nor DOI are dropped (nothing to
    verify against)."""
    bib: Dict[str, Dict] = {}
    index = AuthorYearIndex()
    dropped = 0
    for ref in db_refs or []:
        if not (ref.get("title") or ref.get("doi")):
            dropped += 1
            continue
        key = make_key(ref, bib)
        authors = ref.get("authors") or []
        bib[key] = {
            "key": key,
            "title": ref.get("title"),
            "author": ", ".join(authors) or None,
            "year": str(ref["year"]) if ref.get("year") else None,
            "url": ref.get("url"),
            "doi": ref.get("doi"),
            "arxiv_id": ref.get("arxiv_id"),
            "s2_paper_id": ref.get("paper_id") if ref.get("source") == "s2" else None,
        }
        if authors and ref.get("year"):
            index.add(key, authors, str(ref["year"]))
    if dropped:
        logger.info(f"Dropped {dropped} database reference(s) with no title and no DOI")
    return bib, index


def _style_guess(body: str) -> str:
    """Cheap diagnosis for the no-citations-resolved stop: name what the text
    LOOKS like so a tester knows why (F8, round-1 import loop). Heuristic
    display text only — never drives resolution."""
    body = _ZW_RE.sub("", body)      # soft hyphens hide glued superscripts
    sup = len(re.findall(r"[a-z][\"'”’)]?\d{1,3}(?:\s*[–—-]\s*\d{1,3})?(?:,\d{1,3})*(?=[\s.,;])",
                         body))
    par = len(re.findall(r"\(\d{1,3}(?:\s*[,–—-]\s*\d{1,3})*\)", body))
    bra = len(_NUM_GROUP_RE.findall(body))
    guesses = [(n, label) for n, label in
               [(sup, "superscript-style glued digits"),
                (par, "parenthetical (n) numeric groups"),
                (bra, "bracketed [n] groups that failed alignment")] if n >= 5]
    if not guesses:
        return "The body shows no recognizable citation-mark pattern."
    guesses.sort(reverse=True)
    shown = " and ".join(f"~{n} {label}" for n, label in guesses)
    return (f"The body shows {shown} — "
            + ("one of these styles is the likely cause."
               if len(guesses) > 1 else "that style is the likely cause."))


def _crossref_only_refs(crossref_refs: Optional[List[Dict]],
                        db_refs: List[Dict]) -> List[Dict]:
    """F7a (round-1 import loop): the S2/OpenAlex list and Crossref's
    publisher-deposited list have DIFFERENT gaps (ay2's printed Duverger 1954
    was absent from S2's 22; fn1 had 2 in S2 vs 18 in Crossref). Return
    Crossref refs NOT already in the database list, shaped like neighbors()
    output so build_bibliography can just rebuild over the union. Never-guess:
    only structured deposits (author+year+title, or a DOI) qualify — an
    unstructured raw string can't be verified against and never enters."""
    if not crossref_refs:
        return []
    have_dois = {(r.get("doi") or "").lower() for r in db_refs if r.get("doi")}
    have = [(_title_tokens(r.get("title")),
             str(r["year"]) if r.get("year") else None,
             _ascii_slug(_surname((r.get("authors") or [""])[0])))
            for r in db_refs if r.get("title")]

    def _dupe(title: str, year, author) -> bool:
        # Same TITLE alone is not the same WORK (Duverger's and Michels'
        # "Political Parties" met in the round-1 ay2 list) — a dupe needs
        # title similarity AND year-or-author agreement.
        toks = _title_tokens(title)
        if not toks:
            return False
        surname = _ascii_slug(_surname(author or ""))
        for h_toks, h_year, h_surname in have:
            if not h_toks or len(toks & h_toks) / max(len(toks), len(h_toks)) < 0.7:
                continue
            if (year and h_year and str(year) == h_year) or \
               (surname and h_surname and surname == h_surname):
                return True
        return False

    extra: List[Dict] = []
    for ref in crossref_refs:
        doi = (ref.get("doi") or "").lower()
        title, year, author = ref.get("title"), ref.get("year"), ref.get("author")
        if doi and doi in have_dois:
            continue
        if not title or not ((year and author) or doi):
            continue                      # unstructured deposit — never-guess
        if _dupe(title, year, author):
            continue
        extra.append({
            "paper_id": None, "title": title,
            "year": int(year) if str(year or "").isdigit() else None,
            "authors": [author] if author else [],
            "doi": ref.get("doi"),
            "arxiv_id": None,
            "url": f"https://doi.org/{ref['doi']}" if ref.get("doi") else None,
            "source": "crossref",
        })
    return extra


class AuthorYearIndex:
    """(surname, year) -> key resolution with a never-guess uniqueness rule.

    First-author index first; when that misses, an ANY-author index — database
    records sometimes list authors in a different order than the citation
    ("Card and Krueger 1995" vs a DB record led by Krueger; found live on the
    JPE minimum-wage paper 2026-07-07). Either way the surname+year pair must
    map to exactly ONE bibliography entry or resolution refuses."""

    def __init__(self):
        self.first: Dict[Tuple[str, str], List[str]] = {}
        self.any: Dict[Tuple[str, str], List[str]] = {}

    def add(self, key: str, authors: List[str], year: str):
        self.first.setdefault((_ascii_slug(_surname(authors[0])), year), []).append(key)
        for a in authors:
            pair = (_ascii_slug(_surname(a)), year)
            if key not in self.any.setdefault(pair, []):
                self.any[pair].append(key)

    def resolve(self, surname: str, year: str) -> Tuple[Optional[str], str]:
        pair = (_ascii_slug(surname), year)
        keys = self.first.get(pair, [])
        if len(keys) == 1:
            return keys[0], "ok"
        if len(keys) > 1:
            return None, "ambiguous"
        keys = self.any.get(pair, [])
        if len(keys) == 1:
            return keys[0], "ok"
        return None, ("ambiguous" if len(keys) > 1 else "not_in_bibliography")


# --------------------------------------------------------------------------
# Citation recognition — author-year (v1a). Numeric [n] alignment is v1b.
# --------------------------------------------------------------------------

_YEAR = r"(?:1[89]|20)\d{2}"
# A parenthetical containing at least one year: "(Smith, 2020; Jones, 2019)".
_PAREN_RE = re.compile(r"\(([^()]*\b" + _YEAR + r"[a-z]?\b[^()]*)\)")
# Narrative "Smith et al. (2020)" / "Smith and Jones (2020)": only the (year)
# paren is the citation span — the names are sentence grammar and must stay.
_NARRATIVE_RE = re.compile(
    r"\b([A-Z][\w'’-]+)"                                   # first-author surname
    r"(?:\s+(?:et al\.?|(?:and|&)\s+[A-Z][\w'’-]+))?\s*"    # et al. / second author
    r"(\(\s*(" + _YEAR + r")[a-z]?\s*\))")                  # (2020) / (2020a)
_SEG_YEAR_RE = re.compile(r"\b(" + _YEAR + r")([a-z])?\b")
_SEG_SURNAME_RE = re.compile(r"([A-Z][\w'’-]+)")


class AuthorYearRecognizer:
    """Resolves author-year citations against the DATABASE reference list
    (never against parsed PDF strings). A mention that doesn't resolve to
    exactly one bibliography entry yields NO Citation — it's recorded so the
    report can list it. `find_citations` stays pure for convert_block; use
    `scan` to also get the unresolved mentions."""

    name = "author-year"

    def __init__(self, index: AuthorYearIndex):
        self.index = index

    def scan(self, text: str) -> Tuple[List[Citation], List[Dict]]:
        citations: List[Citation] = []
        unresolved: List[Dict] = []

        for m in _PAREN_RE.finditer(text):
            keys, failed = [], []
            informative = False
            for seg in m.group(1).split(";"):
                ym = _SEG_YEAR_RE.search(seg)
                if not ym:
                    continue                       # locator/prose segment
                sm = _SEG_SURNAME_RE.search(seg[:ym.start()])
                if not sm:
                    continue                       # "(since 2020)" — not a citation
                informative = True
                # "Shimer 2012, 2005" cites one work per year — resolve each.
                for year_m in _SEG_YEAR_RE.finditer(seg, ym.start()):
                    key, why = self.index.resolve(sm.group(1), year_m.group(1))
                    (keys.append(key) if key else
                     failed.append({"mention": f"{sm.group(1)} {year_m.group(1)}",
                                    "why": why}))
            if not informative:
                continue
            if failed:
                # Partial resolution would drop the failed segment's citation
                # silently — treat the whole parenthetical as unresolved instead.
                unresolved.append({"mention": m.group(0), "why": failed[0]["why"],
                                   "detail": failed})
            elif keys:
                citations.append(Citation(m.start(), m.end(), keys))

        paren_spans = [(c.start, c.end) for c in citations]
        for m in _NARRATIVE_RE.finditer(text):
            start, end = m.start(2), m.end(2)      # the (year) paren only
            if any(s <= start < e for s, e in paren_spans):
                continue
            key, why = self.index.resolve(m.group(1), m.group(3))
            if key:
                citations.append(Citation(start, end, [key]))
            else:
                unresolved.append({"mention": m.group(0), "why": why})
        citations.sort(key=lambda c: c.start)
        return citations, unresolved

    def find_citations(self, text: str) -> List[Citation]:
        return self.scan(text)[0]


# --------------------------------------------------------------------------
# Citation recognition — numeric [n] (v1b): needs the paper's own numbering,
# recovered from two independent witnesses (below), never guessed.
# --------------------------------------------------------------------------

# "[12]", "[3,7]", "[3–7]", "[1, 4-6]" — digits/commas/ranges only, so matrix
# refs like "[A]" or locators like "[12, p. 5]" don't match.
_NUM_GROUP_RE = re.compile(
    r"\[(\d{1,3}(?:\s*[-–—]\s*\d{1,3})?(?:\s*,\s*\d{1,3}(?:\s*[-–—]\s*\d{1,3})?)*)\]")
_NUM_ITEM_RE = re.compile(r"(\d{1,3})(?:\s*[-–—]\s*(\d{1,3}))?")


class NumericBracketRecognizer:
    """Resolves [n]-style citations through an index_to_key map produced by the
    two-witness alignment. All-or-nothing per bracket group: one unresolvable
    number inside "[3,7]" makes the whole group unresolved (reported, unmarked)."""

    name = "numeric"

    def __init__(self, index_to_key: Dict[int, str]):
        self.index_to_key = index_to_key

    def scan(self, text: str) -> Tuple[List[Citation], List[Dict]]:
        citations: List[Citation] = []
        unresolved: List[Dict] = []
        for m in _NUM_GROUP_RE.finditer(text):
            numbers: List[int] = []
            for im in _NUM_ITEM_RE.finditer(m.group(1)):
                lo = int(im.group(1))
                hi = int(im.group(2)) if im.group(2) else lo
                if hi < lo or hi - lo > 50:      # "[3-1]" / absurd range = not a citation
                    numbers = []
                    break
                numbers.extend(range(lo, hi + 1))
            if not numbers:
                continue
            keys = [self.index_to_key.get(n) for n in numbers]
            if all(keys):
                citations.append(Citation(m.start(), m.end(), keys))
            else:
                missing = [n for n, k in zip(numbers, keys) if not k]
                unresolved.append({"mention": m.group(0), "why": "unaligned_index",
                                   "detail": missing})
        return citations, unresolved

    def find_citations(self, text: str) -> List[Citation]:
        return self.scan(text)[0]


def _title_tokens(title: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", (title or "").lower()) if len(t) > 3}


def _match_blob_to_bib(blob: str, bib: Dict[str, Dict]) -> Optional[str]:
    """Closed-set assignment: match one reference-section blob (or Crossref ref
    string) against the KNOWN database bibliography. Unique best match above a
    threshold wins; anything murkier returns None (never guess)."""
    low = blob.lower()
    scored = []
    for key, e in bib.items():
        toks = _title_tokens(e.get("title"))
        overlap = (sum(1 for t in toks if t in low) / len(toks)) if toks else 0.0
        surname = _ascii_slug(_surname((e.get("author") or "").split(",")[0]))
        s_hit = 1.0 if surname and surname in _ascii_slug(low) else 0.0
        y_hit = 1.0 if e.get("year") and e["year"] in blob else 0.0
        scored.append((overlap + 0.4 * s_hit + 0.25 * y_hit, overlap, key))
    scored.sort(reverse=True)
    if not scored:
        return None
    best_score, best_overlap, best_key = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    # qualify: real title overlap, or author+year with some title signal
    if best_score >= 0.65 and best_overlap >= 0.3 and best_score - second >= 0.15:
        return best_key
    return None


_REF_ENTRY_START_RE = re.compile(r"(?m)^\s*\[?(\d{1,3})[\].)]\s+")


def _pdftotext_layout_text(path: str) -> str:
    """Whole-document `pdftotext -layout` extraction. Multi-column reference
    pages SCRAMBLE under plain mode (numbers separate from their entries —
    round-1 num2/numparen audits); -layout keeps column geometry so the ref-
    section witness can read them. Best-effort: '' on any failure."""
    import subprocess
    try:
        out = subprocess.run(["pdftotext", "-layout", "-q", path, "-"],
                             capture_output=True, timeout=120)
        return out.stdout.decode("utf-8", errors="replace") if out.returncode == 0 else ""
    except Exception:
        return ""


# -layout keeps column geometry, so a running page number can share the
# heading's LINE ("References <spaces> 12") — trim_body's $-anchored heading
# regex misses it. This variant tolerates exactly that trailing page number.
_LAYOUT_REFS_HEADING_RE = re.compile(
    r"^\s*(?:\d+\.?\s*)?(references|bibliography|literature cited|works cited)"
    r"\s*\.?(?:\s+\d{1,4})?\s*$", re.IGNORECASE)


def _layout_ref_tail(layout_text: str) -> str:
    """The reference-section tail of a `pdftotext -layout` extraction (for the
    alt ref-section witness only — body building stays on trim_body)."""
    lines = layout_text.splitlines()
    refs_at = None
    for i, ln in enumerate(lines):
        if _LAYOUT_REFS_HEADING_RE.match(ln):
            refs_at = i
    return "\n".join(lines[refs_at + 1:]) if refs_at is not None else ""


def _witness_pdf_refsection(ref_tail: str, bib: Dict[str, Dict]) -> Dict[int, str]:
    """Witness 2: the paper's own numbered reference section, used ONLY to align
    numbers to already-known database entries (light closed-set matching — the
    LAST rung of the bibliography ladder, and even then never a data source)."""
    out: Dict[int, str] = {}
    if not ref_tail.strip():
        return out
    starts = list(_REF_ENTRY_START_RE.finditer(ref_tail))
    for i, m in enumerate(starts):
        n = int(m.group(1))
        end = starts[i + 1].start() if i + 1 < len(starts) else len(ref_tail)
        blob = ref_tail[m.end():end].strip()[:500]
        if not blob:
            continue
        key = _match_blob_to_bib(blob, bib)
        if key:
            out[n] = key
    return out


def _witness_crossref(crossref_refs: Optional[List[Dict]],
                      bib: Dict[str, Dict]) -> Dict[int, str]:
    """Witness 1: Crossref's publisher-deposited reference order. DOI equality
    joins directly; otherwise the same closed-set blob matching."""
    out: Dict[int, str] = {}
    if not crossref_refs:
        return out
    by_doi = {(e.get("doi") or "").lower(): k for k, e in bib.items() if e.get("doi")}
    for ref in crossref_refs:
        n = ref.get("key_number") or ref["position"]
        doi = (ref.get("doi") or "").lower()
        key = by_doi.get(doi)
        if not key:
            blob = " ".join(str(ref.get(f) or "") for f in ("author", "year", "title", "raw"))
            if blob.strip():
                key = _match_blob_to_bib(blob, bib)
        if key:
            out[n] = key
    return out


MIN_SINGLE_WITNESS_CORROBORATION = 3


def align_numeric(bib: Dict[str, Dict], crossref_refs: Optional[List[Dict]],
                  ref_tail: str, alt_ref_tail: str = "") -> Tuple[Dict[int, str], List[Dict]]:
    """Two-witness numbering: Crossref deposited order vs the PDF's own
    reference section. Agreement -> aligned; disagreement -> that number stays
    UNMAPPED (its citations get no marker). A single witness is accepted ONLY
    when its ordering is corroborated elsewhere in the same run (>=
    MIN_SINGLE_WITNESS_CORROBORATION two-witness agreements): a witness whose
    numbering agrees with the other one dozens of times has earned trust for
    the few rows the other witness missed, but a witness with ZERO agreements
    is unvalidated and its lone rows minted the only wrong markers of the
    round-1 import audit (RSOS/num2: Crossref deposited order != printed
    numbering while the PDF witness was blinded by 3-column garble).
    Returns (index_to_key, alignment table for the report/manifest)."""
    w1 = _witness_crossref(crossref_refs, bib)
    w2 = _witness_pdf_refsection(ref_tail, bib)
    if alt_ref_tail:
        # Same witness, alternative extraction (pdftotext -layout) — the
        # variant that reads MORE entries wins. Never counted as a third
        # witness: it's the same underlying reference section.
        w2b = _witness_pdf_refsection(alt_ref_tail, bib)
        if len(w2b) > len(w2):
            logger.info(f"PDF ref-section witness: -layout extraction read "
                        f"{len(w2b)} entries vs {len(w2)} plain — using -layout")
            w2 = w2b
    n_agree = sum(1 for n in set(w1) & set(w2) if w1[n] == w2[n])
    corroborated = n_agree >= MIN_SINGLE_WITNESS_CORROBORATION
    index_to_key: Dict[int, str] = {}
    table: List[Dict] = []
    for n in sorted(set(w1) | set(w2)):
        a, b = w1.get(n), w2.get(n)
        if a and b and a == b:
            index_to_key[n] = a
            table.append({"index": n, "key": a, "witnesses": "crossref+pdf"})
        elif a and b:
            table.append({"index": n, "key": None, "witnesses": "DISAGREE",
                          "crossref": a, "pdf": b})
        elif a or b:
            witness = "crossref-only" if a else "pdf-only"
            if corroborated:
                index_to_key[n] = a or b
                table.append({"index": n, "key": a or b, "witnesses": witness})
            else:
                table.append({"index": n, "key": None,
                              "witnesses": witness + "-uncorroborated",
                              "candidate": a or b})
    return index_to_key, table


# --------------------------------------------------------------------------
# Optional GROBID rung (Ladder C, best quality) — a local GROBID server turns
# the PDF into TEI XML with in-text <ref type="bibr" target="#bN"> anchors
# ALREADY linked to <biblStruct> bibliography entries, bypassing the citation
# recognizers and the numeric alignment entirely. NEVER a hard dependency: no
# GROBID_URL (env, or config/grobid_url.txt) => silent fall-through to the
# pure-Python path. Run one with:
#   docker run --rm -p 8070:8070 lfoppiano/grobid:0.8.1
# --------------------------------------------------------------------------

_TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}
# ⟦b3|Smith, 2020⟧ — the biblStruct id plus the citation's visible text, so an
# UNRESOLVED reference can be restored to its original prose instead of vanishing.
_GROBID_PLACEHOLDER_RE = re.compile(r"⟦(b\d+)\|[^⟧]*⟧")


def _grobid_url() -> Optional[str]:
    url = os.environ.get("GROBID_URL")
    if url:
        return url.rstrip("/")
    cfg = os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "config", "grobid_url.txt")
    try:
        if os.path.exists(cfg):
            with open(cfg, "r", encoding="utf-8") as fh:
                url = fh.read().strip()
                return url.rstrip("/") if url else None
    except Exception:
        pass
    return None


def _fetch_grobid_tei(pdf_path: str) -> Optional[str]:
    """POST the PDF to a configured GROBID server; None when no server is
    configured/reachable (the caller falls through silently)."""
    url = _grobid_url()
    if not url:
        return None
    import requests
    try:
        with open(pdf_path, "rb") as fh:
            resp = requests.post(f"{url}/api/processFulltextDocument",
                                 files={"input": fh}, timeout=300)
        resp.raise_for_status()
        logger.info("GROBID fulltext parse OK (%d bytes of TEI)", len(resp.text))
        return resp.text
    except Exception as e:
        logger.warning(f"GROBID server at {url} unusable ({e}) — "
                       f"falling back to plain PDF extraction")
        return None


def parse_grobid_tei(tei_xml: str) -> Tuple[List[str], List[str], Dict[str, str]]:
    """TEI -> (body paragraphs with ⟦bN⟧ citation placeholders,
               abstract paragraphs, {bN: bibliography-entry blob text}).
    Front matter/captions/references are excluded by TEI structure — no
    heading heuristics needed on this path."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(tei_xml)

    def para_text(p) -> str:
        parts = [p.text or ""]
        for child in p:
            tgt = (child.get("target") or "").lstrip("#")
            if child.tag.endswith("ref") and child.get("type") == "bibr" and tgt:
                visible = "".join(child.itertext()).strip().replace("⟧", "")
                parts.append(f"⟦{tgt}|{visible}⟧")
            else:
                parts.append("".join(child.itertext()))
            parts.append(child.tail or "")
        return re.sub(r"\s+", " ", "".join(parts)).strip()

    paragraphs = [para_text(p)
                  for p in root.findall(".//tei:body//tei:p", _TEI_NS)]
    paragraphs = [p for p in paragraphs if p]
    abstract = [para_text(p)
                for p in root.findall(".//tei:profileDesc//tei:abstract//tei:p",
                                      _TEI_NS)]
    bibl: Dict[str, str] = {}
    for bs in root.findall(".//tei:listBibl/tei:biblStruct", _TEI_NS):
        bid = bs.get("{http://www.w3.org/XML/1998/namespace}id")
        if bid:
            bibl[bid] = re.sub(r"\s+", " ", " ".join(bs.itertext())).strip()
    return paragraphs, abstract, bibl


class GrobidRefRecognizer:
    """Resolves the ⟦bN⟧ placeholders parse_grobid_tei leaves in the text.
    Each bN's biblStruct blob was closed-set matched against the DATABASE
    bibliography beforehand; unmatched ids stay unresolved (reported)."""

    name = "grobid"

    def __init__(self, id_to_key: Dict[str, str]):
        self.id_to_key = id_to_key

    def scan(self, text: str) -> Tuple[List[Citation], List[Dict]]:
        citations: List[Citation] = []
        unresolved: List[Dict] = []
        for m in re.finditer(r"⟦(b\d+)\|([^⟧]*)⟧", text):
            key = self.id_to_key.get(m.group(1))
            if key:
                citations.append(Citation(m.start(), m.end(), [key]))
            else:
                unresolved.append({"mention": m.group(2) or m.group(1),
                                   "why": "biblstruct_not_in_db_bibliography"})
        return citations, unresolved

    def find_citations(self, text: str) -> List[Citation]:
        return self.scan(text)[0]


def _restore_grobid_placeholders(text: str) -> str:
    """Unresolved ⟦bN|visible⟧ tokens revert to their original citation text —
    the prose stays honest about a citation we couldn't resolve."""
    return re.sub(r"⟦b\d+\|([^⟧]*)⟧", r"\1", text)


# --------------------------------------------------------------------------
# Body trimming (PDF text -> the prose worth verifying)
# --------------------------------------------------------------------------

_REFS_HEADING_RE = re.compile(
    r"^\s*(?:\d+\.?\s*)?(references|bibliography|literature cited|works cited)\s*\.?\s*$",
    re.IGNORECASE)
_INTRO_HEADING_RE = re.compile(
    r"^\s*(?:\d+\.?|I\.)?\s*introduction\b[^a-z]*$", re.IGNORECASE)
_ABSTRACT_RE = re.compile(r"^\s*abstract\b", re.IGNORECASE)
_BACKMATTER_RE = re.compile(
    r"^\s*(?:\d+\.?\s*)?(acknowledg(?:e)?ments?|funding|author contributions?|"
    r"declarations?|conflicts? of interest|competing interests|"
    r"data availability(?: statement)?|ethics(?: statement)?|"
    r"supplementary (?:material|information))\s*:?\s*$", re.IGNORECASE)
_APPENDIX_RE = re.compile(r"^\s*appendix(?:\s+[A-Z0-9])?\b", re.IGNORECASE)
_CAPTION_RE = re.compile(r"^\s*(figure|fig\.?|table)\s+\d+\s*[.:|]", re.IGNORECASE)


def trim_body(text: str, keep_abstract: bool = False,
              keep_appendix: bool = False) -> Tuple[str, str, List[str]]:
    """Split raw extracted paper text into (body, reference_section_text,
    stripped-sections report). Body = Introduction..Conclusion by default."""
    lines = text.splitlines()
    stripped: List[str] = []

    # References heading: LAST match wins ("references" can occur in prose).
    refs_at = None
    for i, ln in enumerate(lines):
        if _REFS_HEADING_RE.match(ln):
            refs_at = i
    ref_tail = "\n".join(lines[refs_at + 1:]) if refs_at is not None else ""
    if refs_at is not None:
        stripped.append("references section")
        lines = lines[:refs_at]

    # Front matter (journal header, authors, affiliations, abstract): cut at the
    # Introduction heading when one exists near the top.
    intro_at = next((i for i, ln in enumerate(lines[:150])
                     if _INTRO_HEADING_RE.match(ln)), None)
    if intro_at is not None:
        front = lines[:intro_at]
        lines = lines[intro_at:]
        stripped.append("front matter (title/authors/affiliations)")
        if keep_abstract:
            abs_at = next((i for i, ln in enumerate(front) if _ABSTRACT_RE.match(ln)),
                          None)
            if abs_at is not None:
                lines = front[abs_at:] + [""] + lines
                stripped.remove("front matter (title/authors/affiliations)")
                stripped.append("front matter (abstract kept)")
        elif any(_ABSTRACT_RE.match(ln) for ln in front):
            stripped.append("abstract")
    elif not keep_abstract:
        abs_at = next((i for i, ln in enumerate(lines[:80]) if _ABSTRACT_RE.match(ln)),
                      None)
        if abs_at is not None:
            # No intro heading to anchor on: drop the abstract paragraph only.
            j = abs_at + 1
            while j < len(lines) and lines[j].strip():
                j += 1
            lines = lines[:abs_at] + lines[j:]
            stripped.append("abstract")

    # Backmatter / appendix in the tail 40%: truncate at the earliest heading.
    floor = int(len(lines) * 0.6)
    cut = None
    for i in range(floor, len(lines)):
        if _BACKMATTER_RE.match(lines[i]) or \
                (not keep_appendix and _APPENDIX_RE.match(lines[i])):
            cut = i
            break
    if cut is not None:
        name = lines[cut].strip()
        lines = lines[:cut]
        stripped.append(f"backmatter from '{name}'")

    n_captions = sum(1 for ln in lines if _CAPTION_RE.match(ln))
    if n_captions:
        lines = [ln for ln in lines if not _CAPTION_RE.match(ln)]
        stripped.append(f"{n_captions} figure/table caption line(s)")

    return "\n".join(lines), ref_tail, stripped


def _reflow(text: str) -> str:
    """Join hard-wrapped PDF lines into one line per paragraph (blank-line
    separated) — the claim splitter and sentence detector both want prose,
    and de-hyphenate words broken across line ends."""
    paras = re.split(r"\n\s*\n", text)
    out = []
    for p in paras:
        joined = re.sub(r"-\n(?=[a-z])", "", p)          # hyphenated line break
        joined = re.sub(r"\s*\n\s*", " ", joined).strip()
        # Footnote-marker glue (ml_who_to_nudge, 2026-07-12: 13 sightings):
        # pdftotext renders a superscript footnote digit flush against the
        # sentence-final period — "as follows.3 First, the algorithm ..." —
        # which garbles claim splitting and evidence matching. Requires a
        # LETTER before the period (never touches decimals like "0.3 percent"
        # or versions like "3.9") and a capitalized next sentence.
        joined = re.sub(r"(?<=[a-zA-Z])\.(\d{1,2})\s+(?=[A-Z“\"])", ". ", joined)
        if joined:
            out.append(joined)
    return "\n\n".join(out)


# --------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------

def run_paper_import(output_dir: str, pdf: Optional[str] = None,
                     doi: Optional[str] = None, arxiv: Optional[str] = None,
                     url: Optional[str] = None, title: Optional[str] = None,
                     keep_abstract: bool = False, keep_appendix: bool = False,
                     fetchers: Optional[Dict] = None) -> Dict:
    """Import one published paper. Writes the four INPUT_FORMAT.md artifacts +
    import_report.md into output_dir; returns the summary dict."""
    f = fetchers or _default_fetchers()

    record = identify(pdf=pdf, doi=doi, arxiv=arxiv, url=url, title=title, fetchers=f)

    db_refs = f["neighbors"](record["paper_id"], "references",
                             cache_dir=os.path.join(output_dir, "cache"))
    ref_source = (db_refs[0].get("source") if db_refs else None)
    if not db_refs:
        raise PaperImportError(
            f"No reference list found in S2/OpenAlex for {record['paper_id']} — "
            "the paper may be unindexed; use the /import-paper command")
    bib, ay_index = build_bibliography(db_refs)

    # F7a — bibliography union, AUTHOR-YEAR SCOPE ONLY: fold structured
    # Crossref-deposited works the S2/OpenAlex list is missing into a union
    # bib for the author-year resolver. Numeric alignment keeps the pristine
    # DB-only bib: near-duplicate twins with divergent metadata (same work,
    # different year/title strings) poison its closed-set blob matching —
    # observed live as a num1 marker regression when the union fed both.
    crossref_refs = f["crossref_refs"](record["doi"]) if record.get("doi") else None
    extra_refs = _crossref_only_refs(crossref_refs, db_refs)
    bib_union = bib
    if extra_refs:
        logger.info(f"Bibliography union (author-year scope): "
                    f"+{len(extra_refs)} structured work(s) from Crossref "
                    f"missing from the {ref_source} list")
        bib_union, ay_index = build_bibliography(db_refs + extra_refs)

    if not pdf:
        pdf = f["fetch_target_pdf"](record, output_dir)
        if not pdf:
            raise PaperImportError(
                "No open-access PDF of the paper could be fetched — "
                "download it yourself and pass --pdf")
    # Ladder C rung 1 — GROBID (optional, best quality): TEI paragraphs with
    # citation anchors pre-linked to bibliography entries; the recognizers and
    # numeric alignment below are bypassed. Silent fall-through when no server.
    alignment = None
    recognizer = None
    ay_n = num_n = 0
    tei = f.get("grobid_tei", _fetch_grobid_tei)(pdf)
    if tei:
        try:
            paragraphs, abstract_paras, tei_bibl = parse_grobid_tei(tei)
        except Exception as e:
            logger.warning(f"GROBID TEI unparseable ({e}) — using plain extraction")
            paragraphs, tei_bibl = [], {}
        if paragraphs and tei_bibl:
            id_to_key = {}
            for bid, blob in tei_bibl.items():
                key = _match_blob_to_bib(blob, bib)
                if key:
                    id_to_key[bid] = key
            body = "\n\n".join(
                (abstract_paras if keep_abstract else []) + paragraphs)
            recognizer = GrobidRefRecognizer(id_to_key)
            stripped = ["TEI-structured via GROBID (front matter/captions/"
                        "references excluded structurally)"]
            logger.info(f"GROBID path: {len(paragraphs)} paragraphs, "
                        f"{len(id_to_key)}/{len(tei_bibl)} bibliography entries "
                        f"joined to the database list")

    if recognizer is None:
        # Ladder C rung 2 — plain PDF text + style detection: run both
        # recognizers over the whole body, the one that RESOLVES more citation
        # groups wins (raw regex hits would let equation brackets outvote a
        # real author-year style).
        raw = "\n".join(f["pages_reader"](pdf))
        if not raw.strip():
            raise PaperImportError(f"No text extractable from {pdf} (scanned image?)")
        body, ref_tail, stripped = trim_body(raw, keep_abstract, keep_appendix)
        body = _reflow(body)
        alt_tail = ""
        if pdf:
            layout_raw = f.get("layout_text", _pdftotext_layout_text)(pdf)
            if layout_raw.strip():
                alt_tail = _layout_ref_tail(layout_raw)
        index_to_key, alignment = align_numeric(bib, crossref_refs, ref_tail,
                                                alt_ref_tail=alt_tail)
        ay_rec = AuthorYearRecognizer(ay_index)
        num_rec = NumericBracketRecognizer(index_to_key)
        ay_n = len(ay_rec.find_citations(body))
        num_n = len(num_rec.find_citations(body))
        recognizer = num_rec if num_n > ay_n else ay_rec
        if recognizer is ay_rec:
            bib = bib_union   # author-year won -> manifest carries union keys
        style = "undetermined" if ay_n == num_n == 0 else recognizer.name
        logger.info(f"Citation style: {style} "
                    f"(author-year resolved {ay_n}, numeric resolved {num_n})")
    converted_parts: List[str] = []
    cited_keys: List[str] = []
    unresolved_mentions: List[Dict] = []
    n_citations = 0
    for part in re.split(r"(\n\s*\n)", body):        # keep separators (layout)
        if part.strip():
            cits, unres = recognizer.scan(part)
            for u in unres:
                sent = part[:300]
                u["paragraph"] = (sent[:200] + "…") if len(sent) > 200 else sent
            unresolved_mentions.extend(unres)
            n_citations += len(cits)
            conv, keys = convert_block(part, recognizer)
            converted_parts.append(conv)
            cited_keys.extend(keys)
        else:
            converted_parts.append(part)
    converted = "".join(converted_parts)
    if recognizer.name == "grobid":
        converted = _restore_grobid_placeholders(converted)
    unique_keys = list(dict.fromkeys(cited_keys))

    # Sanity tripwire: a defeated citation style (superscripts, footnotes)
    # resolves almost nothing — say so loudly rather than emit a hollow project.
    coverage = len(unique_keys) / max(len(bib), 1)
    if not unique_keys:
        raise PaperImportError(
            "No citations resolved against the database reference list "
            f"({len(bib)} entries; author-year matched {ay_n}, numeric matched "
            f"{num_n}). {_style_guess(body)} Superscript/footnote/(n) styles "
            "and unaligned numbering need the /import-paper command.")

    manifest_extra = {
        "input_pdf": os.path.abspath(pdf),
        "paper": {k: record[k] for k in ("paper_id", "doi", "arxiv_id", "title",
                                         "year", "id_evidence")},
        "citation_syntax": recognizer.name,
        "reference_list_source": ref_source,
    }
    if recognizer.name == "numeric":
        manifest_extra["numeric_alignment"] = alignment
    summary = write_artifacts(output_dir, converted, unique_keys, bib,
                              manifest_extra=manifest_extra)
    summary.update({"citations_converted": n_citations,
                    "citation_syntax": recognizer.name,
                    "unresolved_mentions": unresolved_mentions,
                    "coverage": round(coverage, 2), "stripped": stripped,
                    "paper": record,
                    "report": _write_report(
                        output_dir, record, ref_source, bib, summary, n_citations,
                        unresolved_mentions, coverage, stripped,
                        alignment if recognizer.name == "numeric" else None)})
    return summary


def _write_report(output_dir: str, record: Dict, ref_source: Optional[str],
                  bib: Dict, summary: Dict, n_citations: int,
                  unresolved: List[Dict], coverage: float,
                  stripped: List[str], alignment: Optional[List[Dict]] = None) -> str:
    """import_report.md — what was identified/resolved/stripped, what wasn't."""
    lines = ["# Import report", "",
             f"**Paper:** {record.get('title') or record['paper_id']} "
             f"({record.get('year') or '?'})",
             f"**Identified via:** {record.get('id_evidence')}",
             f"**Reference list:** {len(bib)} entries from "
             f"{ref_source or 'database'} (database-first — the PDF's own "
             f"reference section was not parsed for bibliography data)", "",
             f"**Citations converted:** {n_citations} occurrences → "
             f"{summary['unique_keys']} unique sources "
             f"({coverage:.0%} of the reference list cited in the text)", ""]
    if coverage < 0.30:
        lines += ["> ⚠ **Under 30% of the reference list resolved in the text.** "
                  "Common causes: a citation style that defeats the importer "
                  "(superscripts/footnotes), or an OA copy that is an EARLIER "
                  "working-paper version whose citations differ from the published "
                  "reference list the databases index. Eyeball my_text.md before "
                  "running the verifier, or use the /import-paper command.", ""]
    if alignment is not None:
        n_two = sum(1 for a in alignment if a["witnesses"] == "crossref+pdf")
        n_one = sum(1 for a in alignment if a["witnesses"] in
                    ("crossref-only", "pdf-only"))
        n_unc = sum(1 for a in alignment
                    if a["witnesses"].endswith("-uncorroborated"))
        n_dis = sum(1 for a in alignment if a["witnesses"] == "DISAGREE")
        lines += ["## Numeric alignment (two-witness: Crossref order vs PDF "
                  "reference section)", "",
                  f"{n_two} agreed · {n_one} single-witness · "
                  f"{n_unc} single-witness-uncorroborated (left unmapped) · "
                  f"{n_dis} disagreed (left unmapped)", "",
                  "| [n] | key | witnesses |", "|---|---|---|"]
        for a in alignment:
            if a["witnesses"] == "DISAGREE":
                lines.append(f"| {a['index']} | — | DISAGREE "
                             f"(crossref: {a['crossref']}, pdf: {a['pdf']}) |")
            elif a["witnesses"].endswith("-uncorroborated"):
                lines.append(f"| {a['index']} | — | {a['witnesses']} "
                             f"(candidate: {a['candidate']}; needs "
                             f"{MIN_SINGLE_WITNESS_CORROBORATION}+ two-witness "
                             "agreements elsewhere to be trusted) |")
            else:
                lines.append(f"| {a['index']} | {a['key']} | {a['witnesses']} |")
        lines.append("")
    if unresolved:
        lines += [f"## Unresolved citations ({len(unresolved)}) — left unmarked, "
                  "never guessed", ""]
        for u in unresolved:
            lines.append(f"- `{u['mention']}` — {u['why']}"
                         + (f"\n  - context: {u['paragraph']}" if u.get("paragraph")
                            else ""))
        lines.append("")
    if stripped:
        lines += ["## Stripped sections", ""] + [f"- {s}" for s in stripped] + [""]
    uncited = summary.get("uncited_bibliography_keys") or []
    if uncited:
        lines += [f"*{len(uncited)} bibliography entr(ies) never resolved in the "
                  "text (uncited_bibliography_keys in the manifest).*", ""]
    lines += ["## Next steps", "",
              "1. `python3 download_sources.py --manifest "
              f"{os.path.join(output_dir, 'sources_manifest.json')}`",
              "2. `venv/bin/python3 verify_my_text.py --text "
              f"{os.path.join(output_dir, 'my_text.md')} --sources "
              f"{os.path.join(output_dir, 'sources')} --output-dir "
              f"{output_dir.rstrip('/')}_verification --backend claude-code`", ""]
    path = os.path.join(output_dir, "import_report.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path
