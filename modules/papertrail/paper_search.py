"""Human-like snowball paper search (Stream B).

=== DESIGN STUB (Fable sprint 7/5–7/7). Interfaces first; fill the bodies. ===

WHAT THIS IS
    A researcher doesn't keyword-search once and stop. They read a few abstracts,
    pick the paper closest to what they need (or the one everyone cites), then
    follow ITS citations/references outward and repeat until the trail runs dry.
    This module automates that loop: keywords → abstracts → pick closest /
    likely-to-cite → traverse citation graph → recurse to a relevance target.

WHY BUILD IT (research finding, 2026-07-05 — docs/CLAUDE_SKILLS_RESEARCH.md)
    Existing MCP servers (paper-search-mcp [MIT, 25+ sources], the Semantic
    Scholar MCPs) and our own semantic_scholar_api.py all do DISCOVERY by
    keyword/DOI/author — but NONE do automated citation-graph traversal, and
    /deep-research fans out web search without walking a citation graph. The
    snowball loop is the genuinely net-new piece; discovery underneath it is
    reused, not reinvented.

REUSE (don't reinvent discovery or fetching)
    modules/papertrail/semantic_scholar_api.py:
        search_papers(query, limit, ...)      -> [paper dict]  (keyword search)
        find_paper_by_title(title, year=None)  -> paper | None
        enrich_entry_from_s2(entry, paper)     -> entry
    Semantic Scholar's graph API also returns a paper's `references` and
    `citations` (add a thin fetch here; S2 field `references`/`citations`).
    OpenAlex is the free fallback for citation edges (no key, generous limits).
    modules/papertrail/direct_downloader.py: download_source(entry, ...) to pull
    a chosen paper's PDF/text when we want to read past the abstract.

THE LOOP (agentic but bounded — S2 is rate-limited, budget is ~$0)
    seed = search_papers(keywords)                       # a handful
    frontier = pick_relevant(seed, target, k)            # LLM or cosine rank
    for depth in range(max_depth):                       # default 2
        next = []
        for paper in frontier:
            neighbors = references(paper) + citations(paper)
            next += pick_relevant(neighbors, target, k)
        record provenance edges (who led to whom, why)
        if not next or budget/time exhausted: break
        frontier = dedupe(next) - seen
    Relevance = cheap first: SPECTER cosine (embeddings.py) of abstract vs the
    target text; an optional tiny LLM "is this on-target / likely to cite?"
    gate for the top candidates only.

OUTPUT
    A ranked candidate list + the traversal graph (for provenance / the viewer
    review loop, Stream D):
      { "target": "<what we searched for>",
        "candidates": [ {"paper_id","title","year","abstract","doi","url",
                         "relevance","found_via":[<paper_id path>],
                         "reason":"<why picked>"} , ... ],
        "edges": [ {"from":<paper_id>,"to":<paper_id>,"kind":"cites"|"cited_by"} ],
        "seeds": [...], "max_depth": 2 }
    Shares the citation-traversal CORE with origin_trace.py — build ONE traversal
    primitive (fetch neighbors + rank + recurse) and have both call it.

INTEGRATION: Stream D wires this into the viewer "mark → find new sources" loop
and hands winners to download_sources.py. Keep this module pure logic (no viewer,
no CLI) so Stream D can call it and tests can drive it offline with fixtures.

NON-BLOCKING: experimental Track `streamB`. Merges only when green. Snowball is
DROPPED FIRST if Fable time runs short (drop order: snowball → origin-trace →
crux → argmap). Respect S2 rate limits; cache neighbor lookups on disk by
paper_id (model-agnostic, like source decomposition) to keep re-runs free.
"""
import os
import re
import json
import time
import random
import logging

import requests

from typing import Any, Dict, List, Optional

from . import semantic_scholar_api as s2
from . import embeddings   # cheap top-level import; sentence-transformers loads lazily
# from . import direct_downloader   # (Stream D: fetch past the abstract, later)

logger = logging.getLogger(__name__)

DEFAULT_MAX_DEPTH = 2
DEFAULT_BRANCHING = 5      # candidates kept per node per level

# --- citation-graph fetch config -------------------------------------------
DIRECTIONS = ("references", "citations", "both")
_S2_GRAPH = "https://api.semanticscholar.org/graph/v1"
# Reference-level fields (intents/isInfluential) + the neighbor paper's fields.
_NEIGHBOR_FIELDS = ("intents,isInfluential,title,abstract,year,externalIds,"
                    "authors,openAccessPdf,citationCount")
