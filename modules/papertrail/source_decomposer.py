"""
Stage 1 (PaperTrail): decompose each cited source into atomic claims, and link
each claim to its supporting evidence sentence(s) via SPECTER cosine >= 0.75.

Results are cached to disk keyed by the source file's content hash, so re-running
on unchanged sources makes NO new LLM calls.
"""

import os
import re
import json
import hashlib
import logging
from typing import List, Dict, Any, Optional

from . import embeddings

logger = logging.getLogger(__name__)

EVIDENCE_LINK_THRESHOLD = 0.75   # PaperTrail Stage-1 evidence linking
_CHUNK_WORD_TARGET = 1200        # group paragraphs into chunks to limit LLM calls
CACHE_SCHEMA = 8                 # bump to invalidate caches when the stored shape changes
                                 # (8: rebuild sentence index with the et-al abbrev merge +
                                 #  line-break-split pdftotext swap — t8/t11/t18)
                                 # (7: re-index sentences so the space-collapse pdftotext
                                 # fallback de-garbles cached evidence, e.g. mcnamara1987)
                                 # (6: web-boilerplate line filter on .txt reads — the
                                 # upgrade path rebuilds the sentence index, no LLM)
                                 # (4: blob-guard + fragment-merge segmentation; 5: pdftotext
                                 # fallback for letter-spaced PyPDF2 garble. The upgrade path
                                 # re-splits sentences with NO LLM calls — note the cached
                                 # CLAIMS of a previously-garbled source stay garble-derived
                                 # until a real re-decomposition.)


# ---------- source reading ----------

# download_sources.py prepends "Source URL: <url>\n\n---\n\n" to every .txt source
# it fetches. That metadata header is not content, but the full-text evidence path
# could otherwise chunk it and show the URL line as a claim's "evidence" (cosmetic,
# never flips a verdict, but erodes trust in the shown snippet). Strip the leading
# block here, at the single read boundary, so it never enters the sentence index or
# a fresh decomposition. Cache keys are the raw file BYTES (file_hash / file_sha1),
# so stripping the READ text leaves every disk cache valid.
_TXT_PREAMBLE_RE = re.compile(r"\ASource URL:.*?\n\s*---\s*\n+", re.DOTALL)


def _strip_txt_preamble(text: str) -> str:
    return _TXT_PREAMBLE_RE.sub("", text, count=1)


def _looks_letter_spaced(text: str, sample_chars: int = 4000) -> bool:
    """PyPDF2 sometimes extracts a PDF's text layer one glyph per token
    ("M e a s u r i n g t h e P e r s u a s i v e n e s s ..." — the
    anthropic2024/macaskill2025 class). Normal English runs ~2-5% single-letter
    tokens; garbled extractions run well past half."""
    tokens = text[:sample_chars].split()
    if len(tokens) < 40:
        return False
    singles = sum(1 for t in tokens if len(t) == 1 and t.isalpha())
    return singles / len(tokens) > 0.35


def _looks_space_collapsed(text: str, sample_chars: int = 8000) -> bool:
    """PyPDF2 sometimes drops the spaces BETWEEN words on certain PDFs, gluing
    short words together ("In69%ofthestudies thesubjects compensated forthe..." —
    the mcnamara1987 scanned-PDF class). The inverse of _looks_letter_spaced.

    Signal is the MEAN alpha-token length: normal English runs ~4.5–6.5; this
    class runs ~8–15 (spaces dropped after the short function words while many
    survive). A single long domain term ("montmorillonite", "vulnerabilities")
    does NOT move the mean, so technical prose is safe. The UPPER bound excludes a
    different, more severe failure — near-total space loss (mean well past 20,
    e.g. an extraction that returns a handful of giant tokens per page); that is
    not auto-swapped here because a wholesale text replacement would need a fresh
    ground-truth audit (tracked separately)."""
    tokens = text[:sample_chars].split()
    if len(tokens) < 40:
        return False
    lengths = [len(re.sub(r"[^A-Za-z]", "", t)) for t in tokens]
    lengths = [n for n in lengths if n]
    if not lengths:
        return False
    mean_len = sum(lengths) / len(lengths)
    return 8.0 <= mean_len < 20.0


