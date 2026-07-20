"""Claim-origin tracing — is the cited paper the REAL source? (Stream B).

=== DESIGN STUB (Fable sprint 7/5–7/7). Interfaces first; fill the bodies. ===

WHAT THIS IS
    A supported verdict says "the cited paper backs this claim." But the cited
    paper may itself be citing someone else — it's a relay, not the origin. This
    module checks whether the supporting sentence is ORIGINAL to the cited paper
    or derivative, and if derivative, follows the citation to the real source and
    recurses. Emits a provenance chain (the FLF "claim genealogy" ask, and it
    feeds the nanopub/PROV-O output idea in IDEAS.md).

INPUT (read-only)
    A supported claim from analysis.json (id, text, paper_ids, evidence) + the
    cited source's full text (already on disk in the run's sources/ dir; reuse
    the source's decomposed claims/sentences from source_claims/ cache).

THE RECURSION
    For a cited paper P backing claim C:
      1. Locate the supporting passage in P (we already extracted `evidence`).
      2. Judge: is this passage P's OWN finding/assertion, or is P attributing it
         to another work (a citation marker near the passage, "as X showed",
         "following Y")? -> one LLM call (this stream owns pt_origin_*.txt).
      3. If ORIGINAL  -> stop; P is the origin. Record it.
         If DERIVATIVE -> resolve the referenced work (parse P's bibliography for
         the referenced entry; find it via semantic_scholar_api.find_paper_by_title
         / DOI), fetch it (direct_downloader.download_source), recurse on the
         SAME claim against the newly fetched source.
      Stop conditions (ALL apply): reached a primary source (original) · hit
      max_depth · low confidence on the "derivative?" judgment · can't fetch the
      next source (record "trail broken here", don't guess).
    DEFAULT DEPTH = 2, configurable (owner: "how many layers?" -> 2, tunable
    after the spike; open decision in COMPETITION_PLAN.md).

SHARED CORE: uses paper_search.neighbors() / the same fetch+resolve primitives
as snowball search — build the traversal ONCE. Origin-trace = directed walk up
the "references" edges toward the primary source; snowball = broader relevance
walk. Same plumbing.

OUTPUT (additive; do NOT change the original verdict)
    origin_trace.json next to analysis.json:
      { "<claim id>": {
          "chain": [ {"paper_id","title","role":"cited"|"relay"|"origin",
                      "passage","attribution":"own"|"cites:<ref>",
                      "confidence": 0.0-1.0} , ... ],
          "origin_found": true|false,
          "stopped_because": "primary"|"max_depth"|"low_conf"|"unfetchable",
          "depth": <n>, "model": "...", "prompt_sha": "..." } }
    Viewer (later, Stream A/D territory) can show a "traces to: <origin>" chip and
    the chain. A relay-not-origin finding is a NUDGE (surfaces weak provenance),
    never flips the supported verdict.

BUDGET: ~1 "derivative?" call per hop; depth 2 => ~2-3 calls per traced claim.
Only trace claims the user opts into (a viewer mark) or a small top-N — do NOT
trace every claim by default. Cache each hop's judgment on disk keyed by
(claim id, paper_id, model, prompt_sha) so re-runs are free.

NON-BLOCKING: experimental Track `streamB`. Merges only when green. Dropped
before crux/argmap if Fable time runs short (order: snowball → origin-trace →
crux → argmap). Never mutates verdicts, matcher, or existing prompts.
"""
import os
import re
import json
import hashlib
import logging

from typing import Any, Dict, List, Optional

from . import semantic_scholar_api as s2
from . import paper_search

logger = logging.getLogger(__name__)

PROMPT_FILE = "pt_origin_attribution_prompt.txt"   # this stream authors it
DEFAULT_MAX_DEPTH = 2
ATTRIBUTION = ("own", "derivative")                # LLM verdict on a passage
STOP_REASONS = ("primary", "max_depth", "low_conf", "unfetchable")

CONF_THRESHOLD = 0.55        # below this we stop rather than guess the next hop
_SOURCE_CTX_CHARS = 4000     # how much source context the judge sees
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Prompt loading / hashing
# ---------------------------------------------------------------------------

def _load_prompt() -> str:
    path = os.path.join(_PROJECT_ROOT, "config", "prompts", PROMPT_FILE)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _prompt_sha() -> str:
    try:
        return hashlib.sha1(_load_prompt().encode("utf-8")).hexdigest()[:12]
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# The one LLM call: own vs derivative
# ---------------------------------------------------------------------------