_INNER_KEY = {"references": "citedPaper", "citations": "citingPaper"}
_S2_PAGE = 500            # per-page pull (S2 max is 1000)
MAX_NEIGHBORS = 1000      # bound the walk; citations can be enormous
_OPENALEX = "https://api.openalex.org"
# A contact address makes OpenAlex/Unpaywall put us in the "polite pool".
_OPENALEX_MAILTO = "papertrail@example.org"


# ---------------------------------------------------------------------------
# neighbors() — THE shared traversal primitive (snowball + origin-trace)
# ---------------------------------------------------------------------------

def neighbors(paper_id: str, direction: str = "both",
              cache_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """Citation-graph neighbors of a paper.

    direction: 'references' (papers this one cites), 'citations' (papers that
    cite this one), or 'both'. Semantic Scholar graph API first, OpenAlex as the
    free fallback when S2 errors out. Disk-cached by (paper_id, direction) so
    re-runs cost zero calls (model-agnostic, like source decomposition).

    Returns a list of normalized neighbor dicts:
      { "paper_id", "title", "year", "abstract", "authors" [names], "doi",
        "arxiv_id", "url", "citation_count",
        "relation": "references"|"citations",   # from the input paper's view
        "influential": bool, "intents": [str], "source": "s2"|"openalex" }

    Never guesses: an unresolvable/unfetchable paper yields [] (the caller
    records 'unfetchable' and stops — it does not fabricate an origin)."""
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS}, got {direction!r}")
    if not paper_id:
        return []

    if direction == "both":
        refs = neighbors(paper_id, "references", cache_dir)
        cites = neighbors(paper_id, "citations", cache_dir)
        return _dedupe(refs + cites)

    cached = _load_cache(cache_dir, paper_id, direction)
    if cached is not None:
        return cached

    result = _fetch_s2_edges(paper_id, direction)
    if result is None:
        # S2 hard-errored (rate-limited to exhaustion / network) → try OpenAlex.
        logger.info("S2 neighbors failed for %s (%s); trying OpenAlex fallback",
                    paper_id, direction)
        result = _fetch_openalex_edges(paper_id, direction)
    elif not result:
        # S2 knows the paper but has ZERO parsed edges for it (common for e.g.
        # AEA journals) — OpenAlex often has them. An empty answer is only kept
        # when both backends agree it's empty. (Found live 2026-07-07: S2 had 0
        # references for 10.1257/jep.29.3.3, OpenAlex had 39.)
        alt = _fetch_openalex_edges(paper_id, direction)
        if alt:
            logger.info("S2 had no %s for %s; using %d from OpenAlex",
                        direction, paper_id, len(alt))
            result = alt
    if result is None:
        # Both backends failed. Do NOT cache a failure (a later re-run should
        # retry); return [] so the caller stops honestly.
        logger.warning("neighbors() could not resolve %s (%s) from S2 or OpenAlex",
                       paper_id, direction)
        return []

    _save_cache(cache_dir, paper_id, direction, result)
    return result


# ---------------------------------------------------------------------------
# Semantic Scholar backend (primary)
# ---------------------------------------------------------------------------

def _fetch_s2_edges(paper_id: str, direction: str) -> Optional[List[Dict[str, Any]]]:
    """Page through /paper/{id}/references|citations. Returns normalized
    neighbors, or None on a hard error (so neighbors() can fall back)."""
    inner = _INNER_KEY[direction]
    url = f"{_S2_GRAPH}/paper/{paper_id}/{direction}"
    out: List[Dict[str, Any]] = []
    offset = 0
    while len(out) < MAX_NEIGHBORS:
        limit = min(_S2_PAGE, MAX_NEIGHBORS - offset)
        page = _s2_get(url, {"fields": _NEIGHBOR_FIELDS, "limit": limit,
                             "offset": offset})
        if page is None:
            # Nothing pulled yet → hard failure (fall back). Partial pull →
            # keep what we have rather than throwing it away.
            return None if not out else out
        data = page.get("data") or []
        for item in data:
            paper = item.get(inner) if isinstance(item, dict) else None
            if not paper:
                continue  # S2 returns null for refs it couldn't resolve — skip
            norm = _normalize_s2(paper, direction, item)
            if norm:
                out.append(norm)
        nxt = page.get("next")
        if not data or nxt is None:
            break
        offset = nxt
    return out