# A LOCALIZED space collapse: most of the page reads fine (so the whole-doc mean in
# _looks_space_collapsed stays normal) but a stretch of it glues into one giant run,
# e.g. vincent2019 "tdescribedthedataacrossthefullspectrumofdietarycholesterol".
# The longest real English words top out near 20-22 letters (even the rare technical
# ones); a 25+-letter alpha run is a glued sentence, not a word. Whole-doc detectors
# miss this because a handful of monster tokens don't move the mean.
_GLUED_RUN_RE = re.compile(r"[A-Za-z]{25,}")


def _count_glued_runs(text: str, sample_chars: int = 20000) -> int:
    return len(_GLUED_RUN_RE.findall(text[:sample_chars]))


def _looks_locally_glued(text: str) -> bool:
    return _count_glued_runs(text) > 0


# Intra-word line-break garble (the drouinchartier2020/t18 class): PyPDF2 splits
# words across a wrapped line into a stray leading letter + the remainder
# ("p articipants", "r esults", "cardi\nova"). Neither whole-doc detector fires
# (the doc is mostly fine). Signal: standalone single-CONSONANT tokens — real
# single-letter English words are only a/A/I/O, so a lone "p"/"r"/"d" is a
# line-break artifact. Clean academic PDFs sit at <=0.008; the garbled ones run
# higher. Threshold 0.011 separates drouinchartier (0.013) from clean (vincent
# 0.008). Only ever acted on through the GUARDED swap below (pdftotext must
# reduce it), so a stray false trigger just wastes one pdftotext call.
_LINEBREAK_CONS_THRESH = 0.011
_CONSONANTS = set("bcdfghjklmnpqrstvwxyz")


def _single_consonant_frac(text: str, sample_chars: int = 20000) -> float:
    toks = text[:sample_chars].split()
    if len(toks) < 40:
        return 0.0
    n = sum(1 for t in toks if len(t) == 1 and t.lower() in _CONSONANTS)
    return n / len(toks)


def _looks_linebreak_split(text: str) -> bool:
    return _single_consonant_frac(text) > _LINEBREAK_CONS_THRESH


def _pdftotext_pages(path: str) -> Optional[List[str]]:
    """Per-page text via poppler's pdftotext (pages arrive form-feed-separated);
    None when the binary is missing or extraction fails."""
    import shutil
    import subprocess
    if not shutil.which("pdftotext"):
        return None
    try:
        out = subprocess.run(["pdftotext", "-enc", "UTF-8", path, "-"],
                             capture_output=True, timeout=120)
        if out.returncode != 0:
            return None
        text = out.stdout.decode("utf-8", errors="ignore")
        if not text.strip():
            return None
        pages = text.split("\f")
        if pages and not pages[-1].strip():   # pdftotext ends every page with \f
            pages.pop()
        return pages or None
    except Exception as e:
        logger.warning(f"pdftotext fallback failed for {path}: {e}")
        return None


