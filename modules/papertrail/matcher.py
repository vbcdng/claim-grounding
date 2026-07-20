"""
Stage 3: for each text claim, find the supporting SENTENCE in its cited source.

We match the claim directly against the source's own sentences (SPECTER cosine),
take the top-K candidates, and let the LLM judge whether any of them supports the
claim — surfacing the actual sentence it judged. Matching against the source's real
sentences (rather than LLM-decomposed atomic claims) avoids losing facts and avoids
re-worded mismatches, and guarantees the displayed evidence is the judged evidence.

Verdict: supported / unsupported / omitted. Omitted (cherry-picking signal) still uses
the source's atomic claims: a source claim is "used" if a sentence that supported some
text claim is one of that claim's evidence sentences.
"""

import os
import re
import logging
import unicodedata
from typing import List, Dict, Any, Tuple

from . import embeddings
from .llm_client import extract_json

logger = logging.getLogger(__name__)

# SPECTER scores same-TOPIC sentences highly even when one does not SUPPORT the other,
# so a high cosine alone is NOT evidence of support. Cosine only rejects clearly
# off-topic candidates cheaply; the LLM makes the actual support decision.
OFFTOPIC = 0.55       # candidate sentence cosine below this -> not a candidate
AUTO_SUPPORT = 0.97   # near-verbatim match -> accept without an LLM call
                      # (unless the ±1 window carries a retraction/correction
                      # cue — see _CONTRA_CUE_RE below)

# Retraction/correction cues that disqualify the near-verbatim auto-accept: a
# high-cosine match to a sentence that the surrounding window walks back
# (retractions, errata, "in fact went to…") must be judged, not auto-accepted.
# Kept TIGHT (retraction semantics only) so ordinary prose rarely trips it; a
# trip costs one judge call, never a verdict.
_CONTRA_CUE_RE = re.compile(
    r"\b(retract\w*|erratum|errata|correction|corrected|withdraw\w*|refut\w*|"
    r"debunk\w*|erroneous\w*|mistaken\w*|in fact|contrary to|no longer|"
    r"falsely|is false|was false|turned out)\b", re.I)
TOPK = 3              # judge up to this many best candidate sentences per claim

# The decomposer scopes a marker over EVERYTHING since the previous marker, so a
# claim often carries uncited lead-in sentences (author's framing/transitions)
# ahead of the cited assertion. Judges reject on the lead-in even when the tail
# is well supported (paper1: 8 of 11 multi-sentence failures quote the lead-in
# in their rejection reason — t22/t65 hand-verified). When a multi-sentence claim
# fails, re-judge its last 1..MAX_SUFFIX sentences alone; a supported tail
# rescues the claim and the lead-in is labeled as the author's own (not red).
TAIL_RESCUE_MAX_SUFFIX = 2


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _loose_text(s: str) -> str:
    """Punctuation-insensitive normalization for verbatim source-membership
    checks: lowercase, keep only alphanumerics as space-separated tokens. Robust
    to curly quotes / whitespace drift, while a sentence whose WORDS are not in
    the source in that order still fails."""
    return " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))


def _charstream(s: str) -> str:
    """Alphanumeric character stream (no spaces at all) — the spacing-insensitive
    companion to _loose_text. PDF extraction garbles word boundaries (hyphen-split
    'per - sonalized', glued or letter-spaced text), so an honest quote of a
    garbled region matches the source only at the character level."""
    return "".join(re.findall(r"[a-z0-9]+", (s or "").lower()))


# The unsourced-claim-fragment test (essay-t9 class, VERIFIED_FINDINGS
# 2026-07-17): a fused extraction is poisonous specifically because it carries a
# run of the CLAIM'S OWN WORDING that the source does not contain. Honest
# condensations (extractor drops a parenthetical) and de-hyphenated spans share
# no such run — any wording they share with the claim is wording the source
# itself contains. 6 loose tokens ≈ a clause; shorter collisions are idiom.
_CLAIM_FRAGMENT_TOKENS = 6


def _unsourced_claim_fragment(extracted: str, claim: str,
                              full_loose: str, full_chars: str) -> bool:
    """True iff `extracted` contains a >=_CLAIM_FRAGMENT_TOKENS contiguous token
    run that also appears in `claim` but NOT in the source (loose or charstream
    match) — i.e. claim-authored text masquerading as a quote."""
    et = _loose_text(extracted).split()
    ct = _loose_text(claim).split()
    n = _CLAIM_FRAGMENT_TOKENS
    if len(et) < n or len(ct) < n:
        return False
    cgrams = {" ".join(ct[i:i + n]) for i in range(len(ct) - n + 1)}
    padded = f" {full_loose} "
    for i in range(len(et) - n + 1):
        g = " ".join(et[i:i + n])
        if g in cgrams and f" {g} " not in padded \
                and g.replace(" ", "") not in full_chars:
            return True
    return False


# PDF extraction litters the sentence index with fragments like "." or "1 2 3";
# the paper1 audit found the fallback judge being shown a literal "." as evidence.
# But short NUMERIC table rows can be real evidence (audit t6: "EU, 4.8% 4." is the
# EU's supercomputer share) — a fragment with a digit and at least 8 chars passes;
# the judge sees it inside a ±1-sentence window, so it is not judged bare.
_MIN_EVIDENCE_CHARS = 8
_MIN_EVIDENCE_WORDS = 4


def _degenerate(s: str) -> bool:
    s = (s or "").strip()
    if len(s) < _MIN_EVIDENCE_CHARS:
        return True
    return len(s.split()) < _MIN_EVIDENCE_WORDS and not re.search(r"\d", s)


# Reference-list fragments (owner walkthrough item 19, t44): PDF sentence-splitting
# turns a bibliography line into a "sentence" like "Review of Economic Studies." —
# a bare journal/proceedings name, never real evidence. Shape: 2-7 words, ≥2
# Title-Case tokens, and no lowercase CONTENT word (only connectors/journal words).
_JOURNAL_WORDS = {"of", "and", "the", "for", "in", "on", "a", "&", "journal",
                  "review", "reviews", "proceedings", "letters", "annual", "science",
                  "sciences", "studies", "research", "economics", "politics", "medicine",
                  "nutrition", "circulation", "advances", "international", "national"}


def _is_reference_fragment(s: str) -> bool:
    s = (s or "").strip().rstrip(".")
    words = s.split()
    if not (2 <= len(words) <= 7):
        return False
    alpha = [w for w in words if any(c.isalpha() for c in w)]
    if len(alpha) < 2:
        return False
    titles = sum(1 for w in alpha if w[:1].isupper())
    content_lowers = [w for w in alpha if w.islower() and w.lower() not in _JOURNAL_WORDS]
    return titles >= 2 and not content_lowers


# A citation / reference-header line that PDF extraction glued into a body
# "sentence" (owner walkthrough t20, qin2018: "...Heart 2018;104:1756–1763.
# doi:10.1136/heartjnl-2017-312651Original research article ... chenxi Qin,1
# Jun lv,1 Yu g uo,2 ..."). These are metadata, never evidence for a claim, and
# both signals below are unambiguous — a DOI, or a "year;volume:page"
# bibliographic locator — so this stays high-precision (it does not key off
# Title-Case, unlike _is_reference_fragment). A superscript author-block
# heuristic ("Name,<digit>") was tried and REMOVED: it also matches the most
# valuable statistical evidence ("HR, 1.18", "range, 13.0", "ARD, 4.43%"), so it
# dropped real evidence — the opposite of the goal (7.6% of source sentences).
_DOI_RE = re.compile(r"\bdoi:\s*10\.\d{3,}|\bdoi\.org/10\.\d{3,}", re.I)
_VOL_PAGE_RE = re.compile(r"\b(19|20)\d{2};\s*\d+\s*(\(\d+\))?\s*:\s*\d+")   # "2018;104:1756"
# A "how to cite this article" instruction that PDF extraction glued into a body
# "sentence" (owner walkthrough t12, carson2020: "The American Heart Association
# requests that this document be cited as follows: Carson JAS, …"). It is metadata
# + an author list, never evidence for a claim. High-precision phrasings only.
_CITE_REQUEST_RE = re.compile(
    r"(?i)\b(?:cited as follows|be cited as|cite this (?:document|article|paper|work) as"
    r"|recommended citation|how to cite|please cite|citation for this article)\b")


def _is_citation_header(s: str) -> bool:
    s = (s or "").strip()
    return bool(_DOI_RE.search(s) or _VOL_PAGE_RE.search(s) or _CITE_REQUEST_RE.search(s))


def _unusable_evidence(s: str) -> bool:
    """A candidate sentence that must never be judged or shown as evidence."""
    return _degenerate(s) or _is_reference_fragment(s) or _is_citation_header(s)


# Verbatim quoted spans in a claim (owner walkthrough item 15, t42/t31): when the
# author quotes a source phrase ("a peer competitor in AI"), a deterministic string
# search finds the exact sentence even when SPECTER cosine buries it — the retrieval
# miss that produced two confirmed false-unsupporteds. The matched sentence is fed
# to the judge (never auto-accepted): a negated/sarcastic quote is still caught.
_QUOTE_RE = re.compile(r'[“"]([^“”"]{10,240})[”"]')


def _quoted_spans(claim: str) -> List[str]:
    spans = []
    for m in _QUOTE_RE.finditer(claim or ""):
        s = m.group(1).strip()
        if len(s.split()) >= 3:                # skip trivial one/two-word quotes
            spans.append(s)
    return spans


def _quote_hit_indices(claim: str, sents: List[Dict[str, Any]]) -> List[int]:
    spans = [_norm(s) for s in _quoted_spans(claim)]
    spans = [s for s in spans if s]
    if not spans:
        return []
    hits = []
    for j, s in enumerate(sents):
        ns = _norm(s.get("text", ""))
        if ns and any(sp in ns for sp in spans):
            hits.append(j)
    return hits


def _is_claim_echo(extracted: str, claim: str) -> bool:
    """The extraction model sometimes echoes the CLAIM back as its 'verbatim source
    sentence' (audit: t37, t59). Verbatim-quoting a real source sentence is fine —
    that maps to the sentence index — so callers only treat UNMAPPED extractions
    as echoes."""
    import difflib
    a, b = _norm(extracted), _norm(claim)
    if not a or not b:
        return False
    return a in b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


# Per-backend prompt swaps (filename -> absolute replacement path). The claude-code
# backend installs the Haiku-tuned combined-judgment rubric here (the study found
# Haiku needs its NUMBERS AND MAGNITUDES rules; the PRODUCTION prompt files stay
# untouched and stay under the 3-paper Gemini gate).
PROMPT_OVERRIDES: Dict[str, str] = {}