def _s2_get(url: str, params: dict, retry_attempt: int = 0,
            max_retries: int = 3, use_api_key: bool = True) -> Optional[dict]:
    """GET a single S2 graph page with 429 backoff + 403 key fallback, mirroring
    semantic_scholar_api.search_papers. Returns parsed JSON or None."""
    headers = {}
    api_key = (s2.load_semantic_scholar_api_key()
               if (use_api_key and not s2._key_rejected) else None)
    if api_key:
        headers["x-api-key"] = api_key
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as http_err:
        status = http_err.response.status_code if http_err.response is not None else None
        if status == 403 and api_key:
            s2._key_rejected = True
            logger.warning("S2 API key rejected (403); falling back to public API")
            return _s2_get(url, params, retry_attempt, max_retries, use_api_key=False)
        if status == 429 and retry_attempt < max_retries:
            wait = (2 ** retry_attempt) * 5 + random.uniform(0, 1)
            logger.warning("S2 rate limit (429); waiting %.1fs (retry %d/%d)",
                           wait, retry_attempt + 1, max_retries)
            time.sleep(wait)
            return _s2_get(url, params, retry_attempt + 1, max_retries, use_api_key)
        if status == 404:
            logger.info("S2 has no record for %s", url)
            return None
        logger.error("S2 HTTP error for %s: %s", url, http_err)
        return None
    except Exception as e:
        logger.error("S2 request failed for %s: %s", url, e)
        return None