def read_source_pages(path: str) -> List[str]:
    """Per-page text for PDFs (via PyPDF2); a single-element list for plain text.

    Page boundaries are what let the viewer jump the PDF to the right page, so we keep
    each page separate here (read_source_file joins them for the rest of the pipeline).
    """
    if path.lower().endswith(".pdf"):
        try:
            from PyPDF2 import PdfReader
            with open(path, "rb") as f:
                reader = PdfReader(f)
                pages = [(page.extract_text() or "") for page in reader.pages]
        except Exception as e:
            logger.warning(f"Failed to read PDF pages {path}: {e}")
            return []
        joined = "\n".join(pages)
        letter, collapsed = _looks_letter_spaced(joined), _looks_space_collapsed(joined)
        glued_runs = _count_glued_runs(joined)
        cons_frac = _single_consonant_frac(joined)
        linebreak = cons_frac > _LINEBREAK_CONS_THRESH
        if letter or collapsed or glued_runs or linebreak:
            kind = ("letter-spaced" if letter else
                    "space-collapsed" if collapsed else
                    "locally-glued" if glued_runs else "line-break-split")
            alt = _pdftotext_pages(path)
            if alt:
                alt_joined = "\n".join(alt)
                clean = (not _looks_letter_spaced(alt_joined)
                         and not _looks_space_collapsed(alt_joined))
                # The localized detectors (glue, line-break) already pass the
                # whole-doc tests, so require the fallback to actually REDUCE the
                # signal that fired before swapping — never trade PyPDF2 for a
                # differently-broken pdftotext (e.g. a multi-column reflow).
                better_glue = _count_glued_runs(alt_joined) < glued_runs
                better_cons = _single_consonant_frac(alt_joined) < cons_frac
                if clean and (letter or collapsed
                              or (glued_runs and better_glue)
                              or (linebreak and better_cons)):
                    logger.warning(f"{os.path.basename(path)}: PyPDF2 text is {kind} "
                                   "garble — using the pdftotext extraction instead")
                    return alt
            logger.warning(f"{os.path.basename(path)}: extracted text looks {kind}; "
                           "install poppler-utils (pdftotext) for a clean fallback")
        return pages
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
    except Exception as e:
        logger.warning(f"Failed to read text file {path}: {e}")
        return []
    if _TXT_PREAMBLE_RE.match(raw):
        # The "Source URL:" preamble marks a downloader-saved WEB PAGE — filter
        # bylines/date stamps/photo credits/headline dumps that leaked past the
        # DOM-level extraction (todo item 8). Hand-supplied .txt files (no
        # preamble) are left untouched. Cache keys are the raw file bytes, so
        # filtering the READ text leaves disk caches valid; CACHE_SCHEMA 6
        # rebuilds existing sentence indexes from this cleaned read (no LLM).
        from .webtext import drop_boilerplate_lines
        return [drop_boilerplate_lines(_strip_txt_preamble(raw))]
    return [_strip_txt_preamble(raw)]


def read_source_file(path: str) -> str:
    """Read a source document's full text (.pdf via PyPDF2, otherwise plain text)."""
    return "\n".join(read_source_pages(path))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _page_for_sentence(sentence: str, pages_norm: List[str]) -> Optional[int]:
    """1-based page whose (normalized) text contains the sentence; None if not found.

    Falls back to a short prefix probe so a sentence that straddles a page break (or
    differs slightly from the per-page extraction) still resolves to a page.
    """
    s = _norm(sentence)
    if not s:
        return None
    probes = [s]
    words = s.split()
    if len(words) > 6:
        probes.append(" ".join(words[:6]))
    for probe in probes:
        for i, pg in enumerate(pages_norm):
            if probe in pg:
                return i + 1
    return None


def file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


# ---------- text segmentation ----------

_MAX_SENT_CHARS = 600     # punkt output above this = segmentation failure (blob guard)
_FRAGMENT_MAX_WORDS = 4   # a "sentence" this short is a fragment (table row, list stub)
_FRAGMENT_MAX_CHARS = 60  # ...but only if it's also short in chars — spaceless PDF text
                          # ("Wewillsoonlive...") has few spaces and must not count
_MERGE_MAX_WORDS = 50     # caps for one merged fragment block
_MERGE_MAX_CHARS = 400


def sentence_split(text: str) -> List[str]:
    """Sentence-split with NLTK punkt (regex fallback), plus two structural guards
    for text punkt can't segment (found on the paper1 audit, t31/t6):
    - blob guard: unpunctuated bullet/list lines (policy pages, ToCs) collapse into
      one huge "sentence" once whitespace is flattened — any output longer than
      _MAX_SENT_CHARS is re-split at the original newlines it swallowed;
    - fragment merge: numbered tables split into 2-3 word rows ("USA, 74.5% 2.") —
      runs of consecutive fragments merge into one block, so evidence extraction
      and judging see the whole row group instead of unusable stubs.
    """
    collapsed, offsets = _collapse_ws(text)
    if not collapsed:
        return []
    out = []
    for s in _punkt(collapsed):
        if len(s) <= _MAX_SENT_CHARS:
            out.append(s)
        else:
            out.extend(_split_blob(s, collapsed, offsets, text))
    return _merge_fragments(out)