def _load_prompt(name: str) -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = PROMPT_OVERRIDES.get(name) or os.path.join(root, "config", "prompts", name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_judgment_prompt() -> str:
    return _load_prompt("pt_support_judgment_prompt.txt")


def _failed_calls(llm) -> int:
    """LLMClient.failed_calls as an int; 0 for test fakes/mocks without it."""
    n = getattr(llm, "failed_calls", 0)
    return n if isinstance(n, int) else 0


def _parse_support(raw: str) -> Tuple[bool, str]:
    """
    Robustly read the support judgment, tolerating truncated/fenced JSON.
    Returns (supported, reason).
    """
    if not raw:
        return False, "no LLM response -> treated as unsupported"
    obj = extract_json(raw)
    if isinstance(obj, dict) and "supported" in obj:
        return bool(obj["supported"]), str(obj.get("reason", "LLM support judgment"))
    # Fallback: regex the boolean even if the JSON is cut off mid-string.
    m = re.search(r'"supported"\s*:\s*(true|false)', raw, re.IGNORECASE)
    if m:
        rmatch = re.search(r'"reason"\s*:\s*"([^"]*)', raw)
        return m.group(1).lower() == "true", (rmatch.group(1).strip() if rmatch else "LLM support judgment")
    return False, "LLM judgment unparseable -> treated as unsupported"


def _snippet(sentence: str, n_words: int = 7) -> str:
    """A short, distinctive search term for the viewer's fallback PDF find.

    The stored sentence is PyPDF2-extracted and often won't match PDF.js's text layer
    verbatim; a normalized first-few-words prefix (minus any leading [n] reference
    marker) is far more likely to hit.
    """
    s = unicodedata.normalize("NFKC", sentence or "")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^\[\d+\]\s*", "", s)
    return " ".join(s.split()[:n_words])


def _evidence(src: Dict[str, Any], sent: Dict[str, Any], pid: str) -> Dict[str, Any]:
    text = sent.get("text", "")
    return {
        "paper_id": pid,
        "source_title": src.get("title"),
        "sentence": text,
        "page": sent.get("page"),
        "snippet": _snippet(text),
    }


def _window(sents: List[Dict[str, Any]], j: int, radius: int = 1) -> str:
    """Candidate sentence plus its neighbours — covers facts that sentence-splitting
    fragmented (e.g. a break at 'i.e.') or that span two sentences."""
    lo, hi = max(0, j - radius), min(len(sents), j + radius + 1)
    return " ".join(sents[k].get("text", "") for k in range(lo, hi))


def _judge_source(claim: str, pid: str, src: Dict[str, Any], row: List[float],
                  llm, prompt: str) -> Dict[str, Any]:
    """Best supporting (or, failing that, closest) sentence within ONE source for a claim."""
    sents = src.get("sentences", []) or []
    if not sents or not row:
        return None
    ranked = [j for j in sorted(range(len(row)), key=lambda k: row[k], reverse=True)
              if not _unusable_evidence(sents[j].get("text", ""))]
    topk = [j for j in ranked[:TOPK] if row[j] >= OFFTOPIC]
    title = src.get("title")

    def entry(j: int, supported: bool, reason: str) -> Dict[str, Any]:
        text = sents[j].get("text", "")
        return {"paper_id": pid, "source_title": title, "supported": supported,
                "sentence": text, "page": sents[j].get("page"), "snippet": _snippet(text),
                "cosine": round(float(row[j]), 4), "reason": reason,
                "window": _window(sents, j), "j": j}

    # Verbatim quoted-span hits jump the cosine queue: the author quoted this
    # source's exact words, so judge those sentences FIRST (item 15). Still judged,
    # not auto-accepted — a quote used in negation is caught by the ±1 window.
    q_hits = [j for j in _quote_hit_indices(claim, sents)
              if not _unusable_evidence(sents[j].get("text", ""))]
    judge_order = q_hits + [j for j in topk if j not in q_hits]
    if not judge_order:
        if ranked:
            j = ranked[0]
            return entry(j, False, f"off-topic (cosine {round(float(row[j]),4)} < {OFFTOPIC})")
        return None

    first_reason = None
    for j in judge_order:
        cos = float(row[j])
        if j not in q_hits and cos >= AUTO_SUPPORT \
                and not _CONTRA_CUE_RE.search(_window(sents, j)):
            # A retraction/correction cue in the ±1 window disqualifies the
            # auto-accept (synth prizerec class, VERIFIED_FINDINGS 2026-07-17:
            # a ≥0.97 match to "early reports stated X won…" locked in supported
            # while the NEXT sentence said the reports were retracted). The claim
            # still gets judged below — with the window — so a true support only
            # pays one extra call; it is never auto-rejected here.
            return entry(j, True, f"near-verbatim match (cosine {round(cos,4)} ≥ {AUTO_SUPPORT})")
        # Provenance matters: attribution claims ("UNDP describes…", "as Altman
        # argues") are only judgeable when the judge knows whose document this is.
        label = _src_label(src)
        passage = f"From {label}: {_window(sents, j)}" if label else _window(sents, j)
        supported, reason = _parse_support(
            llm.call(_inject_date_rule(
                         prompt.replace("{CLAIM}", claim).replace("{PASSAGE}", passage),
                         passage),
                     temperature=0.0, max_output_tokens=2048))
        if first_reason is None:
            first_reason = reason
        if supported:
            return entry(j, True, reason)
    return entry(judge_order[0], False,
                 first_reason or "no candidate sentence supported the claim")


def _combined_judge(claim: str, labeled_windows: List[Tuple[str, str]], llm, prompt: str,
                    early_break: bool = True) -> Tuple[bool, str, str]:
    """Does the union of the cited sources' best passages together support the claim?"""
    passage = "\n\n".join(f"From {title}: {win}" for title, win in labeled_windows)
    return _vote_support(llm,
                         _inject_date_rule(
                             prompt.replace("{CLAIM}", claim).replace("{PASSAGE}", passage),
                             passage),
                         early_break=early_break)


# Fallback judgments sit on the borderline by construction (the cheap cosine path
# already said no), and flash-lite flips borderline verdicts between runs even at
# temperature 0 — runs 3/4 gave opposite verdicts on IDENTICAL passages (t27, t49).
# Majority-of-3 stabilizes them; the first two calls decide when they agree, so the
# steady-state overhead is one extra small judgment call.
JUDGE_VOTES = 3


def _vote_support(llm, prompt: str, early_break: bool = True) -> Tuple[bool, str, str]:
    """Majority verdict + a matching reason + the tally ('2-0' unanimous, '2-1'
    split). The tally is free borderline-ness signal: a 2-1 'unsupported' is a
    close call the viewer flags for human review instead of hiding.
    early_break=False always casts all JUDGE_VOTES votes — for decisions that
    need the full tally (the partial-support flag requires a UNANIMOUS negative)."""
    votes: List[Tuple[bool, str]] = []
    for _ in range(JUDGE_VOTES):
        votes.append(_parse_support(
            llm.call(prompt, temperature=0.0, max_output_tokens=4096)))
        if early_break and len(votes) == 2 and votes[0][0] == votes[1][0]:
            break
    n_true = sum(1 for s, _ in votes if s)
    ok = n_true * 2 > len(votes)
    tally = f"{max(n_true, len(votes) - n_true)}-{min(n_true, len(votes) - n_true)}"
    for s, r in votes:            # report a reason matching the majority verdict
        if s == ok:
            return ok, r, tally
    return ok, votes[-1][1], tally


# ---------- LLM full-source extraction fallback ----------
# When SPECTER cosine fails to surface a supporting sentence for a multi-source claim,
# we stop trusting the cosine candidates and let the LLM read each cited source's FULL
# text and pull the verbatim supporting sentence(s) itself.

def _parse_sentences(raw: str) -> List[str]:
    """Read the {"sentences":[...]} extraction output, tolerating a bare list, fences,
    or a response truncated mid-array (thinking models can exhaust the token budget)."""
    if not raw:
        return []
    obj = extract_json(raw)
    if isinstance(obj, dict):
        obj = obj.get("sentences", [])
    if isinstance(obj, list):
        # Filter fragments BEFORE mapping: a "." would otherwise substring-map
        # onto an arbitrary real sentence and launder itself into "evidence".
        out = [s.strip() for s in obj
               if isinstance(s, str) and s.strip() and not _degenerate(s)]
        if out:
            return out
    # Salvage: pull every COMPLETE quoted string from the (possibly truncated) array.
    # A trailing cut-off sentence has no closing quote, so it is simply dropped.
    m_arr = re.search(r'"sentences"\s*:\s*\[(.*)', raw, re.S)
    body = m_arr.group(1) if m_arr else raw
    found = re.findall(r'"((?:[^"\\]|\\.)*)"', body)
    return [s.strip().replace('\\"', '"') for s in found if s.strip() and not _degenerate(s)]


def _map_to_index(extracted: str, sents: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Map an LLM-extracted sentence back to the stored sentence index.

    Returns the canonical stored sentence (so the viewer's stored-text highlight + page
    jump work) and its page. Falls back to token overlap, then to the raw extracted text.
    """
    n = _norm(extracted)
    if not n:
        return {"text": extracted, "page": None, "j": -1}
    ewords = set(n.split())
    best_j, best_score = -1, 0.0
    for j, s in enumerate(sents):
        sn = _norm(s.get("text", ""))
        if not sn:
            continue
        if n == sn:
            return {"text": s.get("text", ""), "page": s.get("page"), "j": j}
        # Containment must be scored, not returned on first hit: a short stored
        # fragment ("2.", a stray header) is a substring of ANY long quote and
        # would hijack the mapping from the true sentence (paper1 t35). Prefer the
        # candidate covering the largest share of the longer string.
        score = 0.0
        if (sn in n or n in sn) and min(len(sn), len(n)) >= 20:
            score = 0.6 + 0.4 * (min(len(sn), len(n)) / max(len(sn), len(n)))
        else:
            swords = set(sn.split())
            if swords:
                score = len(ewords & swords) / len(ewords | swords)
        if score > best_score:
            best_j, best_score = j, score
    if best_j >= 0 and best_score >= 0.6:
        return {"text": sents[best_j].get("text", ""), "page": sents[best_j].get("page"), "j": best_j}
    return {"text": extracted, "page": None, "j": -1}


# Full-text extraction is CHUNKED (paper1 audit + benchmarks/extract_bench.py):
# flash-lite reliably misses needle sentences when handed a whole long document
# (0/4 long-doc needles found whole-doc; found when the containing ~1200-word
# chunk is sent alone). Chunks are gated by the claim's sentence cosines — the
# needle chunk ranked in the top 6 in every benchmark case — so long documents
# get CHEAPER as well as more reliable (top-K chunks vs the whole text).
EXTRACT_CHUNK_WORDS = 1200
EXTRACT_TOP_CHUNKS = 6
EXTRACT_LEX_CHUNKS = 2   # extra chunks rescued by LEXICAL overlap (union with the
                         # cosine top-K, never a replacement — recall can only rise)


_LEX_TOKEN_RE = re.compile(r"\d{1,3}(?:[ ,.]\d{3})+|\d+(?:[.,]\d+)?%?|[a-z]{4,}")
_GROUPED_NUM_RE = re.compile(r"\d{1,3}(?:[ ,.]\d{3})+$")


def _canon_tok(tok: str) -> str:
    """Canonicalize digit-grouped numbers so '100,000', '100 000' and '100000'
    all share one token (round-6: the UNODC per-100,000 rates never matched the
    claim lexically across grouping styles). Decimals like '5.8' are untouched."""
    if _GROUPED_NUM_RE.match(tok):
        return re.sub(r"[ ,.]", "", tok)
    return tok


def _lex_scores(claim: str, texts: List[str]) -> List[float]:
    """Per-text lexical relevance vs the claim: IDF-weighted overlap of the claim's
    tokens (IDF computed within THIS source). Rare shared tokens — figures, years,
    named terms — dominate, which is exactly the signal embeddings miss: run 7's
    t17 needle ('Around 70% of foundational AI models…') ranked 1383/1819 by
    cosine against the claim quoting that very figure. Pure local math, no API."""
    import math
    claim_toks = {_canon_tok(t) for t in _LEX_TOKEN_RE.findall(claim.lower())}
    if not claim_toks:
        return [0.0] * len(texts)
    text_toks = [claim_toks.intersection(_canon_tok(x) for x in _LEX_TOKEN_RE.findall(t.lower()))
                 for t in texts]
    df: Dict[str, int] = {}
    for toks in text_toks:
        for t in toks:
            df[t] = df.get(t, 0) + 1
    n = max(len(texts), 1)
    idf = {t: math.log(1 + n / (1 + d)) for t, d in df.items()}
    return [sum(idf[t] for t in toks) for toks in text_toks]


def _rank_positions(vals: List[float]) -> List[int]:
    """Position of each element in the descending sort of vals (0 = best)."""
    order = sorted(range(len(vals)), key=lambda i: -vals[i])
    pos = [0] * len(vals)
    for p, i in enumerate(order):
        pos[i] = p
    return pos


# Reciprocal-rank fusion constant: small k weights the top ranks heavily —
# right for "did the true evidence sentence make the shortlist" retrieval.
_RRF_K = 5


def _rrf(cos_vals: List[float], lex_vals: List[float]) -> List[float]:
    """Reciprocal-rank fusion of a cosine and a lexical score list (one fused
    key per element). THE hybrid-retrieval formula — extraction pooling and the
    partial-check escalation must rank identically, so there is exactly one
    copy of it."""
    rc, rl = _rank_positions(cos_vals), _rank_positions(lex_vals)
    return [1.0 / (_RRF_K + rc[i]) + 1.0 / (_RRF_K + rl[i])
            for i in range(len(cos_vals))]


def _chunk_sents(sents: List[Dict[str, Any]], chunk_words: int = EXTRACT_CHUNK_WORDS):
    """Split the stored sentence list into ~chunk_words chunks: [(text, [indices])]."""
    chunks, cur, idxs, words = [], [], [], 0
    for j, s in enumerate(sents):
        t = s.get("text", "")
        cur.append(t); idxs.append(j); words += len(t.split())
        if words >= chunk_words:
            chunks.append((" ".join(cur), idxs)); cur, idxs, words = [], [], 0
    if cur:
        chunks.append((" ".join(cur), idxs))
    return chunks


def _extract_evidence(claim: str, pid: str, src: Dict[str, Any], llm,
                      extract_prompt: str, judge_prompt: str,
                      row: List[float] = None) -> Dict[str, Any]:
    """LLM extracts verbatim supporting sentence(s) from ONE source — per ~1200-word
    chunk, not whole-document — and judges whether that source alone supports the
    claim. row = the claim's cosine per source sentence (from the candidate stage);
    when present and the source is long, only the top-EXTRACT_TOP_CHUNKS chunks by
    max sentence cosine are read. Returns one evidence entry."""
    sents = src.get("sentences", []) or []
    title = src.get("title")
    base = {"paper_id": pid, "source_title": title, "cosine": None, "via": "llm_fulltext"}
    chunks = _chunk_sents(sents)
    if not chunks or not any(c[0].strip() for c in chunks):
        return {**base, "supported": False, "sentence": None, "page": None, "snippet": "",
                "reason": "source text empty/unreadable", "window": "", "j": -1}
    lex = _lex_scores(claim, [s.get("text", "") for s in sents])
    if row and len(row) == len(sents) and len(chunks) > EXTRACT_TOP_CHUNKS:
        ranked = sorted(range(len(chunks)),
                        key=lambda i: -max(row[j] for j in chunks[i][1]))
        keep = set(ranked[:EXTRACT_TOP_CHUNKS])
        # Lexical rescue (union, never replacement): chunks holding the claim's
        # rare tokens that cosine buried (run-7 t17 class). Only chunks with a
        # real overlap qualify — a zero score adds nothing.
        lex_ranked = sorted(range(len(chunks)),
                            key=lambda i: -max(lex[j] for j in chunks[i][1]))
        # Round-6: on huge documents the fixed top-2 lexical rescue is too
        # narrow (UNODC, ~330 chunks: the per-100,000 needle ranked just past
        # it) — scale breadth with size, capped at 8 extra chunks.
        lex_keep = max(EXTRACT_LEX_CHUNKS, min(8, len(chunks) // 40))
        for i in lex_ranked[:lex_keep]:
            if max(lex[j] for j in chunks[i][1]) > 0:
                keep.add(i)
        chunks = [chunks[i] for i in sorted(keep)]                     # document order

    def _extract_from(text: str) -> List[str]:
        return _parse_sentences(
            llm.call(extract_prompt.replace("{CLAIM}", claim).replace("{SOURCE}", text),
                     temperature=0.0, max_output_tokens=2048))

    extracted = [e for text, _ in chunks for e in _extract_from(text)]
    if not extracted and chunks:
        # A legitimately-empty answer and a flaky/truncated one look identical; one
        # retry on the most relevant chunk recovers the variance cases cheaply.
        extracted = _extract_from(chunks[0][0])
    if not extracted:
        return {**base, "supported": False, "sentence": None, "page": None, "snippet": "",
                "reason": "LLM found no sentence in the full source supporting the claim",
                "window": "", "j": -1}

    mapped = [_map_to_index(e, sents) for e in extracted]
    # Evidence-quality gate (paper1 audit): drop fragments ("."), and drop
    # extractions that do NOT exist in the source but closely mirror the claim —
    # the model echoing the claim back, not quoting the source. A true verbatim
    # quote maps to the index (j >= 0) and passes.
    #
    # Source-membership gate (essay-t9 bug, VERIFIED_FINDINGS 2026-07-17): the
    # echo check compares the WHOLE extraction to the claim, so a FUSED string —
    # a genuine source sentence with the claim's own tail concatenated onto it —
    # evades it (not a substring of the claim, ratio < 0.85) and its raw text
    # used to flow into the judged passage, letting the judge "prove" the claim
    # against its own words. Verbatim-in-source (punctuation- then spacing-
    # insensitive, for garbled PDFs) is an unconditional keep; otherwise an
    # unmapped extraction dies only when it carries an UNSOURCED CLAIM FRAGMENT —
    # a clause of the claim's own wording the source doesn't contain. Honest
    # condensations (extractor drops a parenthetical) stay: dropping every
    # non-verbatim extraction flipped paper1 t27 to a false unsupported
    # (2026-07-17 gate run — the tail_rescue proof rode an unmapped extraction).
    full_text = " ".join(s.get("text", "") for s in sents)
    full_loose = _loose_text(full_text)
    full_chars = _charstream(full_text)

    def _keep(m: Dict[str, Any]) -> bool:
        if _unusable_evidence(m["text"]):
            return False
        if m["j"] != -1:
            return True
        if _is_claim_echo(m["text"], claim):
            return False
        if _loose_text(m["text"]) in full_loose or _charstream(m["text"]) in full_chars:
            return True                     # verbatim quote (spacing-insensitive)
        if _unsourced_claim_fragment(m["text"], claim, full_loose, full_chars):
            logger.info("membership gate: dropped unsourced claim-fragment "
                        "extraction from %s: %.90r", pid, m["text"])
            return False
        return True                         # honest non-verbatim condensation

    mapped = [m for m in mapped if _keep(m)]
    seen, uniq = set(), []
    for m in mapped:                                   # chunks can re-extract a sentence
        k = m["j"] if m["j"] >= 0 else _norm(m["text"])
        if k not in seen:
            seen.add(k); uniq.append(m)
    # Rank pooled hits before capping: chunks emit hits in document order, so the
    # cap and the primary-sentence choice favored early-document sentences over
    # the actual best match (audit t28/t49/t68). Cosine and lexical ranks are
    # fused reciprocally — a verbatim-figure sentence with a weak embedding
    # (run-7 t17) can't be capped out by higher-cosine junk, and vice versa.
    # Unmapped hits (j == -1) sort last; ties keep document order (stable sort).
    if row and len(row) == len(sents) and len(uniq) > 1:
        cos_vals = [row[m["j"]] if m["j"] >= 0 else -1.0 for m in uniq]
        lex_vals = [lex[m["j"]] if m["j"] >= 0 else -1.0 for m in uniq]
        fused = _rrf(cos_vals, lex_vals)
        uniq = [uniq[i] for i in sorted(range(len(uniq)), key=lambda i: -fused[i])]
    mapped = uniq[:8]
    if not mapped:
        return {**base, "supported": False, "sentence": None, "page": None, "snippet": "",
                "reason": "LLM returned no usable source sentence (fragments or claim echo)",
                "window": "", "j": -1}
    # The judged passage is the BARE extracted sentences: sampling the judge on the
    # benchmark passages showed it perfectly stable (4/4 or 0/4) on short clean
    # passages, while long ±window concatenations tip it to "not stated" refusals
    # (runs 4/5). Only short table-row fragments get their ±1-sentence window —
    # they are meaningless without their table header (audit t6).
    parts, seen_w = [], set()
    for m in mapped:
        short = len(m["text"].split()) < _MIN_EVIDENCE_WORDS
        w = _window(sents, m["j"]) if (m["j"] >= 0 and short) else m["text"]
        if w not in seen_w:
            seen_w.add(w); parts.append(w)
    window = " ".join(parts)
    label = _src_label(src)
    passage = f"From {label}: {window}" if label else window
    supported, reason, votes = _vote_support(
        llm, _inject_date_rule(
            judge_prompt.replace("{CLAIM}", claim).replace("{PASSAGE}", passage),
            passage))
    primary = mapped[0]
    return {**base, "supported": supported, "sentence": primary["text"], "page": primary["page"],
            "snippet": _snippet(primary["text"]), "reason": reason, "votes": votes,
            "window": window, "j": primary["j"]}


# ---------- Partial-support check (opt-in, --partial-check) ----------
# ALCE-style recall over the UNION of cited evidence (ALCE eval.py, reimplemented
# with our judge): a multi-citation claim passed by the per-source OR is fully
# grounded only if the concatenation of ALL cited evidence entails it. Two
# evidence rounds fix the 2026-07-05 re-audit's false alarms (6/7 flags spurious,
# every one because the judge saw a single cosine window while the real support
# sat in the source's title/abstract/thesis):
#   round 1 — hybrid retrieval (SemanticCite): each source's LEAD sentences
#     (where the title/abstract live) are injected next to its window, so a claim
#     restating what a paper IS about no longer reads as ungrounded;
#   round 2 — NEI-triggered escalation (DeepSciVerify trigger, sciwrite-lint
#     ladder): only on a round-1 negative, re-judge against the source's cached
#     DECOMPOSED CLAIMS (top-k by fused lexical+cosine rank) — the "full-source
#     fix" ROADMAP 7-i blocks default-on re-enablement on. Zero new
#     decomposition: the claims are already on disk.
# ALCE's precision test rides along for free: when recall passes, a cited source
# that neither supports the claim alone nor is needed by the union (it still
# entails without it) gets an "over-citation" nudge.
# All of it is a NUDGE, never a veto: the verdict stays supported.
PARTIAL_LEAD_SENTS = 6      # a source's opening sentences ≈ title + abstract
PARTIAL_TOPK_CLAIMS = 4     # decomposed claims per source fed to the round-2 judge
PARTIAL_TOPK_SENTS = 6      # hybrid-ranked SOURCE SENTENCES for round 2 — verbatim
                            # ground truth that survives a broken decomposition
                            # (korinek2023's cache: 8 math fragments from 72 pages)
OVERCITE_MAX_CHECKS = 3     # union-minus-one probes per claim (one call each)
COMPONENT_HUNT_SOURCES = 2  # extraction-probed non-cited sources per missing component


# --- document date (P3, owner ruling 2026-07-11: judges may resolve relative
# time against the article's publication date, with a visible caveat) ---------
# WiCE batch-2 measurement (docs/archive/NIGHT_LOG_2026-07-12_accB.md): temporal proof
# often lives ONLY in datestamp/metadata lines that _unusable_evidence rightly
# filters from evidence display. The date therefore reaches the judge as a
# passage-header fact, never as a standalone supporting sentence.
_MONTHS = ("january|february|march|april|may|june|july|august|september|"
           "october|november|december")
_MON3 = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
_DOC_DATE_RES = [
    # (meta data) PUBLISHED DATETIME: 2016-08-24[T...]
    re.compile(r"PUBLISHED\s+DATETIME:\s*(\d{4})-(\d{2})-(\d{2})", re.I),
    # Posted: 9:36 PM, Nov 14, 2018 / March 7, 2018 / Apr 17, 2019, 09:00am
    re.compile(rf"\b({_MONTHS}|{_MON3})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.I),
    # 22 January 2019 / 15 Feb 19
    re.compile(rf"\b(\d{{1,2}})\s+({_MONTHS}|{_MON3})\.?\s+(\d{{2,4}})\b", re.I),
    # bare ISO: 2018-11-15 ...
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
]
_DOC_DATE_SCAN_SENTS = 25
_MONTH_NUM = {m: i % 12 + 1 for i, m in enumerate(
    (_MONTHS + "|" + _MON3).split("|"))}


def _doc_date(src: Dict[str, Any]) -> str:
    """Best-effort publication date ('YYYY-MM-DD' or 'YYYY-MM') from a source's
    opening sentences. Returns "" when nothing trustworthy is found. Lines that
    look like date RANGES (web-archive capture spans: '17 Apr 2019 - 17 Jul
    2022') are skipped — a range is when the page was crawled, not written."""
    range_re = re.compile(
        rf"\d{{1,2}}\s+(?:{_MON3})\w*\s+\d{{2,4}}\s*[-–]\s*\d{{1,2}}\s+(?:{_MON3})"
        # data spans, not publication dates: "from 2019-01-01 to the present",
        # "2019-01-01 through 2024" (epochai2025 gate source)
        rf"|\d{{4}}-\d{{2}}-\d{{2}}\s*(?:to|until|through|[-–]\s*\d{{4}})",
        re.I)
    marker_re = re.compile(r"\b(posted|updated|published|written|date)\b[:\s]", re.I)
    # /2019/04/17/ in an article URL; the wayback crawl stamp (web/20190417...)
    # is an unslashed digit run and does not match
    url_date_re = re.compile(r"/((?:19|20)\d{2})/(\d{1,2})/(\d{1,2})(?:/|\b)")

    def _valid(y, mo, d):
        if y < 100:
            y += 2000 if y < 50 else 1900
        if 1900 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
        return ""

    def _try_patterns(fragment):
        for rx in _DOC_DATE_RES:
            m = rx.search(fragment)
            if not m:
                continue
            g = m.groups()
            try:
                if g[0].isdigit() and len(g[0]) == 4:          # ISO year first
                    got = _valid(int(g[0]), int(g[1]), int(g[2]))
                elif g[0].isdigit():                            # day month year
                    got = _valid(int(g[2]), _MONTH_NUM[g[1].lower()[:3]], int(g[0]))
                else:                                           # month day year
                    got = _valid(int(g[2]), _MONTH_NUM[g[0].lower()[:3]], int(g[1]))
            except (KeyError, ValueError):
                continue
            if got:
                return got
        return ""

    for s in (src.get("sentences") or [])[:_DOC_DATE_SCAN_SENTS]:
        text = s.get("text", "")
        if not text or range_re.search(text):
            continue
        # A date INSIDE a narrative sentence is usually an event the text
        # describes (johnrfox: the 1944 battle), not the publication date.
        # Accept a date only (a) right after a marker word (Posted:/Date .../
        # PUBLISHED DATETIME:), (b) as an article-URL path segment, or
        # (c) on a short standalone datestamp line.
        for m in marker_re.finditer(text):
            got = _try_patterns(text[m.start():m.start() + 60])
            if got:
                return got
        m = url_date_re.search(text)
        if m:
            got = _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if got:
                return got
        if len(text.split()) <= 7:
            got = _try_patterns(text)
            if got:
                return got
    return ""


# The date-resolution rule is injected into a judgment prompt ONLY when the
# assembled passage actually carries a dated header: prompts for undated
# sources stay byte-identical to the pre-P3 prompts, so the fix cannot move
# any verdict that never sees a date (the 3-paper + coverage gates run on
# undated academic PDFs; flash-lite flips borderline verdicts on ANY prompt
# text change — essay t8, night validation 2026-07-12).
_DATE_RULE = (
    'Article metadata: a passage header may include the source\'s publication '
    'date and/or author byline — "From <title> (byline: <author>; article '
    'dated YYYY-MM-DD):". Relative time references in the passage resolve '
    'against the date: "this year" in an article dated 2019-02-15 means 2019; '
    '"last month", "this week", "yesterday" and weekday names resolve the same '
    'way. Likewise the byline identifies the article\'s author: a claim '
    'attributing this article\'s statements, review, or opinion to a named '
    'person is proven when the byline names that person and the passage '
    'contains the attributed content. A claim component proven only through '
    'such resolution IS proven — but when your true/false decision depends on '
    'it, begin your reason with "DATE-INFERRED: " (date) or "BYLINE-INFERRED: " '
    '(author). The metadata only resolves references against the passage; it '
    'never supplies facts the passage does not state.')
_DATE_RULE_ANCHOR = "Return ONLY a JSON object"


def _inject_date_rule(final_prompt: str, passage: str) -> str:
    """Add the metadata-resolution rule to an already-assembled judgment
    prompt, but only when the passage has a dated/bylined header."""
    if "(article dated " not in passage and "(byline: " not in passage \
            and "; article dated " not in passage:
        return final_prompt
    if _DATE_RULE_ANCHOR in final_prompt:
        return final_prompt.replace(_DATE_RULE_ANCHOR,
                                    _DATE_RULE + "\n\n" + _DATE_RULE_ANCHOR, 1)
    return final_prompt + "\n\n" + _DATE_RULE


_DOC_AUTHOR_RE = re.compile(r"\bAUTHOR:\s*([^(\n]+)")


def _doc_author(src: Dict[str, Any]) -> str:
    """Author byline from an EXPLICIT metadata marker only ('(meta data)
    AUTHOR: Celia Shatzman') — never guessed from prose. P3-general (owner
    condition met night 2026-07-12: styletaylorswiftsong's 'reviewer Annie
    Zaleski' is provable only by the byline)."""
    for s in (src.get("sentences") or [])[:_DOC_DATE_SCAN_SENTS]:
        m = _DOC_AUTHOR_RE.search(s.get("text", ""))
        if m:
            name = m.group(1).strip().strip(",;:.")
            # a plausible byline: 1-4 words, letters/dots/hyphens
            if 0 < len(name.split()) <= 4 and re.match(r"^[\w.'’\- ]+$", name):
                return name
    return ""


def _src_label(src: Dict[str, Any]) -> str:
    """Passage-header label for a source: title plus, when known from
    metadata, the author byline and publication date — so judges can resolve
    relative time ('this year') and byline attribution per the P3 owner
    ruling + its P3-general extension. Display titles stay bare; only judged
    passages get the metadata."""
    title = (src or {}).get("title") or ""
    d = (src or {}).get("doc_date") or ""
    a = (src or {}).get("doc_author") or ""
    meta = "; ".join(x for x in
                     ([f"byline: {a}"] if a else []) +
                     ([f"article dated {d}"] if d else []))
    return f"{title} ({meta})" if meta else title


def _lead_text(src: Dict[str, Any]) -> str:
    """A source's opening sentences — PDF/text extraction puts the title and
    abstract here, exactly the evidence pure cosine retrieval missed (t8's
    davidson2025 title, t28's hackenburg2025 abstract)."""
    sents = [s.get("text", "") for s in (src.get("sentences") or [])]
    lead = [t for t in sents[:PARTIAL_LEAD_SENTS * 2] if not _degenerate(t)]
    return " ".join(lead[:PARTIAL_LEAD_SENTS])


def _hybrid_top(claim: str, texts: List[str], cos_row, k: int) -> List[str]:
    """Top-k texts by reciprocal-rank fusion of lexical and cosine relevance
    (lexical-only when no vectors are available — tests, no cache dir)."""
    if not any(t.strip() for t in texts):
        return []
    lex = _lex_scores(claim, texts)
    if cos_row is not None and len(cos_row) == len(texts):
        key = _rrf([float(v) for v in cos_row], lex)
    else:
        key = lex
    order = sorted(range(len(texts)), key=lambda i: -key[i])
    return [texts[i].strip() for i in order[:k] if texts[i].strip()]


def _escalated_context(claim: str, src: Dict[str, Any], claims_row=None,
                       sents_row=None) -> str:
    """Round-2 evidence for ONE source: the title zone + the top verbatim
    sentences + the top cached decomposed claims, both hybrid-ranked against
    the claim. Sentences and decomposed claims are complementary: sentences
    are ground truth even when the decomposition failed (korinek2023's cache
    is 8 math fragments from 72 pages); decomposed claims de-fragment facts
    the sentence splitter broke up. Kept deliberately COMPACT: the judge is
    stable on short clean passages and tips into blanket "not stated" refusals
    on long concatenations (the run-4/5 learning; re-confirmed live 2026-07-05
    when a lead+8+8 blob turned clears back into unanimous false flags)."""
    sent_texts = [s.get("text", "") for s in (src.get("sentences") or [])]
    head = " ".join([t for t in sent_texts[:4] if not _degenerate(t)][:2])
    parts = [head] if head else []
    top_sents = [t for t in _hybrid_top(claim, sent_texts, sents_row, PARTIAL_TOPK_SENTS)
                 if not _degenerate(t) and t not in head]
    if top_sents:
        parts.append(" ".join(top_sents))
    claim_texts = [(sc.get("text") or "") for sc in (src.get("claims") or [])]
    top_claims = _hybrid_top(claim, claim_texts, claims_row, PARTIAL_TOPK_CLAIMS)
    if top_claims:
        parts.append("Claims this source makes: " + " ".join(top_claims))
    return " ".join(parts)


# The negative judge's reason names the component it found missing ("the passage
# does not state that X"). Extracting X lets round 3 verify the verifier.
_MISSING_COMPONENT_RES = [
    # "the passage does not state that X" / "...does not mention X"
    # Verb list broadened 2026-07-07 (item 17, t30): "does not ESTABLISH that…"
    # and kin were unparsed, so component rescue never fired on them.
    re.compile(r"(?i)(?:passage|sources?|text|claim)s?\s+do(?:es)?\s+not\s+(?:explicitly\s+)?"
               r"(?:state|mention|contain|include|say|support|back|establish|demonstrate|"
               r"show|confirm|indicate|prove|note|report|specify|address|discuss|"
               r"provide|make)\s*"
               r"(?:that\s+|the\s+claim\s+that\s+|information\s+about\s+|any\s+)?(.+)"),
    # "the claim that X is not explicitly stated or unambiguously entailed"
    re.compile(r"(?i)the\s+(?:claim|fact|statement)\s+that\s+(.+?)\s+is\s+not\s+"
               r"(?:explicitly\s+|unambiguously\s+)?"
               r"(?:stated|mentioned|supported|entailed|present|contained)"),
    # rule-5 contradiction shapes — the probe semantics still hold: if the named
    # component IS backed somewhere in a cited source, the contradiction call
    # refuted itself; a genuinely contradicted component stays unsupported.
    # "the claim that X is (directly) contradicted (by ...)"
    re.compile(r"(?i)the\s+(?:claim|fact|statement|assertion)\s+that\s+(.+?)\s+is\s+"
               r"(?:directly\s+|explicitly\s+)?contradicted"),
    # "the passage contradicts the claim that X" / "...contradicts X"
    re.compile(r"(?i)(?:passage|sources?|text)s?\s+(?:directly\s+|explicitly\s+)?"
               r"contradicts?\s+(?:the\s+(?:claim|statement|assertion|fact|idea)\s+"
               r"(?:that\s+)?|that\s+)?(.+)"),
    # "X is contradicted by the passage/Y"
    re.compile(r"(?i)[\"“'‘]?(.+?)[\"”'’]?\s+is\s+"
               r"(?:directly\s+|explicitly\s+)?contradicted\s+by\s"),
]
_COMPONENT_SPLIT_RE = re.compile(r",?\s+(?:nor|or|and)\s+that\s+|,?\s+nor\s+")
# Contradiction reasons usually append the contradicting evidence ("..., which
# states Y" / "— the passage reports Y"); the probe needs only the component.
_COMPONENT_TAIL_RE = re.compile(
    r"\s+[—–;]\s+.*$|,\s+(?:because|since|whereas|which|as\s+the\s+passage)\b.*$",
    re.IGNORECASE)
_PRONOUN_COMPONENTS = {"it", "this", "that", "them", "the claim", "the statement",
                       "the assertion", "the fact"}


def _missing_components(reason: str) -> List[str]:
    """The component assertions a negative combined verdict claims are absent."""
    for rx in _MISSING_COMPONENT_RES:
        m = rx.search(reason or "")
        if m:
            parts = _COMPONENT_SPLIT_RE.split(m.group(1))
            parts = [_COMPONENT_TAIL_RE.sub("", p).strip()
                     .strip("\"“”'‘’").rstrip(".") for p in parts]
            return [p for p in parts
                    if p and p.lower() not in _PRONOUN_COMPONENTS][:3]
    return []


def _claim_names_source(claim: str, src: Dict[str, Any]) -> bool:
    """Does the claim text explicitly attribute to this source (by author name)?
    Cheap surname probe from the citation key ('drago2025' -> 'drago')."""
    key = (src or {}).get("key") or ""
    m = re.match(r"([A-Za-z]+)", key)
    surname = m.group(1).lower() if m else ""
    return len(surname) >= 4 and surname in (claim or "").lower()


def _partial_flags(claim: str, pids: List[str], sources: Dict[str, Dict],
                   evidences: List[Dict[str, Any]], llm, prompt: str,
                   esc_context=None, extract_check=None,
                   comp_hunt=None) -> Dict[str, Any]:
    """Recall/precision flags for one supported cited claim (single- OR
    multi-citation since the owner walkthrough 2026-07-07 — a single-citation
    compound claim over-supports the same way, e.g. an EU figure in no evidence
    while the US/China figures matched). Returns {} (fully grounded, nothing
    over-cited) or a dict with "partial_support" and/or "over_citation".
    esc_context(pid, text) -> the escalated evidence string for that source,
    retrieval-ranked against `text` (the caller wires in cached cosine rows;
    the default is lexical-only ranking). extract_check(pid, text) -> does that
    source's full text contain `text`, per the chunked-extraction pipeline (the
    round-3 probe; falls back to a context judge when absent).
    comp_hunt(components) -> where ELSE a genuinely-missing component might be
    supported (the caller searches the project's other sources); its result is
    attached to the flag so the card can say "support the rest, or the
    unsupported part may be wrong"."""
    if esc_context is None:
        esc_context = lambda pid, text: _escalated_context(
            text, sources.get(pid) or {})
    by_pid: Dict[str, Dict[str, Any]] = {}
    for e in evidences:
        by_pid.setdefault(e.get("paper_id"), e)

    def entry(pid: str, ctx: str):
        src = sources.get(pid)
        if src is None:
            return None
        win = (by_pid.get(pid) or {}).get("window") or ""
        text = " ".join(dict.fromkeys(p for p in (ctx, win) if p))
        return (pid, src.get("title"), text) if text.strip() else None

    round1 = [x for x in ((pid, sources.get(pid)) for pid in pids) if x[1] is not None]
    round1 = [x for x in (entry(pid, _lead_text(src)) for pid, src in round1) if x]
    if not round1:
        return {}
    labeled = round1
    ok, reason, votes = _combined_judge(claim, [(t, w) for _, t, w in round1], llm, prompt)
    escalated = False
    if not ok:
        round2 = [x for x in (entry(pid, esc_context(pid, claim))
                              for pid in pids if sources.get(pid) is not None) if x]
        if round2:
            labeled = round2
            # The flag must be high-precision (the whole 7-i complaint was false
            # alarms), so the deciding round casts ALL votes and only a UNANIMOUS
            # negative flags; a split negative is borderline judge noise
            # (flash-lite flips borderline verdicts at temp 0), not a finding.
            ok, reason, votes = _combined_judge(claim, [(t, w) for _, t, w in round2],
                                                llm, prompt, early_break=False)
            escalated = True
    if not ok:
        unanimous = votes.endswith("-0")
        if escalated and not unanimous:
            return {}
        # Round 3 — verify the verifier. On a compound claim over a multi-source
        # passage the judge names one component and declares it absent even when
        # it is right there (live 2026-07-05: three passes, three different
        # "missing" components for the same t8, each present in the passage).
        # On a single short claim over short evidence it is stable (the
        # judge-bench learning). And ranking heuristics can't be trusted to
        # fetch the component's evidence (davidson2025's near-verbatim "an
        # initial economic lead becomes bigger and bigger" shares ONE lexical
        # token with the component and loses the cosine rank too) — so the
        # probe uses the pipeline's proven needle-finder: chunked full-text
        # EXTRACTION per source. A single component is supported iff SOME
        # cited source contains it; all named components supported -> the flag
        # refuted itself, drop it. A genuinely absent component (the real t69
        # class) survives the probe unchanged.
        comps = _missing_components(reason) if escalated else []
        def _comp_supported(comp: str) -> bool:
            live = [pid for pid in pids if sources.get(pid) is not None]
            if extract_check is not None:
                return any(extract_check(pid, comp) for pid in live)
            targeted = [x for x in (entry(pid, esc_context(pid, comp)) for pid in live) if x]
            if not targeted:
                return False
            return _combined_judge(comp, [(t, w) for _, t, w in targeted], llm, prompt)[0]
        comp_missing = [c for c in comps if not _comp_supported(c)]
        if comps and not comp_missing:
            return {}
        flag = {"reason": reason, "votes": votes, "escalated": escalated}
        if comp_missing and comp_hunt is not None:
            # The named component is in NO cited source — hunt the project's
            # other sources for it, so the card can point the author somewhere
            # concrete ("X may be supported by <source>") or state honestly
            # that nothing on disk backs it.
            hunt = comp_hunt(comp_missing)
            if hunt:
                flag["component_hunt"] = hunt
        return {"partial_support": flag}

    # ALCE precision: probe each source the per-source judge already found
    # unnecessary on its own — if the union still entails WITHOUT it, it adds
    # nothing detectable. The nudge accuses the AUTHOR of over-citing, so it
    # needs the same bar as the partial flag: a UNANIMOUS all-votes verdict —
    # a single lenient call nudged t28's hackenburg2025, the exact source for
    # the claim's second half (2026-07-05 live validation).
    # Skip sources the claim NAMES in prose (item 18, t36: "…noted by Drago and
    # Laine" — drago2025's thesis IS that component; a stray-fragment evidence
    # pick made the union-minus-drago probe pass and mis-flagged it). If the
    # author explicitly attributes to a source, over-citation is the wrong call.
    cands = [pid for pid, _, _ in labeled
             if not (by_pid.get(pid) or {}).get("supported")
             and not _claim_names_source(claim, sources.get(pid))][:OVERCITE_MAX_CHECKS]
    over = []
    for pid in cands:
        rest = [(t, w) for p2, t, w in labeled if p2 != pid]
        if not rest:
            continue
        sup, _, tally = _combined_judge(claim, rest, llm, prompt, early_break=False)
        if sup and tally.endswith("-0"):
            over.append({"paper_id": pid,
                         "source_title": (sources.get(pid) or {}).get("title")})
    return {"over_citation": {"sources": over}} if over else {}


def _split_components(claim_text: str, llm, split_prompt: str) -> List[str]:
    """One tiny call: the claim's citable components (<=4). Round-6 upgrade —
    the reason-regex only sees components the judge happened to NAME, so a
    claim with unnamed extra components could never pass the all-found bar
    (r5 t3), and an unmatched reason phrasing skipped rescue entirely (r5 t1).
    Returns [] on any failure — the caller falls back to the regex."""
    try:
        obj = extract_json(llm.call(split_prompt.replace("{CLAIM}", claim_text),
                                    temperature=0.0, max_output_tokens=512) or "")
        comps = obj.get("components") if isinstance(obj, dict) else None
        if not isinstance(comps, list):
            return []
        return [str(c).strip() for c in comps if str(c).strip()][:4]
    except Exception:
        return []


def _component_rescue(claim_text: str, pids: List[str], sources: Dict[str, Dict],
                      llm, extract_prompt: str, combined_prompt: str,
                      reason: str, base_windows: List[Tuple[str, str]],
                      adhoc_row=None, split_prompt: str = None) -> Dict[str, Any]:
    """False-unsupported rescue (owner walkthrough t23): a multi-component claim
    whose support is SPREAD across a source fails every single-window judgment —
    the judge names one component as missing while the others crowd it out of the
    window. Probe each component alone via chunked full-text extraction (the
    pipeline's proven needle-finder); if EVERY component is found, re-judge the
    whole claim on the union of the original windows + the per-component
    evidence. Since round 6 the component list comes from a real LLM split of
    the claim (ALL components probed, not just the judge-named ones — the
    all-found bar then actually means every part); the reason-regex is the
    fallback when the split fails. Flipping unsupported->supported manufactures
    the worst FP class, so the flip requires a UNANIMOUS all-votes positive.
    Returns None (nothing parseable / nothing found) or
    {"flip", "found", "missing", "evidence", ["reason", "votes"]}."""
    comps = _split_components(claim_text, llm, split_prompt) if split_prompt else []
    if not comps:
        comps = _missing_components(reason)
    if not comps:
        return None
    found, missing, comp_evs = [], [], []
    for comp in comps:
        ev = None
        for pid in pids:
            src = sources.get(pid)
            if src is None:
                continue
            row = adhoc_row(pid, comp) if adhoc_row is not None else None
            e = _extract_evidence(comp, pid, src, llm, extract_prompt,
                                  combined_prompt, row=row)
            if e and e.get("supported"):
                ev = e
                break
        if ev is not None:
            found.append(comp)
            comp_evs.append({**ev, "component": comp})
        else:
            missing.append(comp)
    if not found:
        return None
    if missing:
        # Some components genuinely absent: no flip, but the caller records
        # which parts ARE individually backed so the card can say so.
        return {"flip": False, "found": found, "missing": missing,
                "evidence": comp_evs}
    seen = set(w for _, w in base_windows)
    windows = list(base_windows) + [(e["source_title"], e["window"])
                                    for e in comp_evs
                                    if e["window"] not in seen]
    ok, new_reason, votes = _combined_judge(claim_text, windows, llm,
                                            combined_prompt, early_break=False)
    if ok and votes.endswith("-0"):
        return {"flip": True, "found": found, "missing": [],
                "evidence": comp_evs, "reason": new_reason, "votes": votes}
    return {"flip": False, "found": found, "missing": [], "evidence": comp_evs}


# ---------------------------------------------------------------------------
# Covering-set evidence (round-1 loop fix, owner-approved 2026-07-10).
# The owner standard says the SHOWN sentences must prove EVERY component of a
# claim, but the grounding chain accepts on the first supporting sentence — so
# a supported card typically displays one sentence covering one component
# (loop round 1: 8/11 supported rows showed proof for <=1 component while the
# full proof existed in the cited source; same pattern on eggs 25/27 and
# paper1 16/16). This pass runs AFTER the verdict, on supported cited claims
# only, and is DISPLAY-ONLY: one hybrid-retrieval (cosine+lexical RRF)
# candidate pool per claim, then ONE LLM call that maps the claim's citable
# components to the candidate sentences proving them (<=4 sentences) and names
# the components no candidate proves. The verdict is NEVER touched
# (verdict-from-coverage is a later round); any failure here just means no
# coverage block on the card.

COVER_CANDS_PER_SOURCE = 8    # hybrid top-k sentences pooled per cited source
COVER_MAX_CANDS = 24          # candidate-pool cap across all cited sources
COVER_MAX_ROWS = 12           # runaway guard on the rendered covered list


def _load_covering_prompt() -> str:
    return _load_prompt("pt_covering_set_prompt.txt")


def _covering_candidates(claim: str, pids: List[str], sources: Dict[str, Dict],
                         row_for, evidences: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The candidate pool for the covering-set call: each cited source's hybrid
    (cosine+lexical RRF) top sentences, seeded with the sentences the card
    already shows — the model should reuse displayed evidence where it fits."""
    cands: List[Dict[str, Any]] = []
    seen: set = set()

    def add(pid, title, text, page):
        key = (pid, _norm(text))
        if not text or key in seen or _unusable_evidence(text):
            return
        seen.add(key)
        cands.append({"paper_id": pid, "source_title": title,
                      "text": text, "page": page})

    for e in evidences or []:
        if e and e.get("sentence"):
            add(e.get("paper_id"), e.get("source_title"), e["sentence"], e.get("page"))
    for pid in pids:
        src = sources.get(pid)
        row = row_for(pid)
        sents = (src or {}).get("sentences", []) or []
        if not src or not row or not sents:
            continue
        texts = [s.get("text", "") for s in sents]
        fused = _rrf(list(row), _lex_scores(claim, texts))
        added = 0
        for j in sorted(range(len(sents)), key=lambda k: -fused[k]):
            if added >= COVER_CANDS_PER_SOURCE or len(cands) >= COVER_MAX_CANDS:
                break
            before = len(cands)
            add(pid, src.get("title"), texts[j], sents[j].get("page"))
            added += len(cands) - before
    return cands


def _parse_covering(raw: str, cands: List[Dict[str, Any]]):
    """Validate the covering-set JSON against the candidate pool. Returns the
    claim's `covering` payload ({covered, uncovered}) or None when nothing
    usable parses — None means the card simply gets no coverage block."""
    obj = extract_json(raw or "")
    comps = obj.get("components") if isinstance(obj, dict) else None
    if not isinstance(comps, list):
        return None
    covered: List[Dict[str, Any]] = []
    uncovered: List[str] = []
    for comp in comps:
        if not isinstance(comp, dict):
            continue
        part = str(comp.get("part") or "").strip()
        if not part:
            continue
        # flash-lite emits pick numbers as strings ("5") about as often as ints
        # (round-1 re-run: t4/t5 lost every pick to a strict isinstance check
        # and rendered all-amber) — coerce anything integer-like.
        picks = []
        for p in (comp.get("picks") or []):
            try:
                p = int(str(p).strip().strip("[]"))
            except (ValueError, TypeError):
                continue
            if 1 <= p <= len(cands):
                picks.append(p)
        if not picks:
            uncovered.append(part)
            continue
        for p in picks:
            if len(covered) >= COVER_MAX_ROWS:
                break
            c = cands[p - 1]
            covered.append({"component": part, "paper_id": c["paper_id"],
                            "source_title": c["source_title"],
                            "sentence": c["text"], "page": c.get("page"),
                            "snippet": _snippet(c["text"])})
    if not covered and not uncovered:
        return None
    return {"covered": covered, "uncovered": uncovered}


COVER_ESCALATE_MAX = 3        # uncovered components probed per claim


def _covering_set(claim: str, pids: List[str], sources: Dict[str, Dict], row_for,
                  evidences: List[Dict[str, Any]], llm, prompt: str, probe=None):
    """Run the covering-set display pass for ONE supported cited claim: build
    the candidate pool, make one LLM call, validate. Pure function; thread-safe.

    probe (optional): callable(component) -> covered-entry dict or None.
    Uncovered-component escalation (loop round-2 fix, 2026-07-10): the hybrid
    candidate pool is blind to proof sentences that share few tokens with the
    claim (round 2 verified t6/t7's grader-quoted proofs never reached the
    pool), so each uncovered component is probed ALONE via full-text
    extraction; a hit moves it from the amber line into the covered list.
    Honest ambers survive — an unprovable component's probe finds nothing."""
    cands = _covering_candidates(claim, pids, sources, row_for, evidences)
    if not cands:
        return None
    multi = len({c["paper_id"] for c in cands}) > 1
    lines = []
    for i, c in enumerate(cands, 1):
        label = f' (from {c["source_title"]})' if multi and c.get("source_title") else ""
        lines.append(f"[{i}]{label} {c['text']}")
    raw = llm.call(prompt.replace("{CLAIM}", claim)
                         .replace("{CANDIDATES}", "\n".join(lines)),
                   temperature=0.0, max_output_tokens=2048)
    cov = _parse_covering(raw, cands)
    if cov and cov["uncovered"] and probe is not None:
        still = list(cov["uncovered"][COVER_ESCALATE_MAX:])
        for part in cov["uncovered"][:COVER_ESCALATE_MAX]:
            found = probe(part)
            if found:
                cov["covered"].append(found)
            else:
                still.append(part)
        cov["uncovered"] = still
    return cov


PICK_VERIFY_NEW_MAX = 2       # missed named-specific components probed per claim
PICK_VERIFY_REPROBE_MAX = 2   # parts whose every pick failed, re-probed per claim


def _norm_part(s: str) -> str:
    return " ".join((s or "").casefold().split())


def _parse_pick_verify(raw: str, parts: List[str], n_picks: int):
    """Validate the pick-verification JSON. Returns {keep: {norm_part: [pick
    numbers]}, new_components: [...], common_knowledge: [...]} or None when
    nothing usable parses. A part the model omitted from `verified` is simply
    absent from `keep` — the caller FAILS OPEN and keeps its picks unchanged
    (this pass may only ever tighten the display, never wreck it on a flaky
    response)."""
    obj = extract_json(raw or "")
    if not isinstance(obj, dict) or not isinstance(obj.get("verified"), list):
        return None
    known = {_norm_part(p) for p in parts}
    keep: Dict[str, List[int]] = {}
    common: List[str] = []
    for v in obj["verified"]:
        if not isinstance(v, dict):
            continue
        part = _norm_part(str(v.get("part") or ""))
        if part not in known:
            continue
        ks = []
        for k in (v.get("keep") or []):
            try:
                k = int(str(k).strip().strip("[]"))
            except (ValueError, TypeError):
                continue
            if 1 <= k <= n_picks:
                ks.append(k)
        keep[part] = ks
        # Forced per-part classification (a free-standing common-knowledge
        # LIST made flash-lite echo the prompt's counter-examples verbatim —
        # r5 validation): only an explicit everyday_commonplace on a
        # proof-less part counts; anything else defaults to needs_source.
        if not ks and v.get("no_proof_kind") == "everyday_commonplace":
            common.append(part)
    if not keep:
        return None

    def _strs(key):
        return [str(x).strip() for x in (obj.get(key) or [])
                if isinstance(x, str) and str(x).strip()]
    return {"keep": keep, "new_components": _strs("new_components"),
            "common_knowledge": common}


# --- Subject-entity guard (2026-07-12, WiCE train2 waleedmajid false-support):
# the fulltext-extraction fallback judged "Majid has played ... representing
# Qatar" supported 2-0 from a paywalled stub whose entire text never names
# Majid — a team score-table matched the event, and the judge accepted evidence
# lacking the claim's subject. Deterministic guard: when a claim LEADS with a
# proper-noun run (its subject), a fulltext/rescue positive is only accepted
# from a source whose full text mentions at least one subject token. Strictly
# leading (token 0 of the first sentence, after an article) so attribution
# shapes buried mid-claim ("... Shin and colleagues found ...") never arm the
# guard — extracted paper text can legitimately lack an author byline. A
# frozen common-words set (never the system dictionary — verdicts must be
# machine-independent) disarms ordinary capitalized-because-initial openers
# ("Reviews of...", "Without dialogue, ..."). Corpus scan 2026-07-12: fires on
# exactly 2 of 57 fulltext-supported claims, both WiCE-labeled not_supported.
# Known accepted misses: cross-script aliasing (a Japanese-only page naming
# the subject in katakana reads as absent — observed once, and there the fire
# agreed with the label); cosine-path positives are NOT guarded (zero observed
# failures there — watch item, see docs/SUBJECT_GUARD.md).
_SUBJECT_COMMON = frozenset("""
the a an this that these those it its he she his her they their we our you your
i who what which where when why how there here one some any all both each every
many most few several no not nor and but or so yet if as at by for from in into
of on onto over under to with without within after before during since until
despite although though however moreover furthermore nevertheless meanwhile
additionally also besides thus hence therefore then while because unless once
again still even only just is are was were be been being have has had do does
did can could may might must shall should will would according beyond according
research researchers studies study reviews review results result data evidence
findings finding analysis analyses experts scientists critics reports report
estimates trials figure table section overall finally initially recently
english british american french german spanish italian dutch russian chinese
japanese korean indian african european asian australian canadian mexican
brazilian portuguese greek turkish polish swedish norwegian danish finnish
irish scottish welsh western eastern northern southern
""".split())
_SUBJECT_MIN_TOKEN = 3


def _fold(s: str) -> str:
    """Lowercase + strip diacritics ('Ljubojević' matches 'Ljubojevic')."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(ch for ch in s if not unicodedata.combining(ch)).lower()


def _subject_tokens(text: str) -> List[str]:
    """Folded tokens (len >= _SUBJECT_MIN_TOKEN) of the claim's LEADING
    proper-noun run, or [] when the claim has no checkable leading subject
    (guard silently off — the conservative direction)."""
    punct = "\"'()[]{},.;:!?“”‘’"
    first = re.split(r"(?<=[.!?])\s+", (text or "").strip())[0]
    toks = first.split()
    run: List[str] = []
    for i, tok in enumerate(toks):
        w = tok.strip(punct)
        if not run and i == 0 and w.lower() in ("the", "a", "an"):
            continue                      # "The Shining ..." → subject Shining
        if re.match(r"^[A-Z](?:[^\W\d_]|['’-])*$", w):  # unicode: Ljubojević
            run.append(w)
            if tok and tok[-1] in punct:  # closing quote/comma ends the run
                break
            continue
        if run and w.lower() in ("of", "the", "de", "van", "von", "der"):
            j = i + 1                     # connector inside "University of Oklahoma"
            if j < len(toks) and re.match(r"^[A-Z]", toks[j].strip(punct)):
                run.append(w)
                continue
        break                             # lowercase token: the subject is over
    def _common(w: str) -> bool:          # singular-aware: Reviews/Americans
        lw = w.lower()
        return lw in _SUBJECT_COMMON or lw.rstrip("s") in _SUBJECT_COMMON
    if len(run) == 1 and _common(run[0]):
        return []                         # "Reviews of ..." — ordinary opener
    kept = [_fold(w) for w in run
            if len(w) >= _SUBJECT_MIN_TOKEN and not _common(w)]
    # A multi-token run that COLLAPSES to one checkable token is a fragment of
    # a longer phrase, not a checkable subject: "Frontier AI ..." loses 'AI' to
    # the length filter and would demand the generic word 'frontier' verbatim
    # in the source (paper1 t27, 2026-07-17 gate: salvi2025 proves the GPT-4
    # persuasion claim but never says 'frontier'). Guard off — the docstring's
    # conservative direction. Single-token runs (Finland) and fully-kept
    # multi-token names (Marilyn Castonguay) are unaffected.
    if len(run) >= 2 and len(kept) == 1:
        return []
    return kept


def _subject_in_source(subj: List[str], src: Dict[str, Any]) -> bool:
    """True when any subject token appears anywhere in the source's full text."""
    hay = _fold(" ".join(s.get("text", "") for s in (src or {}).get("sentences") or []))
    return any(t in hay for t in subj)


def _claim_entity_sets(text: str) -> List[Tuple[str, List[str]]]:
    """Entity token-sets a proving source cannot wholly omit: the LEADING
    subject run (see _subject_tokens) plus every NON-LEADING MULTI-TOKEN
    proper-noun run (2026-07-12 extension, the wildskin/Castonguay class —
    "the film stars Marilyn Castonguay" judged supported from a source that
    never names her). Single-token non-leading runs are NOT used (too alias-
    prone) and adjective compounds ("Egyptian-born") plus common/nationality
    words are filtered — the corpus re-scan with this filter fires on exactly
    the 2 known-correct rows, 0 false (docs/SUBJECT_GUARD.md addendum).
    Returns [(display, [folded tokens])]; [] = guard off."""
    sets: List[Tuple[str, List[str]]] = []
    lead = _subject_tokens(text)
    if lead:
        sets.append((" ".join(lead), lead))
    for s in _named_specifics(text):
        if any(ch.isdigit() for ch in s) or "%" in s:
            continue
        toks = []
        for w in s.split():
            if "-" in w and not w.split("-")[-1][:1].isupper():
                continue                  # Egyptian-born: adjective compound
            lw = w.lower()
            if lw in _SUBJECT_COMMON or lw.rstrip("s") in _SUBJECT_COMMON:
                continue
            if len(w) >= _SUBJECT_MIN_TOKEN:
                toks.append(_fold(w))
        if len(toks) >= 2:
            sets.append((s, toks))
    return sets


# --- Round-7 fix A (owner plan of record; double-confirmed r4 t6 Finland +
# r6 t3 Agta): the named-specific rule was single-vote flash-lite and missed
# the class twice. Two hardenings:
#   1. deterministic entity check — claim-level named specifics (proper-noun
#      runs, years, percentages) that appear in NO kept pick sentence and in
#      no component name become components via regex, not model attention;
#   2. majority-of-3 pick verification for components that carry a named
#      specific (2 extra small calls, only when such components exist).
_SPEC_STOP = {"the", "a", "an", "in", "on", "at", "it", "this", "that", "these",
              "those", "but", "and", "for", "with", "from", "when", "while",
              "however", "although", "moreover", "most", "some", "many", "both",
              "english", "british"}  # broad adjectives that name no checkable place
ENTITY_CHECK_MAX = 2


def _named_specifics(text: str) -> List[str]:
    """Named specifics a shown proof must not silently omit: proper-noun runs
    (skipping sentence-initial words), 4-digit years, percentages."""
    specs: List[str] = []
    punct = "\"'()[]{},.;:!?“”‘’"
    for sent in re.split(r"(?<=[.!?])\s+", text or ""):
        toks = sent.split()
        run: List[str] = []
        for i, tok in enumerate(toks):
            w = tok.strip(punct)
            capped = bool(re.match(r"^[A-Z][a-zA-Z'’-]*$", w))
            if capped and (i > 0 or len(run) > 0) and w.lower() not in _SPEC_STOP:
                run.append(w)
                # trailing punctuation ends the run: '…Hot Mum", Alex Massie'
                # is two entities, not one
                if tok[-1] in punct:
                    specs.append(" ".join(run))
                    run = []
                continue
            if run and w.lower() in ("of", "the"):
                # connector chain inside "Agta of the Philippines": look past
                # one more connector for the next capitalized word
                j = i + 1
                if j < len(toks) and toks[j].strip(punct).lower() in ("of", "the"):
                    j += 1
                if j < len(toks) and re.match(r"^[A-Z]", toks[j].strip(punct)):
                    run.append(w)
                    continue
            if run:
                specs.append(" ".join(run))
            run = []
        if run:
            specs.append(" ".join(run))
    specs += re.findall(r"\b(?:1[6-9]|20)\d{2}\b", text or "")
    specs += re.findall(r"\b\d+(?:\.\d+)?\s?%", text or "")
    seen, out = set(), []
    for s in specs:
        k = s.lower()
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


def _pick_verify_call(claim: str, parts: List[str],
                      picks_by_part: Dict[str, List[int]],
                      covered: List[Dict[str, Any]], llm, prompt: str):
    """One batched pick-verify call over `parts` (global pick numbering)."""
    blocks = []
    for p in parts:
        rows = [f"  [{i}] {covered[i - 1].get('sentence')}"
                for i in picks_by_part.get(_norm_part(p), [])]
        blocks.append(f"COMPONENT: {p}\n" + ("\n".join(rows) if rows
                                             else "  (no picked sentence)"))
    raw = llm.call(prompt.replace("{CLAIM}", claim)
                         .replace("{PICKS}", "\n".join(blocks)),
                   temperature=0.0, max_output_tokens=2048)
    return _parse_pick_verify(raw, parts, len(covered))


def _verify_covering(claim: str, cov: Dict[str, Any], llm, prompt: str,
                     probe=None) -> None:
    """Round-5 fix: ONE batched call auditing the covering block in place.
    (a) drops picks that don't genuinely prove their component (the part is
    re-probed once via escalation, else goes amber); (b) collapses duplicate
    picks to the best one; (c) adds claim-level named specifics the component
    list missed (r4 t6: 'Finnish' never became a component -> false full) and
    probes them; (d) tags proof-less components that are everyday commonplaces
    as `common_knowledge` (owner t1 ruling: grey, not amber; proof_state
    ignores them). Mutates cov; sets cov['pick_verified']=True on success.
    DISPLAY-ONLY — the caller swallows failures; verdicts are never touched."""
    covered = list(cov.get("covered") or [])
    uncovered = [u for u in (cov.get("uncovered") or []) if u]
    parts_order: List[str] = []           # display order: covered parts then uncovered
    picks_by_part: Dict[str, List[int]] = {}
    for i, ce in enumerate(covered, 1):
        p = ce.get("component") or ""
        if p not in parts_order:
            parts_order.append(p)
        picks_by_part.setdefault(_norm_part(p), []).append(i)
    for u in uncovered:
        if u not in parts_order:
            parts_order.append(u)
    if not parts_order:
        cov["pick_verified"] = True
        return
    parsed = _pick_verify_call(claim, parts_order, picks_by_part, covered,
                               llm, prompt)
    if parsed is None:
        cov["pick_verified"] = True       # checked, nothing usable — fail open
        return
    keep = parsed["keep"]
    # Majority-of-3 for named-specific components (round-7 fix A): the drop
    # decision on a "Finland"/"Agta" part is single-vote flaky; two extra
    # votes on JUST those parts stabilize it. A vote that fails to parse or
    # doesn't review the part counts as keep (fail-open, same as the base
    # pass).
    spec_parts = [p for p in parts_order
                  if _named_specifics(p) and picks_by_part.get(_norm_part(p))]
    if spec_parts:
        votes = [parsed]
        for _ in range(2):
            votes.append(_pick_verify_call(claim, spec_parts, picks_by_part,
                                           covered, llm, prompt))
        for p in spec_parts:
            pn = _norm_part(p)
            tallies = {}
            for i in picks_by_part.get(pn, []):
                kept = sum(1 for v in votes
                           if v is None or pn not in v["keep"] or i in v["keep"][pn])
                tallies[i] = kept
            keep[pn] = [i for i, n in tallies.items() if n >= 2]
    new_covered: List[Dict[str, Any]] = []
    dropped_parts: List[str] = []
    for i, ce in enumerate(covered, 1):
        pn = _norm_part(ce.get("component") or "")
        if pn not in keep or i in keep[pn]:   # unreviewed part -> fail open
            new_covered.append(ce)
    still_covered = {_norm_part(ce.get("component") or "") for ce in new_covered}
    for p in parts_order:
        pn = _norm_part(p)
        if picks_by_part.get(pn) and pn not in still_covered and p not in dropped_parts:
            dropped_parts.append(p)
    # A part whose every pick failed verification: one escalation re-probe,
    # else it joins the amber/grey line.
    for p in dropped_parts[:PICK_VERIFY_REPROBE_MAX]:
        found = probe(p) if probe is not None else None
        if found:
            new_covered.append(found)
        elif p not in uncovered:
            uncovered.append(p)
    for p in dropped_parts[PICK_VERIFY_REPROBE_MAX:]:
        if p not in uncovered:
            uncovered.append(p)
    # Named specifics the splitter missed become components: probed, else amber.
    known = {_norm_part(p) for p in parts_order}

    def _near_dup(cand: str) -> bool:
        # A paraphrase of an existing part must not land on the amber line
        # twice (r5 validation: "end grain ... where rainwater collects
        # longest" + "end grain ... collecting rainwater" both listed).
        cw = set(_norm_part(cand).split())
        if not cw:
            return True
        for p in known:
            pw = set(p.split())
            if pw and len(cw & pw) / min(len(cw), len(pw)) >= 0.6:
                return True
        return False

    fresh = [c for c in parsed["new_components"] if not _near_dup(c)]
    for c in fresh[:PICK_VERIFY_NEW_MAX]:
        found = probe(c) if probe is not None else None
        if found:
            new_covered.append(found)
        elif c not in uncovered:
            uncovered.append(c)
    # Deterministic entity check (round-7 fix A): claim-level named specifics
    # must appear in a kept proof sentence or a component name — the LLM pass
    # missed Finland (r4 t6) and Agta/Philippines (r6 t3). Regex, not model
    # attention: an absent specific becomes a component (probed, else amber).
    def _spec_norm(s: str) -> str:
        # "AI-risk" must match "AI risk"; possessives must match their bare
        # form ("The GMB's sections" -> GMB, train-b2 wart 2026-07-12)
        s = re.sub(r"['’]s\b", "", s.lower())
        return re.sub(r"[-–'’]", " ", s)

    have = _spec_norm(" ".join([ce.get("sentence") or "" for ce in new_covered]
                               + list(parts_order) + uncovered))
    ent_added = 0
    for spec in _named_specifics(claim):
        if ent_added >= ENTITY_CHECK_MAX:
            break
        if _spec_norm(spec) in have or _near_dup(spec):
            continue
        ent_added += 1
        found = probe(spec) if probe is not None else None
        if found:
            new_covered.append(found)
        elif spec not in uncovered:
            uncovered.append(spec)
    cov["covered"] = new_covered
    cov["uncovered"] = uncovered
    common_norm = {_norm_part(c) for c in parsed["common_knowledge"]}
    cov["common_knowledge"] = [u for u in uncovered if _norm_part(u) in common_norm]
    cov["pick_verified"] = True


COVER_SPAN_GAP = 8            # used sentences this close belong to one reading span
COVER_SPAN_MAX_CLUSTER = 30   # a single span longer than this falls back to ±2 windows


def _covering_spans(cov: Dict[str, Any], pids: List[str],
                    sources: Dict[str, Dict]) -> List[Dict[str, Any]]:
    """The 'read it in context' view (owner request 2026-07-11, round-2 t8):
    for each cited source, the covering set's used sentences plus ALL the
    original text between them, so a human or a sufficiency judge can read how
    the pieces fit together instead of trusting disjoint quotes. Pure string
    work, no LLM. Used sentences further apart than COVER_SPAN_GAP split into
    separate segments joined by an ellipsis; a runaway segment falls back to
    ±2-sentence windows."""
    used: Dict[str, set] = {}
    for e in (cov.get("covered") or []):
        n = _norm(e.get("sentence") or "")
        if n:
            used.setdefault(e.get("paper_id"), set()).add(n)
    spans = []
    for pid in pids:                      # cited-source order, deterministic
        norms = used.get(pid)
        sents = (sources.get(pid) or {}).get("sentences", []) or []
        if not norms or not sents:
            continue
        idxs = sorted(j for j, s in enumerate(sents)
                      if _norm(s.get("text", "")) in norms)
        if not idxs:
            continue
        clusters, cur = [], [idxs[0]]
        for j in idxs[1:]:
            if j - cur[-1] <= COVER_SPAN_GAP:
                cur.append(j)
            else:
                clusters.append(cur)
                cur = [j]
        clusters.append(cur)
        parts = []
        for cl in clusters:
            lo, hi = cl[0], cl[-1]
            if hi - lo + 1 > COVER_SPAN_MAX_CLUSTER:
                parts.extend(_window(sents, j, radius=2) for j in cl)
            else:
                parts.append(" ".join(s.get("text", "") for s in sents[lo:hi + 1]))
        spans.append({"paper_id": pid,
                      "source_title": (sources.get(pid) or {}).get("title"),
                      "text": " […] ".join(p for p in parts if p),
                      "n_used": len(idxs)})
    return spans


def _evaluate(claim_text: str, pids: List[str], row_for, sources: Dict[str, Dict],
              llm, judgment_prompt: str, extract_prompt: str,
              combined_prompt: str, adhoc_row=None,
              component_rescue: bool = True, split_prompt: str = None) -> Dict[str, Any]:
    """The full grounding chain for ONE claim text against its cited sources:
    cosine candidates -> full-text extraction fallback -> multi-source combined
    judge -> component rescue on a fulltext negative. row_for(pid) returns the
    claim's cosine row for that source (or None); adhoc_row(pid, text) a fresh
    text's sentence row (component probes). component_rescue=False skips the
    rescue (tail-suffix re-evaluations — a rescue-of-rescue is cost without
    signal). Pure function of its inputs (thread-safe); the caller owns verdict
    bookkeeping and output shaping. Called once per claim, and again per tail
    suffix when the tail rescue fires."""
    # Snapshot the client's failure counter: an unsupported verdict minted
    # while model calls were dying is an outage artifact suspect, not a
    # finding — it gets judge_error=True (viewer chip, run-end tally, and
    # rerun.py refuses to reuse it). Under --concurrency another claim's
    # failure can trip this flag too; during an outage that over-flagging is
    # honest — "couldn't fully judge" — and a retry re-judges it for free.
    fails_before = _failed_calls(llm)
    evidences: List[Dict[str, Any]] = []
    for pid in pids:
        src = sources.get(pid)
        row = row_for(pid)
        if src is None or not row:
            continue
        e = _judge_source(claim_text, pid, src, row, llm, judgment_prompt)
        if e is not None:
            evidences.append(e)

    if not evidences:
        return {"verdict": "unsupported", "method": "none",
                "reason": "no_source_sentences (source empty or unreadable)",
                "evidences": [], "used": [], "votes": None}

    combined_votes = None            # tally of the multi-source combined judge, if it ran
    ents, subj_missing = [], {}      # entity-guard state (fulltext paths only)
    supported_entries = [e for e in evidences if e["supported"]]
    if supported_entries:
        verdict, method, reason = "supported", "llm", supported_entries[0]["reason"]
        used = supported_entries
    else:
        # PaperTrail (cosine) could not surface a supporting sentence -> stop trusting the
        # cosine candidates (they routinely miss the real sentence, even near-verbatim ones)
        # and let the LLM read each cited source's FULL text, extract the verbatim
        # supporting sentence(s), and re-judge. Applies to single- AND multi-source claims.
        fb = [e for e in (_extract_evidence(claim_text, pid, sources[pid], llm,
                                            extract_prompt, combined_prompt,
                                            row=row_for(pid))
                          for pid in pids if sources.get(pid) is not None) if e]
        if fb:
            # Show the LLM-found sentences, not cosine's — but when extraction
            # came back EMPTY for a source, keep the candidate stage's closest
            # sentence for it: the reviewer must always get something to read
            # and click through, never a bare "nothing found" (audit t31).
            cand = {e["paper_id"]: e for e in evidences}
            evidences = [e if e.get("sentence") else cand.get(e["paper_id"], e)
                         for e in fb]
        # Entity guard (design notes above _subject_tokens/_claim_entity_sets):
        # a source whose full text omits one of the claim's checkable entities
        # (the leading subject, or any multi-token named entity) cannot
        # single-handedly prove the claim — drop its positive before
        # acceptance. Guarded sources still show their evidence; the combined
        # judge only needs every entity present in the UNION of contributing
        # sources.
        ents = _claim_entity_sets(claim_text)
        subj_missing = {}
        for pid in pids:
            if ents and sources.get(pid) is not None:
                miss = [d for d, toks in ents
                        if not _subject_in_source(toks, sources[pid])]
                if miss:
                    subj_missing[pid] = miss

        def _union_missing(entries):
            srcs = [sources[e["paper_id"]] for e in entries
                    if sources.get(e["paper_id"]) is not None]
            if not (ents and srcs):
                return []
            return [d for d, toks in ents
                    if not any(_subject_in_source(toks, s) for s in srcs)]

        fb_supported = [e for e in fb if e["supported"]
                        and e["paper_id"] not in subj_missing]
        guard_dropped = [e for e in fb if e["supported"]
                         and e["paper_id"] in subj_missing]
        if guard_dropped:
            names = sorted({d for e in guard_dropped
                            for d in subj_missing[e["paper_id"]]})
            logging.info("Entity guard: dropped %d fulltext positive(s) — "
                         "'%s' absent from source text (claim %s)",
                         len(guard_dropped), "; ".join(names), claim_text[:60])
        with_sentence = [e for e in fb if e.get("sentence")]
        if fb_supported:
            # A single source backs it on its own.
            verdict, method, reason = "supported", "llm_fulltext", fb_supported[0]["reason"]
            used = fb_supported
        elif len(with_sentence) >= 2 and not _union_missing(with_sentence):
            # No single source suffices, but several are on-topic -> do they TOGETHER support it?
            ok, reason, votes = _combined_judge(claim_text,
                                                [(_src_label(sources.get(e["paper_id"])) or e["source_title"],
                                                  e["window"]) for e in with_sentence],
                                                llm, combined_prompt)
            verdict, method = ("supported" if ok else "unsupported"), "combined_fulltext"
            combined_votes = votes
            used = with_sentence if ok else []
        elif guard_dropped or (len(with_sentence) >= 2):
            missing_names = sorted({d for m in subj_missing.values() for d in m})
            verdict, method = "unsupported", "llm_fulltext"
            reason = (f"'{ '; '.join(missing_names) }' is never mentioned in "
                      f"the cited source's text — topically similar passages "
                      f"cannot prove a claim about an entity the source does "
                      f"not name")
            used = []
        else:
            verdict, method = "unsupported", "llm_fulltext"
            reason = (with_sentence[0]["reason"] if with_sentence
                      else (fb[0]["reason"] if fb else "no supporting sentence found in any cited source"))
            used = []

    component_check = None
    # Component rescue is pointless (and unsafe) when some claim entity is
    # absent from EVERY cited source — no union of windows can prove it.
    subj_blocked = bool(ents) and any(
        all(not _subject_in_source(toks, sources[pid])
            for pid in pids if sources.get(pid) is not None)
        for _, toks in ents) and any(sources.get(pid) is not None for pid in pids)
    if (verdict == "unsupported" and component_rescue and not subj_blocked
            and method in ("llm_fulltext", "combined_fulltext")):
        base = [(e["source_title"], e["window"]) for e in evidences
                if e.get("window")]
        rescue = _component_rescue(claim_text, pids, sources, llm, extract_prompt,
                                   combined_prompt, reason, base, adhoc_row=adhoc_row,
                                   split_prompt=split_prompt)
        if rescue is not None:
            comp_evs = [{**e, "via": "component_rescue"} for e in rescue["evidence"]]
            component_check = {"found": rescue["found"],
                               "missing": rescue["missing"],
                               "rescued": rescue["flip"],
                               # slim per-component evidence for the card's
                               # explanation block (full entries would collide
                               # with the per-source "supported" semantics)
                               "evidence": [{"component": e.get("component"),
                                             "paper_id": e["paper_id"],
                                             "source_title": e["source_title"],
                                             "sentence": e.get("sentence"),
                                             "page": e.get("page")}
                                            for e in comp_evs]}
            if rescue["flip"]:
                verdict, method = "supported", "component_rescue"
                reason = (rescue["reason"]
                          + " (components verified individually, then the union re-judged "
                          + rescue["votes"] + ")")
                used = comp_evs
                # Show the component evidence, not the failed windows: replace a
                # source's unsupported entry with its component evidence (keep
                # entries for other sources so multi-citation cards stay complete).
                covered = {e["paper_id"] for e in comp_evs}
                evidences = comp_evs + [e for e in evidences
                                        if e["paper_id"] not in covered]

    out = {"verdict": verdict, "method": method, "reason": reason,
           "evidences": evidences, "used": used, "votes": combined_votes}
    if verdict == "unsupported" and _failed_calls(llm) > fails_before:
        out["judge_error"] = True
    if subj_missing:
        # Consumed by arbiter.rescue: a rescue must never re-buy a positive
        # from a source that does not name the claim's entities.
        out["subject_guard"] = {
            "subject": "; ".join(sorted({d for m in subj_missing.values()
                                         for d in m})),
            "missing_from": sorted(subj_missing)}
    # P3 visible caveat: a judge that resolved relative time against the
    # article date prefixes its reason with DATE-INFERRED. Surface it as a
    # flag (viewer chip); the prefix stays in the reason text too.
    if any(str(r).startswith("DATE-INFERRED")
           for r in [reason] + [e.get("reason", "") for e in used]):
        out["date_inferred"] = True
    if any(str(r).startswith("BYLINE-INFERRED")
           for r in [reason] + [e.get("reason", "") for e in used]):
        out["byline_inferred"] = True
    # Deterministic fallback (validation: flash-lite supported essex's "in
    # 2019" via the date header but ignored the prefix instruction): the
    # claim names a year, no shown evidence states it, and a used source's
    # publication date matches it -> the year can only have come from the
    # article date. Flag it.
    if verdict == "supported" and not out.get("date_inferred"):
        years = set(re.findall(r"\b(?:19|20)\d{2}\b", claim_text))
        if years:
            shown = " ".join((e.get("window") or "") + " " + (e.get("sentence") or "")
                             for e in used)
            missing = {y for y in years if y not in shown}
            doc_years = {((sources.get(e.get("paper_id")) or {}).get("doc_date")
                          or "")[:4] for e in used}
            if missing and missing & doc_years:
                out["date_inferred"] = True
    # Same deterministic fallback for bylines: the claim names the article's
    # author, no shown evidence contains the name -> it came from the byline.
    if verdict == "supported" and not out.get("byline_inferred"):
        for e in used:
            author = (sources.get(e.get("paper_id")) or {}).get("doc_author") or ""
            surname = author.split()[-1].lower() if author else ""
            if len(surname) >= 4 and surname in claim_text.lower():
                shown = ((e.get("window") or "") + " "
                         + (e.get("sentence") or "")).lower()
                if surname not in shown:
                    out["byline_inferred"] = True
                    break
    if component_check is not None:
        out["component_check"] = component_check
    return out


def _used_norms_from(claim: Dict[str, Any], sources: Dict[str, Dict]) -> set:
    """Rebuild the coverage 'used sentence' set for a REUSED claim from its stored
    evidences (the live path derives it from _evaluate's `used`, which is not
    persisted). Supported evidence sentences are mapped back to the source
    sentence index by normalized equality, then marked with the same ±1 window."""
    norms: set = set()
    if claim.get("verdict") != "supported":
        return norms
    evs = [e for e in (claim.get("evidences") or []) if e]
    used = [e for e in evs if e.get("supported")]
    if not used:                       # combined verdict: every on-topic passage counted
        used = [e for e in evs if e.get("sentence")]
    for e in used:
        sents = (sources.get(e.get("paper_id")) or {}).get("sentences", []) or []
        n = _norm(e.get("sentence") or "")
        if not n:
            continue
        for j, s in enumerate(sents):
            if _norm(s.get("text", "")) == n:
                for k in (j - 1, j, j + 1):
                    if 0 <= k < len(sents):
                        norms.add(_norm(sents[k].get("text", "")))
                break
    return norms


# How many replacement-source suggestions to attach per unsupported claim (the
# review loop's "wrong source" repair path — pure cosine slicing, no LLM).
ALTERNATIVES_PER_CLAIM = 3


def run(text_claims: List[Dict], sources: Dict[str, Dict], llm, workers: int = 1,
        emb_cache_dir: str = None, reuse: Dict[str, Dict] = None,
        partial_check: bool = False) -> Dict[str, Any]:
    """
    text_claims: [{id, text, markers, paper_ids}]
    sources:     {paper_id: {title, sentences:[{text,page}], claims:[...], ...}}
    workers>1 judges claims concurrently (claims are independent; the calls WITHIN a
    claim stay sequential because candidate judging short-circuits on first support).
    emb_cache_dir: if set, per-source embeddings (sentences + claims) are cached
    there as <pid>.*.npz — SPECTER encoding is the dominant re-run cost, and the
    vectors only change when the source file does.
    reuse: {new_claim_id: previous OUT claim} — incremental re-verification. A
    claim in the map skips the whole grounding chain (zero LLM calls) and keeps
    its previous verdict/evidence; coverage is rebuilt from the stored evidences.
    The caller guarantees the claim text + markers are unchanged.
    """
    reuse = reuse or {}
    from .llm_client import parallel_map
    # Publication dates once per source (P3): judges resolve relative time
    # ("this year") against the article date, with a visible caveat downstream.
    for src in sources.values():
        if "doc_date" not in src:
            src["doc_date"] = _doc_date(src)
        if "doc_author" not in src:
            src["doc_author"] = _doc_author(src)
    judgment_prompt = _load_judgment_prompt()
    extract_prompt = _load_prompt("pt_extract_evidence_prompt.txt")
    combined_prompt = _load_prompt("pt_combined_judgment_prompt.txt")
    covering_prompt = _load_covering_prompt()
    pick_verify_prompt = _load_prompt("pt_pick_verify_prompt.txt")
    component_split_prompt = _load_prompt("pt_component_split_prompt.txt")

    # Tail-rescue suffixes: for every multi-sentence cited claim, precompute the
    # last-k-sentence texts as pseudo-claims so their cosine rows are built here
    # (embedded in the same batch as the real claims — no SPECTER encoding inside
    # the worker threads). A suffix must be a strict subset of the claim; whether
    # a rescue actually runs is decided per claim after the full-text verdict.
    from .text_decomposer import _sentence_split
    tails: List[Dict] = []
    for tc in text_claims:
        if not tc.get("paper_ids") or tc["id"] in reuse:
            continue
        sents = _sentence_split(tc["text"])
        for k in range(1, TAIL_RESCUE_MAX_SUFFIX + 1):
            if len(sents) > k:
                tails.append({"id": f"{tc['id']}#tail{k}",
                              "text": " ".join(sents[-k:]),
                              "paper_ids": tc.get("paper_ids", [])})

    # With a cache dir, embed the user's claims ONCE and slice per source (the
    # uncached path keeps the historical behavior — and stays patchable in tests).
    # user_texts stays claims-only: the omitted-relevance ranking below must not
    # see the tail pseudo-claims.
    user_texts = [tc["text"] for tc in text_claims]
    emb_claims = text_claims + tails
    uidx = {tc["id"]: i for i, tc in enumerate(emb_claims)}
    all_vecs = embeddings.embed([tc["text"] for tc in emb_claims]) \
        if (emb_cache_dir and emb_claims) else None
    user_vecs = all_vecs[:len(text_claims)] if all_vecs is not None else None

    # Precompute, per cited source, cosine(text_claims_citing_it x source_SENTENCES).
    per_source: Dict[str, Dict] = {}
    for pid, src in sources.items():
        citing = [tc for tc in emb_claims if pid in tc.get("paper_ids", [])]
        sents = src.get("sentences", []) or []
        s_texts = [s.get("text", "") for s in sents]
        if citing and s_texts:
            if emb_cache_dir:
                matrix = embeddings.cosine_matrix(
                    [tc["text"] for tc in citing], s_texts,
                    a_vecs=all_vecs[[uidx[tc["id"]] for tc in citing]],
                    b_cache_file=os.path.join(emb_cache_dir, f"{pid}.sents.npz"))
            else:
                matrix = embeddings.cosine_matrix([tc["text"] for tc in citing], s_texts)
        else:
            matrix = []
        per_source[pid] = {"row_of": {tc["id"]: i for i, tc in enumerate(citing)}, "matrix": matrix}

    # Round-2 partial-check escalation ranks a source's decomposed claims by
    # cosine to the user's claim. The vectors live in the same {pid}.claims.npz
    # the omitted-relevance ranking uses, so steady-state this is pure math.
    # The lock only guards the first-run cache MISS (two workers encoding and
    # writing the same file — wasted work); once the file exists it is complete
    # (embed_cached writes atomically via os.replace), so cache hits run
    # lock-free — an always-held lock made the whole escalation stage
    # effectively single-threaded under --concurrency.
    import threading
    _claims_row_lock = threading.Lock()

    def _cached_cos(texts: List[str], b_texts: List[str], a_vecs, cache_name: str):
        cache_file = os.path.join(emb_cache_dir, cache_name)
        if not any(t.strip() for t in b_texts):
            return None
        if os.path.exists(cache_file):
            rel = embeddings.cosine_matrix(texts, b_texts, a_vecs=a_vecs,
                                           b_cache_file=cache_file)
        else:
            with _claims_row_lock:
                rel = embeddings.cosine_matrix(texts, b_texts, a_vecs=a_vecs,
                                               b_cache_file=cache_file)
        return rel[0] if rel else None

    def claims_row_for(pid: str, cid: str):
        if not emb_cache_dir or all_vecs is None:
            return None
        src = sources.get(pid) or {}
        c_texts = [(sc.get("text") or "") for sc in (src.get("claims") or [])]
        i = uidx.get(cid)
        if i is None:
            return None
        return _cached_cos([emb_claims[i]["text"]], c_texts, all_vecs[[i]],
                           f"{pid}.claims.npz")

    def adhoc_rows(pid: str, text: str):
        """Cosine rows of an ad-hoc probe text (a round-3 named component) vs a
        source's decomposed claims and sentences — encodes ONE short text and
        reuses the cached source vectors. (None, None) without a cache dir ->
        lexical-only ranking downstream."""
        if not emb_cache_dir:
            return (None, None)
        src = sources.get(pid) or {}
        c_texts = [(sc.get("text") or "") for sc in (src.get("claims") or [])]
        s_texts = [s.get("text", "") for s in (src.get("sentences") or [])]
        return (_cached_cos([text], c_texts, None, f"{pid}.claims.npz"),
                _cached_cos([text], s_texts, None, f"{pid}.sents.npz"))

    def row_for(pid: str, cid: str):
        pm = per_source.get(pid, {})
        ri = pm.get("row_of", {}).get(cid)
        mat = pm.get("matrix") or []
        return mat[ri] if ri is not None and ri < len(mat) else None

    def _partial_qualifies(out: Dict, tc: Dict) -> bool:
        # Single-citation claims qualify too (owner walkthrough 2026-07-07: t6's
        # EU figure was in no evidence, yet per-source OR said supported). A
        # "combined*" verdict already cleared the every-component bar -> exempt;
        # so did a component rescue (per-component probes + unanimous union judge).
        return (out.get("verdict") == "supported"
                and len(tc.get("paper_ids", [])) >= 1
                and out.get("method") not in ("combined", "combined_fulltext",
                                              "component_rescue"))

    partial_on_reuse: List[str] = []   # reused claims whose nudge pass ran this run

    def _apply_partial(out: Dict, tc: Dict) -> None:
        """Partial-support / over-citation nudges for one supported multi-citation
        claim, marked `partial_checked` so incremental reuse can tell "checked,
        clean" from "never checked"."""
        pids = tc.get("paper_ids", [])
        tail_info = out.get("tail_rescue")
        rescued = bool(tail_info and tail_info.get("supported"))
        judged_text = tail_info["tail"] if rescued else tc["text"]
        rid = f"{tc['id']}#tail{tail_info['reach']}" if rescued else tc["id"]

        def esc_ctx(pid, text, _rid=rid, _jt=judged_text):
            src = sources.get(pid)
            if src is None:
                return ""
            if text == _jt and uidx.get(_rid) is not None:
                # this run's judged claims have precomputed rows; a REUSED
                # tail suffix does not -> ad-hoc encode below
                return _escalated_context(text, src, claims_row_for(pid, _rid),
                                          row_for(pid, _rid))
            return _escalated_context(text, src, *adhoc_rows(pid, text))

        def extract_check(pid, text):
            src = sources.get(pid)
            if src is None:
                return False
            e = _extract_evidence(text, pid, src, llm, extract_prompt,
                                  combined_prompt, row=adhoc_rows(pid, text)[1])
            return bool(e and e.get("supported"))

        def comp_hunt(comps: List[str]) -> List[Dict[str, Any]]:
            """Missing-component hunt: rank the project's OTHER sources by cosine
            of the component vs their cached decomposed-claim vectors, then
            extraction-probe the best two. Runs only when a partial flag actually
            fires (rare by construction), so the cost stays a handful of calls."""
            others = [p for p, s in sources.items() if p not in pids and s is not None]
            results = []
            for comp in comps[:3]:
                v = embeddings.embed([comp]) if emb_cache_dir else None
                scored = []
                for p in others:
                    c_texts = [(sc.get("text") or "") for sc in (sources[p].get("claims") or [])]
                    if not any(t.strip() for t in c_texts):
                        continue
                    if v is not None:
                        r = _cached_cos([comp], c_texts, v, f"{p}.claims.npz")
                        best = max((float(x) for x in (r or [])), default=0.0)
                    else:
                        lex = _lex_scores(comp, c_texts)
                        best = max(lex, default=0.0)
                    if best >= (OFFTOPIC if v is not None else 0.001):
                        scored.append((best, p))
                found = []
                for _, p in sorted(scored, reverse=True)[:COMPONENT_HUNT_SOURCES]:
                    if extract_check(p, comp):
                        found.append({"paper_id": p,
                                      "source_title": (sources[p] or {}).get("title"),
                                      "key": (sources[p] or {}).get("key")})
                results.append({"component": comp, "found_in": found,
                                "searched": min(len(scored), COMPONENT_HUNT_SOURCES)})
            return results

        out.update(_partial_flags(judged_text, pids, sources,
                                  out.get("evidences") or [], llm, combined_prompt,
                                  esc_context=esc_ctx, extract_check=extract_check,
                                  comp_hunt=comp_hunt))
        out["partial_checked"] = True

    covering_on_reuse: List[str] = []  # reused claims whose coverage pass ran this run
    verify_on_reuse: List[str] = []    # reused claims whose pick-verify audit ran this run

    def _judged_text(out: Dict, tc: Dict) -> str:
        tail_info = out.get("tail_rescue")
        if tail_info and tail_info.get("supported"):
            return tail_info["tail"]
        return tc["text"]

    def _set_proof_state(out: Dict) -> None:
        """proof_state = the badge-level summary of the covering pass (round-4
        product fix): "partial" when post-escalation components remain with no
        shown proof, "full" when the covering covers everything. NO covering
        block parsed -> no proof_state key at all (the viewer badge stays
        plain). Pure derivation from `covering` — the `verdict` FIELD NEVER
        CHANGES here (a hard flip manufactures false negatives, audit t8/t28)."""
        cov = out.get("covering")
        if cov:
            common = set(cov.get("common_knowledge") or [])
            real_gaps = [u for u in (cov.get("uncovered") or []) if u not in common]
            out["proof_state"] = "partial" if real_gaps else "full"
        else:
            out.pop("proof_state", None)

    def _apply_covering(out: Dict, tc: Dict) -> None:
        """Covering-set display pass for one SUPPORTED cited claim (design note
        above _covering_candidates). Judges the rescued tail when the claim was
        tail-rescued (the lead-in is the author's own voice, not a component).
        Marked `covering_checked` so incremental reuse can tell "checked, no
        block parsed" from "never checked". Failures are swallowed — this pass
        may never sink a verdict."""
        tail_info = out.get("tail_rescue")
        rescued = bool(tail_info and tail_info.get("supported"))
        judged_text = tail_info["tail"] if rescued else tc["text"]
        rid = f"{tc['id']}#tail{tail_info['reach']}" if rescued else tc["id"]

        def row_fn(pid):
            r = row_for(pid, rid)     # a REUSED tail suffix has no precomputed
            if r is not None:         # row -> fall back to the full claim's row
                return r
            return row_for(pid, tc["id"])

        try:
            cov = _covering_set(judged_text, tc.get("paper_ids", []), sources,
                                row_fn, out.get("evidences") or [], llm,
                                covering_prompt, probe=_make_cover_probe(tc))
            if cov:
                out["covering"] = cov
                _pick_verify(out, tc, judged_text)
                cov["spans"] = _covering_spans(cov, tc.get("paper_ids", []),
                                               sources)
        except Exception as ex:
            logger.warning(f"covering-set pass failed for {tc['id']}: {ex}")
        out["covering_checked"] = True
        _set_proof_state(out)

    def _make_cover_probe(tc: Dict):
        """Uncovered-component escalation (design note on _covering_set): the
        same chunked full-text extraction component-rescue trusts, aimed at
        ONE component, first cited source that proves it wins. A factory so
        the fresh path AND the reuse buy-once path share it."""
        def cover_probe(part):
            for pid in tc.get("paper_ids", []):
                src = sources.get(pid)
                if src is None:
                    continue
                e = _extract_evidence(part, pid, src, llm, extract_prompt,
                                      combined_prompt,
                                      row=adhoc_rows(pid, part)[1])
                if e and e.get("supported") and e.get("sentence"):
                    return {"component": part, "paper_id": pid,
                            "source_title": e.get("source_title"),
                            "sentence": e["sentence"], "page": e.get("page"),
                            "snippet": _snippet(e["sentence"]),
                            "via": "escalation"}
            return None
        return cover_probe

    def _pick_verify(out: Dict, tc: Dict, judged_text: str) -> None:
        """Round-5 audit of the covering block (design note on
        _verify_covering). Failure leaves the block exactly as the covering
        pass built it and is NOT marked verified — the failures-never-cached
        rule retries it on the next run."""
        try:
            _verify_covering(judged_text, out["covering"], llm,
                             pick_verify_prompt, probe=_make_cover_probe(tc))
        except Exception as ex:
            logger.warning(f"pick-verify failed for {tc['id']}: {ex}")

    def process_claim(tc: Dict) -> Tuple[Dict, set]:
        """Full verdict pipeline for ONE claim. Returns (out_claim, used_sentence_norms)
        — no shared state is mutated, so claims can run on parallel threads."""
        prev = reuse.get(tc["id"])
        if prev is not None and not tc.get("missing_files"):
            # Incremental run: text + markers unchanged -> keep the previous
            # verdict verbatim (identity fields refreshed; stale change-tracking
            # dropped). Zero LLM calls for the grounding chain.
            out = {**prev, "id": tc["id"], "text": tc["text"],
                   "markers": tc.get("markers", []),
                   "paper_ids": tc.get("paper_ids", [])}
            out.pop("prev", None)
            if not partial_check:
                # check disabled this run: a carried-over nudge would misreport
                # the current configuration
                for k in ("partial_support", "over_citation", "partial_checked"):
                    out.pop(k, None)
            elif not out.get("partial_checked") and _partial_qualifies(out, tc):
                # The previous run predates the (now default-on) partial check —
                # reuse skips the grounding chain, not the nudge pass. Buy the
                # check once; `partial_checked` carries it forward from here on.
                for k in ("partial_support", "over_citation"):
                    out.pop(k, None)     # pre-ladder flags: recomputed fresh
                _apply_partial(out, tc)
                partial_on_reuse.append(tc["id"])
            if (out.get("verdict") == "supported" and out.get("paper_ids")
                    and not out.get("covering_checked")):
                # The cached verdict predates the covering-set display pass —
                # buy the coverage block once (one small call); the
                # `covering_checked` mark carries it forward from here on.
                _apply_covering(out, tc)
                covering_on_reuse.append(tc["id"])
            elif out.get("verdict") == "supported" and out.get("covering_checked"):
                cov = out.get("covering")
                if cov and not cov.get("pick_verified"):
                    # Cache predates the round-5 pick-verify audit: buy it
                    # once (one call); `pick_verified` carries it forward.
                    _pick_verify(out, tc, _judged_text(out, tc))
                    cov["spans"] = _covering_spans(cov, tc.get("paper_ids", []),
                                                   sources)
                    verify_on_reuse.append(tc["id"])
                # proof_state re-derived either way — pure, zero calls, and the
                # grey (common-knowledge) subtraction may change it.
                _set_proof_state(out)
            return (out, _used_norms_from(out, sources))

        pids = tc.get("paper_ids", [])
        if not pids:
            # Distinguish "author cited nothing" from "cited file isn't in sources/" —
            # the latter is fixable by supplying the file, not by adding a citation.
            missing = tc.get("missing_files")
            if missing:
                return ({**tc, "verdict": "unsupported", "method": "none", "cosine": None,
                         "evidence": None, "evidences": [],
                         "reason": f"source_file_missing: {', '.join(missing)}"}, set())
            # No citation at all: this is the AUTHOR'S OWN claim (thesis, argument,
            # transition) — a separate category, not a red "unsupported": nothing
            # was checked and nothing failed (owner requirement, 2026-07-03).
            return ({**tc, "verdict": "own", "method": "none", "cosine": None,
                     "evidence": None, "evidences": [],
                     "reason": "no_citation_marker"}, set())

        # Full claim through the grounding chain (candidates -> extraction ->
        # combined -> component rescue).
        res = _evaluate(tc["text"], pids, lambda pid: row_for(pid, tc["id"]),
                        sources, llm, judgment_prompt, extract_prompt, combined_prompt,
                        adhoc_row=lambda pid, text: adhoc_rows(pid, text)[1],
                        split_prompt=component_split_prompt)

        if not res["evidences"]:
            return ({**tc, "verdict": "unsupported", "method": "none", "cosine": None,
                     "evidence": None, "evidences": [],
                     "reason": res["reason"]}, set())

        # Tail rescue: the marker scopes over everything since the previous marker,
        # so a failed multi-sentence claim may be a supported cited assertion sunk
        # by its uncited lead-in. Re-judge the last 1..TAIL_RESCUE_MAX_SUFFIX
        # sentences alone (verbatim subsets, same pipeline — no new FP path); the
        # first supported suffix rescues the claim and the lead-in is reported as
        # the author's own framing.
        tail_info = None
        if res["verdict"] == "unsupported":
            sents_split = _sentence_split(tc["text"])
            tried: List[int] = []
            for k in range(1, TAIL_RESCUE_MAX_SUFFIX + 1):
                if len(sents_split) <= k:
                    break
                cid = f"{tc['id']}#tail{k}"
                tried.append(k)
                tr = _evaluate(" ".join(sents_split[-k:]), pids,
                               lambda pid, cid=cid: row_for(pid, cid),
                               sources, llm, judgment_prompt, extract_prompt,
                               combined_prompt, component_rescue=False)
                if tr["verdict"] == "supported":
                    res = tr
                    tail_info = {"supported": True, "reach": k,
                                 "lead_in": " ".join(sents_split[:-k]),
                                 "tail": " ".join(sents_split[-k:])}
                    break
            if tail_info is None and tried:
                tail_info = {"supported": False, "tried": tried}

        verdict, reason, evidences = res["verdict"], res["reason"], res["evidences"]
        method = "tail_rescue" if (tail_info and tail_info["supported"]) else res["method"]
        supported_entries = [e for e in evidences if e["supported"]]

        used_norms: set = set()
        for e in res["used"]:                # mark window sentences as "used" for coverage
            if e.get("j", -1) < 0:
                continue                     # unmapped extraction: j=-1 would wrongly
                                             # mark the source's FIRST sentence
            sents = sources[e["paper_id"]]["sentences"]
            for k in (e["j"] - 1, e["j"], e["j"] + 1):
                if 0 <= k < len(sents):
                    used_norms.add(_norm(sents[k].get("text", "")))

        primary = (supported_entries[0] if supported_entries
                   else max(evidences, key=lambda e: e.get("cosine") or -1))
        # "window" (the judged passage) is kept so the viewer can show the human
        # exactly what the judge read; only the internal index "j" is dropped.
        strip = lambda e: {k: v for k, v in e.items() if k != "j"}
        out = {**tc, "verdict": verdict, "method": method,
               "cosine": primary.get("cosine"),
               "evidence": strip(primary),
               "evidences": [strip(e) for e in evidences],
               "votes": res["votes"],
               "reason": reason}
        if tail_info is not None:
            out["tail_rescue"] = tail_info
        if res.get("date_inferred"):
            out["date_inferred"] = True
        if res.get("byline_inferred"):
            out["byline_inferred"] = True
        if res.get("component_check"):
            out["component_check"] = res["component_check"]
        if res.get("subject_guard"):
            out["subject_guard"] = res["subject_guard"]
        if res.get("judge_error"):
            out["judge_error"] = True
        # A multi-citation claim where SOME cited files are missing still gets
        # judged on the present ones; carry the absent markers so the viewer can
        # show a "source file missing" row instead of silently dropping them
        # (item 16, t14).
        if tc.get("missing_markers"):
            out["missing_markers"] = tc["missing_markers"]

        # Covering-set display pass: a supported card must show the sentences
        # proving EVERY component, or say plainly which parts have no shown
        # evidence (design note above _covering_candidates). Never a verdict.
        if verdict == "supported":
            _apply_covering(out, tc)

        # Partial-support / over-citation nudges (multi-citation claims only). The
        # per-source OR that produces a "supported" verdict passes the whole claim
        # as soon as ANY one cited source backs its fragment — so a compound
        # sentence can read as fully grounded when a specific component (a number,
        # an attribution) is in none of the cited sources (audit: t69's IEA
        # figure). _partial_flags runs the ALCE-style union recall check with
        # hybrid retrieval + NEI escalation (design note above it); flags are a
        # NUDGE, never a veto — the verdict stays supported (a hard flip wrongly
        # rejects framing-heavy claims a human confirmed — audit t8/t28). A
        # "combined*" verdict already cleared the every-component bar → exempt.
        if partial_check and _partial_qualifies(out, tc):
            _apply_partial(out, tc)
        return (out, used_norms)

    # Per-claim progress: a slow run (esp. the claude-code backend, seconds per
    # judge call × dozens of claims) otherwise looks frozen. Log each completion
    # as it lands (parallel_map returns in order, but claims finish out of order —
    # the counter reflects real progress). Thread-safe; instant on reused claims.
    _n_total = len(text_claims)
    _done = {"n": 0}
    _done_lock = threading.Lock()

    def _process_with_progress(tc: Dict) -> Tuple[Dict, set]:
        r = process_claim(tc)
        with _done_lock:
            _done["n"] += 1
            n = _done["n"]
        logger.info(f"judged claim {n}/{_n_total}")
        return r

    results = parallel_map(_process_with_progress, text_claims, workers)
    if covering_on_reuse:
        logger.info(f"Coverage display pass ran on {len(covering_on_reuse)} reused "
                    f"claim(s) whose cached verdict predates it (one small call "
                    f"each, once): {', '.join(sorted(covering_on_reuse)[:8])}"
                    + ("…" if len(covering_on_reuse) > 8 else ""))
    if verify_on_reuse:
        logger.info(f"Pick-verify audit ran on {len(verify_on_reuse)} reused "
                    f"claim(s) whose cached covering predates it (one small call "
                    f"each, once): {', '.join(sorted(verify_on_reuse)[:8])}"
                    + ("…" if len(verify_on_reuse) > 8 else ""))
    if partial_on_reuse:
        logger.info(f"Partial-support check ran on {len(partial_on_reuse)} reused "
                    f"claim(s) whose previous run predates it (a few judge calls "
                    f"each, once): {', '.join(sorted(partial_on_reuse)[:8])}"
                    + ("…" if len(partial_on_reuse) > 8 else ""))
    out_claims = [r[0] for r in results]
    supporting_norm: set = set().union(*(r[1] for r in results)) if results else set()

    # Omitted (cherry-picking): source atomic claims whose evidence sentences were not
    # among the sentences that supported any of your claims.
    omitted: List[Dict] = []
    cited_pids = {pid for tc in text_claims for pid in tc.get("paper_ids", [])}
    used: Dict[str, set] = {pid: set() for pid in cited_pids}
    for pid in cited_pids:
        src = sources.get(pid, {})
        for j, sc in enumerate(src.get("claims", [])):
            ev_list = sc.get("evidence", []) or []
            if any(_norm(s) in supporting_norm for s in ev_list):
                used[pid].add(j)
            else:
                osent = ev_list[0] if ev_list else sc.get("text", "")
                omitted.append({
                    "paper_id": pid,
                    "source_title": src.get("title"),
                    "source_claim_id": sc.get("id"),
                    "_j": j,
                    "text": sc.get("text"),
                    "evidence": ev_list,
                    "page": (sc.get("evidence_pages") or [None])[0],
                    "snippet": _snippet(osent),
                })

    # Rank omitted source-claims by relevance to the user's text (max cosine to any of
    # the user's claims). SPECTER's absolute cosine is compressed and text-dependent, so
    # we rank rather than hard-threshold — the viewer surfaces the most-relevant unused
    # claims (the real cherry-picking signal) and collapses the irrelevant long tail.
    if omitted:
        if emb_cache_dir and user_texts:
            # Cached path: a source's claim texts are stable, so embed ALL of them
            # once per source (cached) and read the omitted subset by claim index —
            # instead of re-encoding ~30k omitted texts every run.
            for pid in cited_pids:
                olist = [o for o in omitted if o["paper_id"] == pid]
                c_texts = [(sc.get("text") or "") for sc in sources.get(pid, {}).get("claims", [])]
                if not olist or not c_texts:
                    continue
                rel = embeddings.cosine_matrix(
                    user_texts, c_texts, a_vecs=user_vecs,
                    b_cache_file=os.path.join(emb_cache_dir, f"{pid}.claims.npz"))
                for o in olist:
                    col = [rel[r][o["_j"]] for r in range(len(user_texts))]
                    o["relevance"] = round(max(col), 4) if col else None
        else:
            om_texts = [o.get("text") or "" for o in omitted]
            rel_matrix = embeddings.cosine_matrix(user_texts, om_texts) if user_texts and om_texts else []
            for j, o in enumerate(omitted):
                col = [rel_matrix[r][j] for r in range(len(user_texts))] if rel_matrix else []
                o["relevance"] = round(max(col), 4) if col else None
        omitted.sort(key=lambda o: (o.get("relevance") is None, -(o.get("relevance") or 0)))
    for o in omitted:
        o.pop("_j", None)
        o.setdefault("relevance", None)

    # Alternatives (review loop, "wrong source" repairs): for each judged-
    # unsupported cited claim, the closest source claims from the OTHER cited
    # sources in this run — the repair brief offers them as re-citation
    # candidates. Pure cosine over already-cached vectors; no LLM calls.
    alt_rows = [i for i, c in enumerate(out_claims)
                if c["verdict"] == "unsupported" and c.get("paper_ids")
                and not str(c.get("reason", "")).startswith("source_file_missing")]
    if alt_rows and user_texts:
        cands: Dict[int, List[Dict]] = {i: [] for i in alt_rows}
        for pid in cited_pids:
            src = sources.get(pid, {})
            claims_list = src.get("claims", []) or []
            c_texts = [(sc.get("text") or "") for sc in claims_list]
            rows = [i for i in alt_rows if pid not in out_claims[i].get("paper_ids", [])]
            if not rows or not any(t.strip() for t in c_texts):
                continue
            if emb_cache_dir:
                rel = embeddings.cosine_matrix(
                    user_texts, c_texts, a_vecs=user_vecs,
                    b_cache_file=os.path.join(emb_cache_dir, f"{pid}.claims.npz"))
            else:
                rel = embeddings.cosine_matrix(user_texts, c_texts)
            for i in rows:
                row = rel[i]
                top = sorted(range(len(row)), key=lambda j: -row[j])
                for j in top[:ALTERNATIVES_PER_CLAIM]:
                    sc = claims_list[j]
                    if not (sc.get("text") or "").strip():
                        continue
                    cands[i].append({
                        "paper_id": pid, "source_title": src.get("title"),
                        "text": sc.get("text"),
                        "evidence": (sc.get("evidence") or [None])[0],
                        "relevance": round(float(row[j]), 4)})
        for i in alt_rows:
            best = sorted(cands[i], key=lambda a: -(a["relevance"] or 0))
            if best:
                out_claims[i]["alternatives"] = best[:ALTERNATIVES_PER_CLAIM]

    coverage = {"per_source": {}, "totals": {
        "claims": len(text_claims),
        "supported": sum(1 for c in out_claims if c["verdict"] == "supported"),
        "unsupported": sum(1 for c in out_claims if c["verdict"] == "unsupported"),
        "own": sum(1 for c in out_claims if c["verdict"] == "own"),
        "omitted": len(omitted),
    }}
    # Per-source citation stats (owner walkthrough item 6): "used" alone reads as
    # "source useless" when it is 0, but most zeros are structural — the citing
    # claims came out unsupported, or a co-cited source supplied the winning
    # evidence. Count both so the viewer can say WHY a bar is empty. `supported`
    # counts claims where THIS source's own evidence was judged supporting —
    # also the honest headline when the decomposed-claims mapping undercounts
    # `used` (4 such sources in the paper1 audit: winning evidence sentences
    # that no decomposed source-claim's evidence list covers).
    cited_by = {pid: 0 for pid in cited_pids}
    citing_supported = {pid: 0 for pid in cited_pids}
    won = {pid: 0 for pid in cited_pids}
    for c in out_claims:
        c_pids = set(c.get("paper_ids", []))
        for pid in c_pids & set(cited_by):
            cited_by[pid] += 1
            if c["verdict"] == "supported":
                citing_supported[pid] += 1
        if c["verdict"] == "supported":
            for pid in {e["paper_id"] for e in (c.get("evidences") or [])
                        if e and e.get("supported")} & set(won):
                won[pid] += 1
    for pid in cited_pids:
        src = sources.get(pid, {})
        total = len(src.get("claims", []))
        coverage["per_source"][pid] = {
            "title": src.get("title"),
            "total_source_claims": total,
            "used": len(used.get(pid, set())),
            "cited_by": cited_by[pid],
            "citing_supported": citing_supported[pid],
            "supported": won[pid],
        }

    return {"text_claims": out_claims, "omitted": omitted, "coverage": coverage}