def _normalize_s2(paper: dict, direction: str,
                  ref_item: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    ext = paper.get("externalIds") or {}
    doi = ext.get("DOI")
    arxiv = ext.get("ArXiv")
    authors = [a.get("name") for a in (paper.get("authors") or []) if a.get("name")]
    ref_item = ref_item or {}
    return {
        "paper_id": paper.get("paperId"),
        "title": paper.get("title"),
        "year": paper.get("year"),
        "abstract": paper.get("abstract"),
        "authors": authors,
        "doi": doi,
        "arxiv_id": arxiv,
        "url": _best_url(doi, arxiv, paper.get("openAccessPdf"),
                         paper.get("paperId")),
        "citation_count": paper.get("citationCount"),
        "relation": direction,
        "influential": bool(ref_item.get("isInfluential")),
        "intents": ref_item.get("intents") or [],
        "source": "s2",
    }


def _best_url(doi, arxiv, oa_pdf, s2_id) -> Optional[str]:
    if doi:
        return f"https://doi.org/{doi}"
    if arxiv:
        return f"https://arxiv.org/abs/{arxiv}"
    if oa_pdf and oa_pdf.get("url"):
        return oa_pdf["url"]
    if s2_id:
        return f"https://www.semanticscholar.org/paper/{s2_id}"
    return None


# ---------------------------------------------------------------------------
# OpenAlex backend (free fallback) — best-effort, DOI-keyed
# ---------------------------------------------------------------------------

def _fetch_openalex_edges(paper_id: str, direction: str) -> Optional[List[Dict[str, Any]]]:
    """Best-effort citation edges from OpenAlex. Resolves the paper to an
    OpenAlex work (by DOI, arXiv id, or an OpenAlex Wxxxx id), then:
      references  -> the work's referenced_works (batch-fetched for metadata)
      citations   -> works with filter=cites:<id>
    Returns None if the paper can't be resolved (caller then returns [])."""
    work = _openalex_resolve(paper_id)
    if not work:
        return None
    oa_id = _openalex_short_id(work.get("id"))
    if direction == "references":
        ref_ids = [_openalex_short_id(w) for w in (work.get("referenced_works") or [])]
        ref_ids = [r for r in ref_ids if r][:MAX_NEIGHBORS]
        return _openalex_fetch_by_ids(ref_ids, "references")   # None when all batches fail
    # citations
    out: List[Dict[str, Any]] = []
    cursor = "*"
    while cursor and len(out) < MAX_NEIGHBORS:
        page = _openalex_get(f"{_OPENALEX}/works",
                             {"filter": f"cites:{oa_id}", "per-page": 200,
                              "cursor": cursor})
        if page is None:
            return out or None
        for w in page.get("results") or []:
            norm = _normalize_openalex(w, "citations")
            if norm:
                out.append(norm)
        cursor = (page.get("meta") or {}).get("next_cursor")
    return out


def _openalex_resolve(paper_id: str) -> Optional[dict]:
    pid = paper_id.strip()
    if pid.upper().startswith("DOI:"):
        key = "https://doi.org/" + pid[4:]
    elif pid.upper().startswith("ARXIV:"):
        return _openalex_get(f"{_OPENALEX}/works",
                             {"filter": f"ids.arxiv:{pid[6:]}", "per-page": 1},
                             first_result=True)
    elif re.match(r"^W\d+$", pid):
        key = pid
    elif re.match(r"^10\.\d{4,}/", pid):     # bare DOI
        key = "https://doi.org/" + pid
    else:
        # An S2 hex id or something OpenAlex can't key on directly → give up
        # (honest: no fabricated resolution).
        return None
    return _openalex_get(f"{_OPENALEX}/works/{key}", {})


def _openalex_fetch_by_ids(short_ids: List[str],
                           direction: str) -> Optional[List[Dict[str, Any]]]:
    """None when every batch call failed — a transient network failure must not
    surface as (and get disk-cached as) 'this paper has no references'. An
    empty short_ids list is a genuine empty result, not a failure."""
    out: List[Dict[str, Any]] = []
    any_ok = not short_ids
    for i in range(0, len(short_ids), 50):           # OR-filter allows ~50/call
        batch = short_ids[i:i + 50]
        page = _openalex_get(f"{_OPENALEX}/works",
                             {"filter": "openalex_id:" + "|".join(batch),
                              "per-page": 50})
        if page is None:
            continue
        any_ok = True
        for w in page.get("results") or []:
            norm = _normalize_openalex(w, direction)
            if norm:
                out.append(norm)
    return out if any_ok else None


def _openalex_get(url: str, params: dict, first_result: bool = False,
                  retry_attempt: int = 0, max_retries: int = 3):
    params = dict(params)
    params.setdefault("mailto", _OPENALEX_MAILTO)
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if first_result:
            results = data.get("results") or []
            return results[0] if results else None
        return data
    except requests.exceptions.HTTPError as http_err:
        status = http_err.response.status_code if http_err.response is not None else None
        if status == 429 and retry_attempt < max_retries:
            wait = (2 ** retry_attempt) * 2 + random.uniform(0, 1)
            time.sleep(wait)
            return _openalex_get(url, params, first_result, retry_attempt + 1, max_retries)
        if status == 404:
            return None
        logger.error("OpenAlex HTTP error for %s: %s", url, http_err)
        return None
    except Exception as e:
        logger.error("OpenAlex request failed for %s: %s", url, e)
        return None


def _openalex_short_id(oa_url: Optional[str]) -> Optional[str]:
    if not oa_url:
        return None
    return oa_url.rstrip("/").rsplit("/", 1)[-1]   # ".../W123" -> "W123"


def _reconstruct_abstract(inv_index: Optional[dict]) -> Optional[str]:
    """OpenAlex stores abstracts as an inverted index {word: [positions]}."""
    if not inv_index:
        return None
    positions: List = []
    for word, idxs in inv_index.items():
        for i in idxs:
            positions.append((i, word))
    if not positions:
        return None
    positions.sort()
    return " ".join(w for _, w in positions)


def _normalize_openalex(work: dict, direction: str) -> Optional[Dict[str, Any]]:
    if not work:
        return None
    ids = work.get("ids") or {}
    doi = work.get("doi") or ids.get("doi")
    if doi:
        doi = doi.replace("https://doi.org/", "")
    authors = [a.get("author", {}).get("display_name")
               for a in (work.get("authorships") or [])
               if a.get("author", {}).get("display_name")]
    return {
        "paper_id": _openalex_short_id(work.get("id")),
        "title": work.get("title") or work.get("display_name"),
        "year": work.get("publication_year"),
        "abstract": _reconstruct_abstract(work.get("abstract_inverted_index")),
        "authors": authors,
        "doi": doi,
        "arxiv_id": None,
        "url": (f"https://doi.org/{doi}" if doi else work.get("id")),
        "citation_count": work.get("cited_by_count"),
        "relation": direction,
        "influential": False,
        "intents": [],
        "source": "openalex",
    }


# ---------------------------------------------------------------------------
# Disk cache (by paper_id + direction; model-agnostic)
# ---------------------------------------------------------------------------

def _cache_path(cache_dir: str, paper_id: str, direction: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", paper_id)[:120]
    return os.path.join(cache_dir, f"neighbors__{safe}__{direction}.json")


def _load_cache(cache_dir: Optional[str], paper_id: str,
                direction: str) -> Optional[List[Dict[str, Any]]]:
    if not cache_dir:
        return None
    path = _cache_path(cache_dir, paper_id, direction)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("neighbors", [])
    except Exception as e:
        logger.debug("Could not read neighbors cache %s: %s", path, e)
        return None


def _save_cache(cache_dir: Optional[str], paper_id: str, direction: str,
                neighbors_list: List[Dict[str, Any]]) -> None:
    if not cache_dir:
        return
    os.makedirs(cache_dir, exist_ok=True)
    path = _cache_path(cache_dir, paper_id, direction)
    payload = {"paper_id": paper_id, "direction": direction,
               "count": len(neighbors_list), "neighbors": neighbors_list}
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
    except Exception as e:
        logger.debug("Could not write neighbors cache %s: %s", path, e)


def _dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge duplicate neighbors (same paper reached as both a reference and a
    citation, or repeated). Keyed by paper_id, else doi, else title. When a
    paper appears in both directions, keep 'both' as its relation."""
    seen: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for it in items:
        key = (it.get("paper_id") or it.get("doi")
               or (it.get("title") or "").lower().strip())
        if not key:
            continue
        if key not in seen:
            seen[key] = dict(it)
            order.append(key)
        else:
            prev = seen[key]
            if prev.get("relation") != it.get("relation"):
                prev["relation"] = "both"
            prev["influential"] = prev.get("influential") or it.get("influential")
    return [seen[k] for k in order]


# ---------------------------------------------------------------------------
# snowball() / pick_relevant() — the human-like search loop
# ---------------------------------------------------------------------------

# Carry only the best few leads forward per level (a researcher chases a handful
# of the most promising papers, not the whole frontier). All papers *discovered*
# are still recorded as candidates — only the *expansion* set is bounded, which
# is what keeps live S2/OpenAlex calls (and cost) in check.
SEED_LIMIT = 15           # seeds pulled from the keyword search

# Optional LLM relevance gate (default OFF — cosine-only is the $0 path). One
# batched JSON call per pick_relevant, not one per paper. On any failure or an
# omitted candidate we KEEP the paper (never silently prune on error/uncertainty
# — snowball is discovery; over-including beats missing).
SNOWBALL_GATE_PROMPT = (
    "You are helping a researcher snowball-search the literature.\n\n"
    "TARGET (what they are looking for):\n{TARGET}\n\n"
    "CANDIDATE PAPERS (numbered):\n{CANDIDATES}\n\n"
    "For each candidate decide whether it is genuinely on-target — worth reading "
    "to pursue the TARGET, not merely sharing a keyword. Return ONLY a JSON array, "
    "one entry per candidate:\n"
    '[{"index": <int>, "relevant": true|false, "reason": "<short why>"}, ...]'
)
_GATE_ABSTRACT_CHARS = 320


def _relevance_text(paper: Dict[str, Any]) -> Optional[str]:
    """The text we embed / show the LLM for a paper: abstract, else title."""
    return (paper.get("abstract") or paper.get("title") or "").strip() or None


class _VecCache:
    """Per-snowball SPECTER vector memo. Frontier papers' neighbor lists overlap
    heavily and already-seen papers are re-ranked every round, so without this
    each round re-encodes thousands of abstracts (CPU, minutes); with it every
    abstract — and the target — is encoded exactly once per snowball call."""

    def __init__(self):
        self._target_vec = None
        self._target_text = None
        self._by_key: Dict[str, Any] = {}

    def scores(self, target_text: str, papers: List[Dict[str, Any]],
               texts: List[str]) -> List[float]:
        import torch
        from sentence_transformers import util
        if self._target_vec is None or self._target_text != target_text:
            self._target_vec = embeddings.embed([target_text])
            self._target_text = target_text
        keys = [p.get("paper_id") or t for p, t in zip(papers, texts)]
        missing = [(k2, t) for k2, t in zip(keys, texts) if k2 not in self._by_key]
        if missing:
            vecs = embeddings.embed([t for _, t in missing])
            for (k2, _), v in zip(missing, vecs):
                self._by_key[k2] = v
        mat = torch.stack([self._by_key[k2] for k2 in keys])
        return util.cos_sim(self._target_vec, mat)[0].tolist()


def pick_relevant(papers: List[Dict[str, Any]], target_text: str, k: int,
                  llm=None, vec_cache: Optional[_VecCache] = None) -> List[Dict[str, Any]]:
    """Rank candidates by relevance to target_text (SPECTER cosine of abstract,
    title as fallback) and keep the top-k. Papers with neither abstract nor title
    can't be ranked and are dropped (never guessed onto the list). Each returned
    paper gains a float `relevance` and a `reason`. When `llm` is given, a single
    batched LLM gate re-checks the top-k and can drop clear off-target hits and
    supply a better `reason`; `llm=None` → cosine-only (zero API cost).
    vec_cache memoizes SPECTER vectors across calls (see _VecCache)."""
    if k <= 0 or not papers or not (target_text or "").strip():
        return []

    rankable = [p for p in papers if _relevance_text(p)]
    if not rankable:
        return []

    texts = [_relevance_text(p) for p in rankable]
    if vec_cache is not None:
        scores = vec_cache.scores(target_text, rankable, texts)
    else:
        scores = embeddings.cosine_matrix([target_text], texts)[0]

    scored = []
    for paper, score in zip(rankable, scores):
        cand = dict(paper)
        cand["relevance"] = round(float(score), 4)
        cand.setdefault("reason", f"cosine {cand['relevance']:.2f} to target")
        scored.append(cand)
    scored.sort(key=lambda c: c["relevance"], reverse=True)
    top = scored[:k]

    if llm is not None and top:
        top = _llm_gate(top, target_text, llm)
    return top


def _llm_gate(candidates: List[Dict[str, Any]], target_text: str,
              llm) -> List[Dict[str, Any]]:
    """Batched relevance gate over already-cosine-ranked candidates. Drops only
    the ones the LLM explicitly marks off-target; keeps everything else (missing
    verdict, parse failure, or LLM error) so we never prune on uncertainty."""
    lines = []
    for i, c in enumerate(candidates):
        blurb = (_relevance_text(c) or "")[:_GATE_ABSTRACT_CHARS]
        title = c.get("title") or "(untitled)"
        lines.append(f"[{i}] {title}\n    {blurb}")
    prompt = (SNOWBALL_GATE_PROMPT
              .replace("{TARGET}", target_text.strip())
              .replace("{CANDIDATES}", "\n".join(lines)))
    try:
        resp = llm.call_json(prompt)
    except Exception as e:
        logger.warning("snowball LLM gate failed (%s); keeping cosine ranking", e)
        return candidates
    if not isinstance(resp, list):
        return candidates

    verdicts = {}
    for entry in resp:
        if isinstance(entry, dict) and isinstance(entry.get("index"), int):
            verdicts[entry["index"]] = entry

    kept = []
    for i, c in enumerate(candidates):
        v = verdicts.get(i)
        if v is None:
            kept.append(c)                       # no verdict → keep (honest)
            continue
        if v.get("relevant") is False:
            continue                             # explicit off-target → drop
        if v.get("reason"):
            c = dict(c)
            c["reason"] = str(v["reason"])
        kept.append(c)
    return kept


def _edge_kind(relation: Optional[str]) -> str:
    """From the source paper's view: a 'references' neighbor is one it CITES; a
    'citations' neighbor is one that CITES it (cited_by)."""
    return {"references": "cites", "citations": "cited_by"}.get(relation, "cites")


def _as_candidate(paper: Dict[str, Any], path: List[str]) -> Dict[str, Any]:
    return {
        "paper_id": paper.get("paper_id"),
        "title": paper.get("title"),
        "year": paper.get("year"),
        "abstract": paper.get("abstract"),
        "doi": paper.get("doi"),
        "url": paper.get("url"),
        "relevance": paper.get("relevance", 0.0),
        "found_via": list(path),
        "reason": paper.get("reason", ""),
    }


def snowball(target_text: str, keywords, llm=None,
             max_depth: int = DEFAULT_MAX_DEPTH,
             branching: int = DEFAULT_BRANCHING,
             cache_dir: Optional[str] = None) -> Dict[str, Any]:
    """Human-like snowball search: keyword seed → rank by relevance to
    `target_text` → follow the best leads' citation graph outward → recurse.

    keywords: a query string or a list of keyword strings (joined into one
    search — seeds are meant to be a handful, not exhaustive). `llm=None` →
    cosine-only ranking (zero API cost). Neighbor lookups are disk-cached via
    `cache_dir` (model-agnostic), so re-runs are free.

    Returns:
      { "target": str,
        "candidates": [ {paper_id,title,year,abstract,doi,url,relevance,
                         found_via:[paper_id path], reason} , ... ] (relevance desc),
        "edges": [ {"from":pid,"to":pid,"kind":"cites"|"cited_by"} , ... ],
        "seeds": [ {paper_id,title,relevance} , ... ],
        "max_depth": int,
        "status": "ok"|"empty_query"|"search_failed"|"no_seeds" }

    Honest by construction: nothing is fabricated. Papers that can't be ranked
    or resolved simply don't appear. `status` distinguishes a rate-limited /
    failed seed search ("search_failed" — worth a retry) from a search that ran
    but matched nothing ("no_seeds"), so a caller/viewer never mistakes a
    transient failure for an empty field.

    Perf note: ranking embeds EVERY neighbor's abstract (SPECTER, local CPU), and
    a heavily-cited paper's `citations` can hit MAX_NEIGHBORS (1000). Neighbor
    *fetches* are disk-cached so re-runs are free, but the *encode* is not — for a
    live demo keep `branching`/`max_depth` modest (e.g. 3–5 / 1–2)."""
    query = keywords if isinstance(keywords, str) else " ".join(
        k for k in (keywords or []) if k)
    query = (query or "").strip()
    result = {"target": target_text, "candidates": [], "edges": [],
              "seeds": [], "max_depth": max_depth, "status": "ok"}
    if not query:
        logger.warning("snowball: empty keyword query; nothing to search")
        result["status"] = "empty_query"
        return result

    raw_seed = s2.search_papers(query, limit=SEED_LIMIT)
    if raw_seed is None:
        # Hard error (rate-limited to exhaustion / network) — NOT "no results".
        logger.warning("snowball: seed search failed for %r (retry later)", query)
        result["status"] = "search_failed"
        return result
    seeds = [_normalize_s2(p, "seed") for p in raw_seed]
    seeds = [s for s in seeds if s and s.get("paper_id")]
    vec_cache = _VecCache()          # encode each abstract once per snowball
    frontier = pick_relevant(seeds, target_text, branching, llm, vec_cache=vec_cache)
    if not frontier:
        logger.info("snowball: no relevant seeds for %r", query)
        result["status"] = "no_seeds"
        return result

    candidates: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []
    seen = set()
    found_via: Dict[str, List[str]] = {}

    for s in frontier:
        pid = s["paper_id"]
        seen.add(pid)
        found_via[pid] = [pid]
        candidates[pid] = _as_candidate(s, [pid])
    result["seeds"] = [{"paper_id": s["paper_id"], "title": s.get("title"),
                        "relevance": s.get("relevance")} for s in frontier]

    current = list(frontier)
    for _depth in range(max_depth):
        discovered: List[Dict[str, Any]] = []
        for paper in current:
            pid = paper["paper_id"]
            nbrs = neighbors(pid, "both", cache_dir)
            ranked = pick_relevant(nbrs, target_text, branching, llm,
                                   vec_cache=vec_cache)
            for nb in ranked:
                nbid = nb.get("paper_id")
                if not nbid:
                    continue
                edges.append({"from": pid, "to": nbid,
                              "kind": _edge_kind(nb.get("relation"))})
                if nbid in seen:
                    continue
                seen.add(nbid)
                path = found_via[pid] + [nbid]
                found_via[nbid] = path
                cand = _as_candidate(nb, path)
                candidates[nbid] = cand
                discovered.append(nb)
        if not discovered:
            break
        # Chase only the best few new leads onward (bounded expansion); all
        # discovered papers remain in the candidate list regardless.
        discovered = _dedupe(discovered)
        discovered.sort(key=lambda c: c.get("relevance", 0.0), reverse=True)
        current = discovered[:branching]

    result["candidates"] = sorted(candidates.values(),
                                  key=lambda c: c["relevance"], reverse=True)
    result["edges"] = edges
    return result