# Academic abbreviations that end in a period but NOT a sentence — punkt (and the
# regex fallback) wrongly break after them, producing fragments like "Studies by
# Clarkson et al." (mcnamara/t8) and "[29] examined …" (blesso/t11, the author name
# split off at "al."). Merge the fragment back onto the next piece, but ONLY when
# that next piece continues the sentence (starts lowercase / digit / open-bracket) —
# so a real sentence that happens to end in "etc." or "No." is never swallowed.
_ABBREV_END_RE = re.compile(
    r"(?:\bet\s+al|\bal|\be\.?g|\bi\.?e|\bcf|\bvs|\bviz|\bibid|\bfigs?|\bno|\bpp|\bvol"
    r"|\beq|\bref|\bapprox|\bdr|\bprof|\bmr|\bmrs|\bms|\bst|\bjr|\bsr)\.$", re.I)
_CONTINUES_RE = re.compile(r"^[a-z0-9(\[]")


def _merge_abbrev(sents: List[str]) -> List[str]:
    out: List[str] = []
    for s in sents:
        if (out and _ABBREV_END_RE.search(out[-1]) and _CONTINUES_RE.match(s)):
            out[-1] = out[-1] + " " + s
        else:
            out.append(s)
    return out


def _punkt(text: str) -> List[str]:
    try:
        import nltk
        sents = [s.strip() for s in nltk.sent_tokenize(text) if s.strip()]
    except Exception:
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    return _merge_abbrev(sents)


def _collapse_ws(text: str):
    """Collapse whitespace runs to single spaces, keeping a map from each collapsed
    index back to an original index (so the blob guard can recover newlines)."""
    chars, offsets = [], []
    pending = False
    for i, ch in enumerate(text):
        if ch.isspace():
            pending = bool(chars)
            continue
        if pending:
            chars.append(" ")
            offsets.append(i - 1)
            pending = False
        chars.append(ch)
        offsets.append(i)
    return "".join(chars), offsets


def _split_blob(sent: str, collapsed: str, offsets: List[int], original: str) -> List[str]:
    """Re-split an oversized punkt 'sentence' at the original newlines inside it."""
    p = collapsed.find(sent)
    if p == -1:                        # can't locate (shouldn't happen) — wrap blindly
        return _hard_wrap(sent)
    start, end = offsets[p], offsets[p + len(sent) - 1] + 1
    pieces = []
    for line in original[start:end].split("\n"):
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            pieces.extend([line] if len(line) <= _MAX_SENT_CHARS else _hard_wrap(line))
    return pieces or [sent]


def _hard_wrap(s: str, target: int = 300) -> List[str]:
    """Last resort for one giant line with no punctuation and no newlines: break
    at word boundaries near `target` chars so pieces stay usable as evidence.
    A single 'word' longer than target (spaceless PDF text layers) is cut at
    fixed char offsets — ugly, but the alternative is an unusable multi-KB blob."""
    out, cur = [], ""
    for word in s.split(" "):
        while len(word) > target:
            if cur:
                out.append(cur)
                cur = ""
            out.append(word[:target])
            word = word[target:]
        if cur and len(cur) + 1 + len(word) > target:
            out.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}" if cur else word
    if cur:
        out.append(cur)
    return out


_COMPLETE_SENT_RE = re.compile(r'[.!?]["\')\]]?$')
# Ranked-list artifact: the row ends with the NEXT row's standalone 1-2 digit
# list number ("USA, 74.5% 2."). Must NOT match a genuine short sentence that
# happens to end in a number ("Sales fell in 2020." / "The rate fell by 4.5."),
# so the number needs a leading boundary and at most two digits.
_LIST_NUM_RE = re.compile(r"(?:^|\s)\d{1,2}\.$")


