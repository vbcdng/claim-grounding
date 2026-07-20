"""
Semantic Scholar lookups for the source downloader (ROADMAP item 2).

Two entry points:
- search_papers(query, ...)          — raw relevance search (ported from the
                                       AI_framework_papers monorepo, trimmed).
- find_paper_by_title(title, year)   — the needs_search path: look a reference up
                                       by title when the bibliography gave no
                                       url/DOI, and only accept a confident match
                                       (never silently download the wrong paper).

An API key is optional (public API has lower rate limits): set
SEMANTIC_SCHOLAR_API_KEY, or put the key in config/semantic_scholar_api_key.txt.
"""

import os
import re
import json
import time
import random
import difflib
import logging

import requests

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SEARCH_FIELDS = "paperId,externalIds,title,abstract,venue,year,authors,citationCount,openAccessPdf"


def load_semantic_scholar_api_key():
    """Env var SEMANTIC_SCHOLAR_API_KEY, else config file; None -> public API."""
    api_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    if api_key:
        return api_key.strip()
    for rel in ("config/semantic_scholar_api_key.txt",):
        path = os.path.join(PROJECT_ROOT, rel)
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    key = f.read().strip()
                    if key:
                        return key
        except Exception as e:
            logger.debug(f"Could not load API key from {path}: {e}")
    config_path = os.path.join(PROJECT_ROOT, "config", "semantic_scholar_config.json")
    try:
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                key = json.load(f).get("api_key")
                if key:
                    return key.strip()
    except Exception as e:
        logger.debug(f"Could not load API key from {config_path}: {e}")
    return None


_key_rejected = False  # set on a 403 so one bad key doesn't 403 every lookup


def search_papers(query, limit=10, offset=0, retry_attempt=0, max_retries=3,
                  use_api_key=True):
    """
    Relevance search against the S2 /paper/search endpoint, with 429 backoff.
    A 403 (invalid/inactive API key) falls back to the public API for the rest
    of the run. Returns a list of paper dicts (possibly empty), or None on
    hard error.
    """
    global _key_rejected
    base_url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {"query": query, "limit": limit, "offset": offset, "fields": _SEARCH_FIELDS}
    headers = {}
    api_key = load_semantic_scholar_api_key() if (use_api_key and not _key_rejected) else None
    if api_key:
        headers["x-api-key"] = api_key

    logger.info(f"Semantic Scholar search: '{query}' (limit={limit})")
    try:
        response = requests.get(base_url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get("data") or []   # S2 sends {"data": null} for no hits
    except requests.exceptions.HTTPError as http_err:
        status = http_err.response.status_code if http_err.response is not None else None
        if status == 403 and api_key:
            _key_rejected = True
            logger.warning("S2 API key rejected (403 Forbidden) — check the key in "
                           "config/semantic_scholar_api_key.txt. Falling back to the "
                           "public API for this run.")
            return search_papers(query, limit, offset, retry_attempt, max_retries,
                                 use_api_key=False)
        if status == 429 and retry_attempt < max_retries:
            wait = (2 ** retry_attempt) * 5 + random.uniform(0, 1)
            logger.warning(f"S2 rate limit (429); waiting {wait:.1f}s "
                           f"(retry {retry_attempt + 1}/{max_retries})")
            time.sleep(wait)
            return search_papers(query, limit, offset, retry_attempt + 1, max_retries,
                                 use_api_key=use_api_key)
        logger.error(f"S2 HTTP error for '{query}': {http_err}")
        return None
    except Exception as e:
        logger.error(f"S2 search failed for '{query}': {e}")
        return None


def get_paper(paper_id, retry_attempt=0, max_retries=3, use_api_key=True):
    """
    GET /paper/{id} — direct lookup by id (accepts 'DOI:10…', 'ARXIV:…', a raw
    S2 paperId; same grammar as paper_search.neighbors). Returns the paper dict
    with _SEARCH_FIELDS, or None (unknown id / hard error). Same 429/403
    handling as search_papers.
    """
    global _key_rejected
    if not paper_id:
        return None
    url = f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}"
    headers = {}
    api_key = load_semantic_scholar_api_key() if (use_api_key and not _key_rejected) else None
    if api_key:
        headers["x-api-key"] = api_key
    try:
        response = requests.get(url, params={"fields": _SEARCH_FIELDS},
                                headers=headers, timeout=30)
        if response.status_code == 404:
            logger.info(f"S2: no paper for id {paper_id}")
            return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as http_err:
        status = http_err.response.status_code if http_err.response is not None else None
        if status == 403 and api_key:
            _key_rejected = True
            logger.warning("S2 API key rejected (403) — falling back to the public API")
            return get_paper(paper_id, retry_attempt, max_retries, use_api_key=False)
        if status == 429 and retry_attempt < max_retries:
            wait = (2 ** retry_attempt) * 5 + random.uniform(0, 1)
            logger.warning(f"S2 rate limit (429); waiting {wait:.1f}s "
                           f"(retry {retry_attempt + 1}/{max_retries})")
            time.sleep(wait)
            return get_paper(paper_id, retry_attempt + 1, max_retries, use_api_key)
        logger.error(f"S2 HTTP error for {paper_id}: {http_err}")
        return None
    except Exception as e:
        logger.error(f"S2 paper lookup failed for {paper_id}: {e}")
        return None