def judge_attribution(claim_text: str, passage: str, source_text: str,
                      llm) -> Dict[str, Any]:
    """One LLM call: is `passage` the source's OWN assertion, or attributed to
    another work? Returns {attribution, cited_ref, confidence, reason}. A failed
    or unparseable judgment returns attribution='unknown', confidence=0.0 so the
    caller stops ('low_conf') instead of guessing."""
    prompt = (_load_prompt()
              .replace("{CLAIM}", claim_text or "")
              .replace("{PASSAGE}", passage or "")
              .replace("{SOURCE}", (source_text or "")[:_SOURCE_CTX_CHARS]))
    resp = None
    try:
        resp = llm.call_json(prompt)
    except Exception as e:
        logger.warning("attribution judge failed: %s", e)
    if not isinstance(resp, dict):
        return {"attribution": "unknown", "cited_ref": None,
                "confidence": 0.0, "reason": "judge returned no parseable JSON"}
    attribution = str(resp.get("attribution", "")).strip().lower()
    if attribution not in ATTRIBUTION:
        attribution = "unknown"
    try:
        confidence = float(resp.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    cited_ref = resp.get("cited_ref")
    if isinstance(cited_ref, str) and cited_ref.strip().lower() in ("", "null", "none"):
        cited_ref = None
    return {"attribution": attribution, "cited_ref": cited_ref,
            "confidence": max(0.0, min(1.0, confidence)),
            "reason": str(resp.get("reason", ""))[:300]}


def _cached_attribution(claim: Dict[str, Any], node: Dict[str, Any], llm,
                        cache_dir: Optional[str]) -> Dict[str, Any]:
    """judge_attribution wrapped in a disk cache keyed by (claim id, paper_id,
    model, prompt_sha) — re-runs are free, per the budget rule."""
    model = getattr(llm, "model", None) or "unknown"
    key = f"{claim.get('id')}|{node.get('paper_id')}|{model}|{_prompt_sha()}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    path = os.path.join(cache_dir, f"origin_attr__{digest}.json") if cache_dir else None
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    result = judge_attribution(claim.get("text", ""), node.get("passage", ""),
                               node.get("text", ""), llm)
    # Never cache a failure: the unknown/0.0 shape is judge_attribution's
    # failed-call sentinel — caching it would make every future run reuse the
    # failure "for free" instead of retrying (evidence_independence's
    # cacheable-predicate rule, applied here too).
    failed = (result.get("attribution") == "unknown"
              and not float(result.get("confidence") or 0.0))
    if path and not failed:
        os.makedirs(cache_dir, exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=1)
        except Exception as e:
            logger.debug("could not cache attribution: %s", e)
    return result


# ---------------------------------------------------------------------------
# Resolve a referenced work from the source's bibliography (no LLM, no guess)
# ---------------------------------------------------------------------------

_DOI_RE = re.compile(r"doi:?\s*(10\.\d{4,}[^\s)]*(?:\s?[^\s)]+)*)", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _ref_number(cited_ref: str) -> Optional[int]:
    """The bibliography number in a NUMBERED citation marker ('[12]', '12',
    'ref 12', '[12, 14]' -> 12). Anchored: an author-year string like
    'Smith et al. (2020)' must yield None (the year's digits are not a ref
    number), so the caller stops 'unfetchable' instead of following a wrong
    bibliography entry."""
    if not cited_ref:
        return None
    s = cited_ref.strip()
    m = re.fullmatch(r"(?:refs?\.?|reference)?\s*\[?\s*(\d{1,3})"
                     r"(?:\s*[,;–-]\s*\d{1,3})*\s*\]?\.?", s, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _bib_entry_by_number(source_text: str, n: int) -> Optional[str]:
    """Return the raw text of numbered bibliography entry `n`. Picks the LAST
    occurrence of an ` n. ` boundary (the reference list sits at the end, so this
    avoids inline hits like 'Figure 5.'), and slices to the next boundary."""
    boundaries = [(int(m.group(1)), m.start(), m.end())
                  for m in re.finditer(r"(?<![\d.])(\d{1,3})\.\s+(?=[A-Z(])", source_text)]
    if not boundaries:
        return None
    starts = sorted(b[1] for b in boundaries)
    hits = [b for b in boundaries if b[0] == n]
    if not hits:
        return None
    _, _, content_start = hits[-1]           # last = the bibliography entry
    nxt = next((s for s in starts if s > content_start), len(source_text))
    chunk = source_text[content_start:nxt].strip()
    return chunk or None


def _extract_doi(text: str) -> Optional[str]:
    m = _DOI_RE.search(text or "")
    if not m:
        return None
    doi = re.sub(r"\s+", "", m.group(1)).rstrip(").,;")
    return doi or None


def _extract_title(entry: str) -> Optional[str]:
    """Heuristic title = the sentence right after the year in a bib entry."""
    ym = _YEAR_RE.search(entry or "")
    if not ym:
        return None
    after = entry[ym.end():].lstrip(" .")
    # title runs to the venue abbreviation — cut at the first '. ' boundary that
    # is followed by a capital (venue) or at a double-space.
    parts = re.split(r"\.\s+", after)
    for p in parts:
        words = p.strip().split()
        if len(words) >= 3:
            return p.strip().rstrip(".")
    return None


def resolve_referenced(cited_ref: str, source_text: str) -> Optional[Dict[str, Any]]:
    """Resolve the reference a passage points to, to a fetchable descriptor from
    the source's own bibliography. Returns {ref_num, raw, doi, title, year,
    authors} or None (-> caller stops 'unfetchable'; NEVER guesses)."""
    if not cited_ref or not source_text:
        return None
    n = _ref_number(cited_ref)
    if n is None:
        return None                          # author-year resolution: TODO, not guessed
    entry = _bib_entry_by_number(source_text, n)
    if not entry:
        return None
    ym = _YEAR_RE.search(entry)
    year = ym.group(0) if ym else None
    authors = entry[:ym.start()].strip(" .") if ym else None
    doi = _extract_doi(entry)
    title = _extract_title(entry)
    if not doi and not title:
        return None                          # nothing resolvable -> unfetchable
    return {"ref_num": n, "raw": entry[:400], "doi": doi,
            "title": title, "year": year, "authors": authors}


# ---------------------------------------------------------------------------
# Resolve a referenced descriptor to a real paper (S2; abstract = next passage)
# ---------------------------------------------------------------------------

def _s2_paper_from_ref(ref: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Look the referenced work up on Semantic Scholar (by DOI, else confident
    title match). Returns a paper with an abstract, or None. No LLM."""
    doi = ref.get("doi")
    if doi:
        page = paper_search._s2_get(
            f"{paper_search._S2_GRAPH}/paper/DOI:{doi}",
            {"fields": "paperId,title,abstract,year,externalIds,authors"})
        if page and page.get("title"):
            return _norm_paper(page)
    title = ref.get("title")
    if title:
        paper, status = s2.find_paper_by_title(title, ref.get("year"))
        if paper:
            return _norm_paper(paper)
    return None


def _norm_paper(p: Dict[str, Any]) -> Dict[str, Any]:
    ext = p.get("externalIds") or {}
    return {"paper_id": p.get("paperId"),
            "title": p.get("title"),
            "abstract": p.get("abstract"),
            "year": p.get("year"),
            "doi": ext.get("DOI")}


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------

def _claim_passage(claim: Dict[str, Any], pid: Optional[str]) -> str:
    """The supporting passage to judge: the primary evidence window/sentence."""
    ev = claim.get("evidence") or {}
    for e in (claim.get("evidences") or []):
        if pid is None or e.get("paper_id") == pid:
            ev = e
            break
    return ev.get("window") or ev.get("sentence") or claim.get("text", "")


def _cited_paper_id(claim: Dict[str, Any]) -> Optional[str]:
    ev = claim.get("evidence") or {}
    if ev.get("paper_id"):
        return ev["paper_id"]
    pids = claim.get("paper_ids") or []
    return pids[0] if pids else None


def trace_claim(claim: Dict[str, Any], sources_dir: str, llm,
                sources: Optional[List[Dict[str, Any]]] = None,
                source_texts: Optional[Dict[str, Dict[str, str]]] = None,
                max_depth: int = DEFAULT_MAX_DEPTH,
                cache_dir: Optional[str] = None) -> Dict[str, Any]:
    """Walk one supported claim up toward its origin. Read-only on `claim` — it
    NEVER flips the verdict; the result is a separate provenance artifact.

    source_texts[paper_id] = {"title":..., "text":...} supplies the cited
    source's body (from the run's source_claims cache); deeper hops judge over
    the referenced work's S2 abstract. Returns the per-claim chain payload."""
    source_texts = source_texts or {}
    pid = _cited_paper_id(claim)
    meta = source_texts.get(pid, {})
    current = {"paper_id": pid,
               "title": meta.get("title") or _title_from_sources(sources, pid),
               "passage": _claim_passage(claim, pid),
               "text": meta.get("text", ""),
               "url": None, "doi": None}

    chain: List[Dict[str, Any]] = []
    origin_found = False
    stopped = None

    for depth in range(max_depth + 1):
        attr = _cached_attribution(claim, current, llm, cache_dir)
        is_own = attr["attribution"] == "own"
        role = "origin" if is_own else ("cited" if depth == 0 else "relay")
        chain.append({
            "paper_id": current["paper_id"],
            "title": current["title"],
            "role": role,
            "passage": (current["passage"] or "")[:400],
            "attribution": "own" if is_own else (f"cites:{attr['cited_ref']}"
                                                 if attr["cited_ref"] else attr["attribution"]),
            "confidence": attr["confidence"],
            "reason": attr["reason"],
            "url": current.get("url"),
            "doi": current.get("doi"),
        })

        if attr["confidence"] < CONF_THRESHOLD:
            stopped = "low_conf"
            break
        if is_own:
            origin_found = True
            stopped = "primary"
            break
        if attr["attribution"] != "derivative":   # 'unknown' with high conf shouldn't happen
            stopped = "low_conf"
            break
        if depth == max_depth:
            stopped = "max_depth"
            break

        ref = resolve_referenced(attr["cited_ref"], current["text"])
        if not ref:
            stopped = "unfetchable"
            break
        paper = _s2_paper_from_ref(ref)
        if not paper or not paper.get("abstract"):
            stopped = "unfetchable"
            break
        doi = paper.get("doi") or ref.get("doi")
        url = (f"https://doi.org/{doi}" if doi
               else (f"https://www.semanticscholar.org/paper/{paper['paper_id']}"
                     if paper.get("paper_id") else None))
        current = {"paper_id": paper["paper_id"] or ref.get("doi") or paper["title"],
                   "title": paper["title"],
                   "passage": paper["abstract"],   # judge the origin over its abstract
                   "text": paper["abstract"],
                   "url": url, "doi": doi}

    return {"chain": chain,
            "origin_found": origin_found,
            "stopped_because": stopped,
            "depth": len(chain) - 1,
            "model": getattr(llm, "model", None),
            "prompt_sha": _prompt_sha()}


def _title_from_sources(sources, pid) -> Optional[str]:
    for s in (sources or []):
        if s.get("paper_id") == pid:
            return s.get("title")
    return None


# ---------------------------------------------------------------------------
# Batch driver (opt-in claims only — never trace everything by default)
# ---------------------------------------------------------------------------

def _load_source_texts(analysis: Dict[str, Any], sources_dir: str) -> Dict[str, Dict[str, str]]:
    """Reconstruct each cited source's title + body from the run's source_claims
    cache (sibling of sources_dir), for hop-0 attribution judging."""
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(sources_dir)), "source_claims")
    out: Dict[str, Dict[str, str]] = {}
    for s in (analysis.get("sources") or []):
        pid = s.get("paper_id")
        if not pid:
            continue
        path = os.path.join(cache_dir, f"{pid}.json")
        text, title = "", s.get("title")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    c = json.load(f)
                text = "\n".join(x.get("text", "") for x in c.get("sentences", []))
                title = c.get("title") or title
            except Exception as e:
                logger.debug("could not read source cache %s: %s", path, e)
        out[pid] = {"title": title, "text": text}
    return out


def trace_run(analysis: Dict[str, Any], sources_dir: str, llm,
              claim_ids: Optional[List[str]] = None,
              max_depth: int = DEFAULT_MAX_DEPTH,
              cache_dir: Optional[str] = None,
              out_path: Optional[str] = None,
              viewer_path: Optional[str] = None,
              viewer_title: str = "Claim origin trace") -> Dict[str, Any]:
    """Trace a SELECTED set of claims (opt-in ids only — never all, per budget).
    Returns {claim_id: chain payload} and, when out_path is given, writes it as
    origin_trace.json. When viewer_path is given, also renders a self-contained
    HTML viewer. Additive: does not touch analysis.json or any verdict."""
    if not claim_ids:
        logger.info("trace_run: no claim_ids given — tracing nothing "
                    "(origin-trace is opt-in, never all claims by default)")
        return {}
    wanted = set(claim_ids)
    source_texts = _load_source_texts(analysis, sources_dir)
    by_id = {c.get("id"): c for c in analysis.get("text_claims", [])}
    results: Dict[str, Any] = {}
    for cid in claim_ids:
        claim = by_id.get(cid)
        if claim is None:
            logger.warning("trace_run: claim %s not found", cid)
            continue
        if claim.get("verdict") != "supported":
            logger.info("trace_run: skipping %s (verdict=%s; only 'supported' "
                        "claims have an origin to trace)", cid, claim.get("verdict"))
            continue
        results[cid] = trace_claim(claim, sources_dir, llm, sources=analysis.get("sources"),
                                   source_texts=source_texts, max_depth=max_depth,
                                   cache_dir=cache_dir)
    if out_path:
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=1)
            logger.info("wrote %s (%d claims traced)", out_path, len(results))
        except Exception as e:
            logger.error("could not write %s: %s", out_path, e)
    if viewer_path:
        try:
            from . import origin_viewer
            run_id = (analysis.get("metadata", {}) or {}).get("timestamp")
            origin_viewer.generate(results, analysis, viewer_path,
                                   title=viewer_title, run_id=run_id)
            logger.info("wrote %s", viewer_path)
        except Exception as e:
            logger.error("could not write viewer %s: %s", viewer_path, e)
    return results