def _is_fragment(s: str) -> bool:
    """Table rows / list stubs — short AND not a complete sentence. A short
    genuine sentence ('He agreed.') ends with terminal punctuation and must NOT
    be merged; a ranked-table row ends with the NEXT row's list number ('2.')."""
    if len(s.split()) > _FRAGMENT_MAX_WORDS or len(s) > _FRAGMENT_MAX_CHARS:
        return False
    return not _COMPLETE_SENT_RE.search(s) or bool(_LIST_NUM_RE.search(s))


def _merge_fragments(sents: List[str]) -> List[str]:
    """Merge runs of >=2 consecutive fragments; a lone fragment stays as-is
    (the matcher's ±1 evidence window already handles isolated short lines)."""
    out, run = [], []
    for s in sents:
        if _is_fragment(s):
            run.append(s)
        else:
            out.extend(_merged(run))
            run = []
            out.append(s)
    out.extend(_merged(run))
    return out


def _merged(run: List[str]) -> List[str]:
    if len(run) < 2:
        return run
    out, cur, words, chars = [], [], 0, 0
    for s in run:
        w = len(s.split())
        if cur and (words + w > _MERGE_MAX_WORDS or chars + len(s) > _MERGE_MAX_CHARS):
            out.append(" ".join(cur))
            cur, words, chars = [], 0, 0
        cur.append(s)
        words += w
        chars += len(s) + 1
    if cur:
        out.append(" ".join(cur))
    return out