# --------------------------------------------------------------------------
# Title lookup with confidence check (the needs_search path)
# --------------------------------------------------------------------------

def _norm_title(s) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def _titles_match(wanted, candidate, threshold=0.90) -> bool:
    a, b = _norm_title(wanted), _norm_title(candidate)
    if not a or not b:
        return False
    if a == b:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= threshold


def find_paper_by_title(title, year=None):
    """
    Look up one paper by title. Returns (paper, status): the S2 paper dict only
    for a confident match (title similarity >= 0.9, year within ±1 when both
    known), else None with status 'no_match' or 'search_failed' (rate limit /
    network — worth retrying later or with an API key, unlike 'no_match').
    The caller reports "needs a literature search" for None — that is the safe
    outcome, not a failure to paper over.
    """
    if not title:
        return None, "no_match"
    # The keyless public pool 429s in bursts but admits requests eventually —
    # the proven workflow is patience + re-running the command, so retry longer
    # when there is no API key (the circuit breaker in download_sources.py still
    # caps the total damage of a fully saturated pool).
    max_retries = 3 if load_semantic_scholar_api_key() else 5
    results = search_papers(title, limit=5, max_retries=max_retries)
    if results is None:
        return None, "search_failed"
    for paper in results:
        if not _titles_match(title, paper.get("title")):
            continue
        if year and paper.get("year"):
            try:
                if abs(int(year) - int(paper["year"])) > 1:
                    logger.info(f"Title matched but year mismatch for '{title}': "
                                f"{year} vs {paper['year']} — rejecting")
                    continue
            except (TypeError, ValueError):
                pass
        logger.info(f"Confident S2 match for '{title}': paperId={paper.get('paperId')}")
        return paper, "matched"
    if results:
        logger.info(f"No confident S2 match for '{title}' "
                    f"(top hit: '{results[0].get('title')}')")
    return None, "no_match"


def enrich_entry_from_s2(entry: dict, paper: dict) -> dict:
    """Fill a normalized downloader entry with ids from a matched S2 paper."""
    ext = paper.get("externalIds") or {}
    oa = paper.get("openAccessPdf") or {}
    entry = dict(entry)
    entry["doi"] = entry.get("doi") or ext.get("DOI")
    entry["arxiv_id"] = entry.get("arxiv_id") or ext.get("ArXiv")
    entry["pmc_id"] = entry.get("pmc_id") or ext.get("PubMedCentral")
    entry["s2_paper_id"] = paper.get("paperId")
    entry["oa_pdf_url"] = oa.get("url")
    entry["s2_matched_title"] = paper.get("title")
    return entry
