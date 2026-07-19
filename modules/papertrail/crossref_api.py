"""
Crossref REST client — the paper importer's ORDERING witness (ROADMAP item 1,
generic-paper importer).

Semantic Scholar / OpenAlex give the reference list as an unordered SET; for a
numeric-citation paper ([12]-style) we also need the paper's own numbering.
Crossref's /works/{doi} record carries the publisher-deposited `reference` array,
which for numeric journals IS the paper's ordering, and each item's publisher key
("bib12" / "CR12" / "ref12") often ends in the number — a second signal.

Deliberately hand-rolled (~like semantic_scholar_api.py) instead of habanero:
house style is dependency-light, and we use exactly one endpoint. A `mailto`
query param puts us in Crossref's polite pool. No API key needed.
"""

import logging
import random
import time
import re

import requests

logger = logging.getLogger(__name__)

_CROSSREF_WORKS = "https://api.crossref.org/works"
_MAILTO = "papertrail@example.org"     # polite-pool contact, same as paper_search
_TRAILING_NUM_RE = re.compile(r"(\d+)\s*$")


def get_work(doi: str, retry_attempt: int = 0, max_retries: int = 3):
    """GET /works/{doi} → the `message` dict, or None (missing DOI / hard error).
    429s back off like the S2 client; 404 = not deposited, returns None quietly."""
    if not doi:
        return None
    url = f"{_CROSSREF_WORKS}/{doi}"
    try:
        resp = requests.get(url, params={"mailto": _MAILTO}, timeout=30)
        if resp.status_code == 404:
            logger.info(f"Crossref: no record for DOI {doi}")
            return None
        resp.raise_for_status()
        return (resp.json() or {}).get("message")
    except requests.exceptions.HTTPError as http_err:
        status = http_err.response.status_code if http_err.response is not None else None
        if status == 429 and retry_attempt < max_retries:
            wait = (2 ** retry_attempt) * 5 + random.uniform(0, 1)
            logger.warning(f"Crossref rate limit (429); waiting {wait:.1f}s "
                           f"(retry {retry_attempt + 1}/{max_retries})")
            time.sleep(wait)
            return get_work(doi, retry_attempt + 1, max_retries)
        logger.error(f"Crossref HTTP error for {doi}: {http_err}")
        return None
    except Exception as e:
        logger.error(f"Crossref lookup failed for {doi}: {e}")
        return None


def get_references(doi: str):
    """The publisher-deposited reference list of a paper, IN DEPOSITED ORDER.

    Returns a list of normalized dicts (possibly empty — many deposits omit
    references), or None when the work itself couldn't be fetched:
      { "position": 1-based index in the deposit,
        "publisher_key": raw key ("bib12"), "key_number": trailing int or None,
        "doi", "year", "author": first-author surname or None,
        "title", "raw": unstructured citation string or None }
    """
    work = get_work(doi)
    if work is None:
        return None
    out = []
    for i, ref in enumerate(work.get("reference") or [], start=1):
        pub_key = ref.get("key") or ""
        m = _TRAILING_NUM_RE.search(pub_key)
        out.append({
            "position": i,
            "publisher_key": pub_key or None,
            "key_number": int(m.group(1)) if m else None,
            "doi": (ref.get("DOI") or None),
            "year": ref.get("year") or None,
            "author": ref.get("author") or None,   # Crossref refs carry a surname string
            "title": ref.get("article-title") or ref.get("volume-title") or None,
            "raw": ref.get("unstructured") or None,
        })
    logger.info(f"Crossref: {len(out)} deposited reference(s) for {doi}")
    return out