def _chunk_paragraphs(text: str) -> List[str]:
    """Group paragraphs into ~_CHUNK_WORD_TARGET-word chunks to limit LLM calls."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) <= 1:
        # No paragraph structure (common in PDF extraction): chunk by sentences.
        sents = sentence_split(text)
        paras = [" ".join(sents[i:i + 6]) for i in range(0, len(sents), 6)]
    chunks, cur, cur_words = [], [], 0
    for p in paras:
        w = len(p.split())
        if cur and cur_words + w > _CHUNK_WORD_TARGET:
            chunks.append("\n\n".join(cur))
            cur, cur_words = [], 0
        cur.append(p)
        cur_words += w
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


# ---------- decomposition ----------

# Model-agnostic junk filter: even a good decomposer occasionally emits a
# non-claim lifted straight from the source — a funding/COI line, a reference
# entry, a bare statistic fragment. These pollute the "unused points" panel and
# round-2 escalation (they can never legitimately support anything). Each pattern
# is high-precision and was validated against 5077 real eggs claims (drops ~1.3%,
# all genuine junk). NOTE: we deliberately DON'T drop short claims — "Eggs are
# affordable." is 3 words and perfectly valid; length is not a junk signal.
_JUNK_DOI = re.compile(r"\bdoi:\s*10\.\d{3,}|\bdoi\.org/10\.\d{3,}", re.I)
_JUNK_VOLPAGE = re.compile(r"\b(19|20)\d{2};\s*\d+\s*(\(\d+\))?\s*:\s*\d+")   # "2018;104:1756"
_JUNK_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_JUNK_BOILER = re.compile(r"\b(all rights reserved|protected by copyright|"
                          r"corresponding author|conflicts? of interest|"
                          r"declare(?:d|s)? (?:no|that|competing)|"
                          r"supported by (?:a |the )?grants?|funded by|grants? from|"
                          r"acknowledge?ments?|©)\b", re.I)
_JUNK_TABLE_LEAD = re.compile(r"^\s*(table|fig(?:ure)?|appendix|supplement(?:ary|al)?|scheme)\s*[0-9IVX]+\b", re.I)
_JUNK_STAT_LEAD = re.compile(r"^\s*[\(\[]?\s*(p\s*[<>=≤≥]|n\s*=\s*\d|95%\s*ci)", re.I)


def _is_junk_claim(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if _JUNK_DOI.search(t) or _JUNK_VOLPAGE.search(t):   # citation / reference entry
        return True
    if _JUNK_EMAIL.search(t):
        return True
    if _JUNK_BOILER.search(t):                            # funding / COI / copyright
        return True
    if _JUNK_TABLE_LEAD.search(t):                        # "Table 2 ...", "Figure 3 ..."
        return True
    if _JUNK_STAT_LEAD.search(t):                         # bare "P < 0.001 ..." fragment
        return True
    return False


def _load_prompt() -> str:
    path = os.path.join(embeddings_project_root(), "config", "prompts", "pt_extract_claims_prompt.txt")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def embeddings_project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _extract_claims_from_text(text: str, llm, workers: int = 1) -> List[str]:
    """Run the atomic-claim extraction prompt over text, return list of claim strings.
    Chunks are independent, so their LLM calls run through a thread pool (workers>1);
    parallel_map preserves chunk order, so the claim list is deterministic."""
    from .llm_client import parallel_map
    prompt_template = _load_prompt()

    def extract_chunk(chunk: str) -> List[str]:
        prompt = prompt_template.replace("{TEXT}", chunk)
        result = llm.call_json(prompt, temperature=0.1)
        if isinstance(result, list):
            return [c.strip() for c in result if isinstance(c, str) and c.strip()]
        return []

    claims: List[str] = []
    for chunk_claims in parallel_map(extract_chunk, _chunk_paragraphs(text), workers):
        claims.extend(chunk_claims)
    # De-duplicate while preserving order, dropping model-agnostic junk (citation
    # entries, funding/COI/copyright, table captions, bare-stat fragments).
    seen, unique, n_junk = set(), [], 0
    for c in claims:
        if _is_junk_claim(c):
            n_junk += 1
            continue
        key = c.lower()
        if key not in seen:
            seen.add(key)
            unique.append(c)
    if n_junk:
        logger.info(f"[decompose] dropped {n_junk} junk claim(s) "
                    f"(citation/boilerplate/table/stat fragments)")
    return unique


def _link_evidence(claims: List[str], sentences: List[str]) -> List[List[str]]:
    """For each claim, return up to 3 source sentences with cosine >= threshold (else best 1)."""
    if not claims or not sentences:
        return [[] for _ in claims]
    matrix = embeddings.cosine_matrix(claims, sentences)
    evidence = []
    for row in matrix:
        ranked = sorted(range(len(sentences)), key=lambda j: row[j], reverse=True)
        above = [sentences[j] for j in ranked if row[j] >= EVIDENCE_LINK_THRESHOLD][:3]
        if not above and ranked:
            above = [sentences[ranked[0]]]  # best-effort fallback
        evidence.append(above)
    return evidence


def _sentence_index(path: str) -> List[Dict[str, Any]]:
    """Full list of source sentences with the 1-based page each falls on (no LLM)."""
    pages = read_source_pages(path)
    pages_norm = [_norm(p) for p in pages]
    sentences = sentence_split("\n".join(pages))
    return [{"text": s, "page": _page_for_sentence(s, pages_norm)} for s in sentences]


def decompose_source(path: str, paper_id: str, key: str, cache_dir: str, llm,
                     workers: int = 1, extract_claims: bool = True) -> Dict[str, Any]:
    """
    Decompose a single source into claims+evidence, using the on-disk cache when the
    file is unchanged. Returns the source-claims dict (and writes it to the cache).

    extract_claims=False (the pipeline default since 2026-07-10) skips the LLM
    claim extraction — the sentence index (the verdict path's ground truth) is
    still built and cached; `claims` stays empty and the cache is marked
    `decomposed: false` so a later --decompose run knows to fill it in. A cache
    that already HAS claims is used as-is in both modes (paid data is kept).
    """
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f"{paper_id}.json")
    fhash = file_hash(path)
    filename = os.path.basename(path)
    title = os.path.splitext(filename)[0]

    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("file_hash") == fhash:
                # Claims extracted from letter-spaced PyPDF2 garble (a cache
                # written before the pdftotext fallback existed) poison every
                # retrieval that reads them — and a sentence-only rebuild can't
                # fix them. Detect on the cached CLAIM text and fall through to
                # a real re-decomposition (LLM cost, once, garbled sources only).
                claims_blob = " ".join((c.get("text") or "")
                                       for c in (cached.get("claims") or []))
                # Only letter-spacing survives into LLM-cleaned CLAIM text as a
                # reliable garble signal. We deliberately do NOT test
                # _looks_space_collapsed here: cleaned claims of a long-word corpus
                # (chemistry nomenclature, agglutinative languages) can sit in its
                # [8,20) mean-length band and would then re-decompose on EVERY run —
                # a permanent LLM re-buy. The schema upgrade below already re-indexes
                # the raw sentences (evidence) from the pdftotext-clean read at $0.
                if _looks_letter_spaced(claims_blob):
                    logger.warning(f"[cache] {filename}: cached claims look letter-spaced "
                                   f"(pre-pdftotext garble) — re-decomposing from clean text")
                elif extract_claims and cached.get("decomposed") is False:
                    # sentence-only cache from a default (no-decompose) run, and
                    # this run asked for claims -> fall through to extraction
                    logger.info(f"[cache] {filename}: sentence-only cache — "
                                f"extracting claims now (--decompose)")
                elif cached.get("schema") == CACHE_SCHEMA and "sentences" in cached:
                    logger.info(f"[cache] source claims for {filename} ({len(cached.get('claims', []))} claims)")
                    cached["key"] = key
                    return cached
                # Same content, older schema: add the per-sentence index WITHOUT re-running
                # the (expensive) LLM claim extraction — keep the cached claims as-is.
                elif cached.get("claims") is not None:
                    logger.info(f"[cache] upgrading {filename} to schema {CACHE_SCHEMA} (no LLM)")
                    cached["sentences"] = _sentence_index(path)
                    cached["schema"] = CACHE_SCHEMA
                    cached["num_sentences"] = len(cached["sentences"])
                    cached["key"] = key
                    with open(cache_path, "w", encoding="utf-8") as f:
                        json.dump(cached, f, indent=2, ensure_ascii=False)
                    return cached
        except Exception:
            pass

    logger.info(f"Decomposing source: {filename}" if extract_claims
                else f"Indexing source (no claim extraction): {filename}")
    pages = read_source_pages(path)
    pages_norm = [_norm(p) for p in pages]
    text = "\n".join(pages)
    sentences = sentence_split(text)
    sentence_index = [{"text": s, "page": _page_for_sentence(s, pages_norm)} for s in sentences]
    claim_texts = (_extract_claims_from_text(text, llm, workers=workers)
                   if (text.strip() and extract_claims) else [])
    evidence_lists = _link_evidence(claim_texts, sentences)

    claims = []
    for i, (ctext, ev) in enumerate(zip(claim_texts, evidence_lists)):
        ev_pages = [_page_for_sentence(s, pages_norm) for s in ev]
        claims.append({"id": f"{paper_id[:8]}_c{i}", "text": ctext,
                       "evidence": ev, "evidence_pages": ev_pages})

    result = {
        "paper_id": paper_id,
        "key": key,
        "filename": filename,
        "title": title,
        "file_hash": fhash,
        "schema": CACHE_SCHEMA,
        "num_pages": len(pages),
        "source_text_chars": len(text),
        "num_sentences": len(sentences),
        "sentences": sentence_index,   # full source sentences (text + page) for Stage-3 matching
        "claims": claims,
        "decomposed": bool(extract_claims),  # False = claims deliberately skipped
    }
    if not text.strip():
        result["warning"] = "source_text_empty (scanned PDF or unreadable) — supply a .txt/OCR copy"

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    logger.info(f"Extracted {len(claims)} claims from {filename}")
    return result
