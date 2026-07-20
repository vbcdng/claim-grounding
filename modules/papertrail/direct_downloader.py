#!/usr/bin/env python3
"""
Source downloader for a sources_manifest.json (ROADMAP item 2).

Fetches each cited source into a sources/ folder as <key>.pdf (or <key>.txt when
only page text is available), routing by what the manifest entry actually is:

- paper-shaped (arXiv / DOI / direct PDF / a landing page with a known id):
  PDF cascade — given open-access PDF url -> arXiv -> Unpaywall (by DOI) ->
  OpenAlex (by DOI) -> PMC -> doi.org resolution + common publisher PDF-URL
  patterns -> Semantic Scholar direct PDF -> page-text fallback of the landing page.
- plain web page (reports, news, blogs — half of a real Claude Science manifest):
  page-text extraction is the PRIMARY path (with a cheap PDF attempt first in case
  the url actually serves one).

Never bypasses a paywall: a miss is reported (see download_sources.py's report),
not worked around.

Download machinery (download_file / download_page_content / the cascade order) was
ported from the AI_framework_papers monorepo (2026-07-02) and then adapted to
manifest input. Deliberately dropped in the adaptation: Google Scholar scraping
(fragile, slow, ToS-gray — owner-approved removal 2026-07-02) and the Semantic
Scholar `openAccessPdf.disclaimer` Unpaywall trigger (only exists in S2-shaped
input; the query-by-DOI path below covers it).
"""

import os
import re
import sys
import time
import random
import logging
from urllib.parse import urlparse, urljoin, quote

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Unpaywall's API terms ask for a real contact email with every request, and
# OpenAlex uses one for its "polite pool". The email is the user's own: read it
# from config/unpaywall_email.txt (or UNPAYWALL_EMAIL), and when running on a
# terminal ask once and save the answer there. With no email available the
# Unpaywall lookup is skipped (never sent with a fake address) and OpenAlex is
# queried without the mailto param.
_EMAIL_CACHE: list = []  # [email_or_empty] once resolved — ask at most once


def get_unpaywall_email() -> str:
    if _EMAIL_CACHE:
        return _EMAIL_CACHE[0]
    email = ""
    path = os.path.join(PROJECT_ROOT, "config", "unpaywall_email.txt")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            email = f.read().strip()
    if not email:
        email = os.environ.get("UNPAYWALL_EMAIL", "").strip()
    if not email and sys.stdin.isatty() and sys.stdout.isatty():
        print("  Unpaywall/OpenAlex ask for a contact email with each request"
              " (their polite-use policy).")
        answer = input("  Email to send (saved to config/unpaywall_email.txt;"
                       " leave empty to skip Unpaywall): ").strip()
        if answer:
            email = answer
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(email + "\n")
            except OSError as e:
                logger.warning(f"Could not save the email to {path}: {e}")
    if not email:
        logger.info("No contact email configured (config/unpaywall_email.txt or"
                    " UNPAYWALL_EMAIL) — skipping Unpaywall lookups.")
    _EMAIL_CACHE.append(email)
    return email


# --------------------------------------------------------------------------
# HTTP plumbing (ported)
# --------------------------------------------------------------------------

def setup_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9', 'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1', 'DNT': '1',
    })
    return session


def is_valid_url(url) -> bool:
    if not isinstance(url, str):
        return False
    try:
        r = urlparse(url)
        return r.scheme in ("http", "https") and bool(r.netloc)
    except ValueError:
        return False


def download_file(url, output_path, session, max_retries=3) -> bool:
    """Download a PDF with retries; validates content-type / %PDF signature / size."""
    for attempt in range(max_retries):
        try:
            logger.info(f"Attempting PDF download from: {url}")
            response = session.get(url, stream=True, timeout=30, allow_redirects=True)

            if response.status_code == 200:
                content_type = response.headers.get('Content-Type', '').lower()
                is_pdf_content_type = 'application/pdf' in content_type

                first_bytes = b''
                try:
                    chunk_iterator = response.iter_content(chunk_size=1024, decode_unicode=False)
                    first_bytes = next(chunk_iterator, b'')
                except StopIteration:
                    logger.warning(f"Empty response from {url}"); continue
                except Exception as e:
                    logger.warning(f"Error reading initial bytes from {url}: {e}"); continue

                is_pdf_signature = first_bytes.startswith(b'%PDF')

                if not is_pdf_content_type and not is_pdf_signature:
                    logger.info(f"Not a PDF (Content-Type: {content_type or 'none'}) from {url}")
                    if 'html' in content_type and not is_pdf_signature:
                        return False
                    if attempt < max_retries - 1:
                        time.sleep(2); continue
                    return False

                with open(output_path, 'wb') as f:
                    if first_bytes:
                        f.write(first_bytes)
                    for chunk in chunk_iterator:
                        if chunk:
                            f.write(chunk)

                file_size = os.path.getsize(output_path)
                if file_size < 10000:
                    logger.warning(f"Downloaded file too small ({file_size} B) from {url}, removing.")
                    try:
                        os.remove(output_path)
                    except OSError as e:
                        logger.error(f"Error removing small file {output_path}: {e}")
                    if attempt < max_retries - 1:
                        time.sleep(2); continue
                    return False

                logger.info(f"Downloaded: {os.path.basename(output_path)} ({file_size/1024:.1f} KB) from {url}")
                return True

            else:
                logger.info(f"Failed download from {url}: status {response.status_code}")
                if response.status_code < 500 and response.status_code != 429:
                    return False

        except requests.exceptions.SSLError as e:
            logger.warning(f"SSL error {url}: {e}"); return False
        except requests.exceptions.RequestException as e:
            logger.warning(f"Request error {url}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error downloading {url}: {e}")

        if attempt < max_retries - 1:
            wait = (1.5 ** attempt) + random.uniform(0.5, 1.5)
            time.sleep(wait)

    return False


def fetch_html(url, session, max_retries=3):
    """GET a page; return (soup, final_url) for HTML, or None."""
    for attempt in range(max_retries):
        try:
            logger.info(f"Fetching page: {url}")
            response = session.get(url, timeout=30, allow_redirects=True)
            if response.status_code == 200:
                content_type = response.headers.get('Content-Type', '').lower()
                if 'html' not in content_type:
                    logger.info(f"Not HTML ({content_type}) at {url}")
                    return None
                try:
                    import lxml  # noqa: F401
                    parser = 'lxml'
                except ImportError:
                    parser = 'html.parser'
                return BeautifulSoup(response.content, parser), response.url
            logger.info(f"Failed page fetch from {url}: status {response.status_code}")
            if response.status_code < 500 and response.status_code != 429:
                return None
        except requests.exceptions.SSLError as e:
            logger.warning(f"SSL error {url}: {e}"); return None
        except requests.exceptions.RequestException as e:
            logger.warning(f"Request error {url}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error on page {url}: {e}")
        if attempt < max_retries - 1:
            time.sleep((1.5 ** attempt) + random.uniform(0.5, 1.5))
    return None


def extract_pdf_links(soup, base_url, limit=4) -> list:
    """
    PDF urls a page offers, best first: the citation_pdf_url meta tag (the
    Google Scholar convention that NBER/arXiv/most publishers emit on paper
    landing pages), then anchors that end in .pdf.
    """
    urls = []
    meta = soup.find("meta", attrs={"name": "citation_pdf_url"})
    if meta and meta.get("content"):
        u = urljoin(base_url, meta["content"])
        if is_valid_url(u):
            urls.append(u)
    for a in soup.find_all("a", href=True):
        u = urljoin(base_url, a["href"])
        bare = u.lower().split("?")[0].split("#")[0]
        if bare.endswith(".pdf") and is_valid_url(u) and u not in urls:
            urls.append(u)
        if len(urls) >= limit:
            break
    return urls[:limit]


# Class/id smells of page chrome that sites put in plain <div>s (so the tag
# strip above misses them). Deliberately conservative — no "author"/"meta"
# style tokens that real article containers use (webtext's line filters catch
# bylines instead).
_BOILER_ATTR_RE = re.compile(
    r"(?i)\b(related|share|social|newsletter|subscribe|cookie|breadcrumb|"
    r"sidebar|comment|promo|recommend|trending|popular|pagination|widget|"
    r"masthead|site-nav|menu)\b")
_LINK_DENSITY = 0.7          # element text that is >=70% link text = a nav block


def _strip_link_dense(area) -> None:
    """Remove related-articles / headline-list blocks: containers whose text is
    mostly anchor text (the agenceeurope2026 class — ~40 unrelated headlines
    saved as 'content'). Parents are seen before children (document order), so
    a low-density article container survives while its nav children go."""
    for el in area.find_all(["div", "ul", "ol", "section", "table"]):
        txt = el.get_text(" ", strip=True)
        if len(txt) < 120:
            continue
        linked = " ".join(a.get_text(" ", strip=True) for a in el.find_all("a"))
        if len(linked) / len(txt) >= _LINK_DENSITY:
            el.extract()


def extract_page_text(soup) -> str:
    from .webtext import drop_boilerplate_lines
    for element in soup(["script", "style", "nav", "footer", "header",
                         "aside", "form", "button", "input", "figure",
                         "figcaption", "noscript", "iframe"]):
        element.extract()
    for element in soup.find_all(class_=_BOILER_ATTR_RE):
        element.extract()
    for element in soup.find_all(id=_BOILER_ATTR_RE):
        element.extract()
    text = None
    for selector in ('main', 'article', 'div[role="main"]', '#main',
                     '.main', '#content', '.content', 'body'):
        area = soup.select_one(selector)
        if area:
            _strip_link_dense(area)
            text = area.get_text(separator='\n\n', strip=True)
            if text and len(text) > 200:
                break
    if not text:
        text = soup.get_text(separator='\n\n', strip=True)
    lines = (line.strip() for line in text.splitlines())
    text = '\n'.join(line for line in lines if len(line.split()) > 2)
    text = re.sub(r'\s{2,}', ' ', text).strip()
    return drop_boilerplate_lines(text)


# Below this, a saved page text is flagged as suspect in the report — landing
# pages, cookie walls, and abstract-only pages all land in this range.
THIN_TEXT_WORDS = 900


def try_page(url, key, sources_dir, session, title=None, author=None):
    """
    Fetch a landing/web page ONCE; prefer any PDF it links (citation_pdf_url
    meta or .pdf anchors), else save its extracted text. A linked PDF that
    never mentions the cited title is discarded — pages link many PDFs and the
    first one isn't always the cited work (paper1: a 1956 aviation yearbook was
    saved as an Epoch analysis this way).
    Returns (outcome, filename, detail): outcome 'pdf'|'text'|'text_thin'|None.
    """
    fetched = fetch_html(url, session)
    if fetched is None:
        return None, None, "page fetch failed"
    soup, final_url = fetched

    pdf_path = os.path.join(sources_dir, f"{key}.pdf")
    for pdf_url in extract_pdf_links(soup, final_url):
        if download_file(pdf_url, pdf_path, session):
            if title and content_check(pdf_path, title, author) == "mismatch":
                logger.warning(f"[{key}] linked PDF is not the cited work — discarding {pdf_url}")
                os.remove(pdf_path)
                continue
            return "pdf", f"{key}.pdf", f"PDF linked from {final_url}"

    text = extract_page_text(soup)
    words = len(text.split())
    if not text or len(text) < 500:
        logger.warning(f"Extracted only {len(text or '')} chars from {final_url} — not saving")
        return None, None, "no PDF linked and page text too short"

    txt_path = os.path.join(sources_dir, f"{key}.txt")
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write(f"Source URL: {final_url}\n\n---\n\n" + text)
    logger.info(f"Saved page text: {key}.txt ({words} words)")
    if words < THIN_TEXT_WORDS:
        return "text_thin", f"{key}.txt", (f"only {words} words extracted — likely a "
                                           "landing page, cookie wall, or abstract; "
                                           "check it and replace manually if incomplete")
    return "text", f"{key}.txt", f"page text, {words} words"


# --------------------------------------------------------------------------
# Manifest-entry normalization and URL routing
# --------------------------------------------------------------------------

_ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5}(?:v\d+)?|[a-z-]+/[0-9]{7})",
                           re.IGNORECASE)
_DOI_URL_RE = re.compile(r"doi\.org/(10\.[^\s?#]+)", re.IGNORECASE)


def extract_arxiv_id(url) -> str:
    m = _ARXIV_URL_RE.search(url or "")
    return m.group(1) if m else None


def extract_doi_from_url(url) -> str:
    m = _DOI_URL_RE.search(url or "")
    return m.group(1) if m else None


def normalize_entry(manifest_entry: dict) -> dict:
    """Manifest entry -> normalized record with ids derived from the url."""
    url = manifest_entry.get("url")
    return {
        "key": manifest_entry["key"],
        "title": manifest_entry.get("title"),
        "author": manifest_entry.get("author"),
        "year": manifest_entry.get("year"),
        "url": url,
        "doi": manifest_entry.get("doi") or extract_doi_from_url(url),
        # Rich ids: a database-backed importer (paper_importer) resolves these at
        # import time — honor them so the downloader skips redundant S2 lookups.
        "arxiv_id": manifest_entry.get("arxiv_id") or extract_arxiv_id(url),
        "pmc_id": manifest_entry.get("pmc_id"),
        "s2_paper_id": manifest_entry.get("s2_paper_id"),
        "oa_pdf_url": manifest_entry.get("oa_pdf_url"),
        "status": manifest_entry.get("status"),
    }


_PAPER_HOSTS = ("nber.org", "ssrn.com", "openreview.net", "biorxiv.org", "osf.io",
                "semanticscholar.org", "ncbi.nlm.nih.gov", "pubmed.")


def classify(entry: dict) -> str:
    """'paper' -> run the PDF cascade first; 'web' -> page text is the primary path."""
    if entry.get("doi") or entry.get("arxiv_id") or entry.get("pmc_id") or entry.get("oa_pdf_url"):
        return "paper"
    url = (entry.get("url") or "").lower()
    if url.endswith(".pdf"):
        return "paper"
    if any(h in url for h in _PAPER_HOSTS):
        return "paper"
    return "web"


# --------------------------------------------------------------------------
# PDF-source cascade pieces
# --------------------------------------------------------------------------

def _unpaywall_pdf_urls(doi, session) -> list:
    """Query Unpaywall for a DOI; return open-access PDF urls, best first."""
    urls = []
    email = get_unpaywall_email()
    if not email:
        return urls  # Unpaywall requires a real contact email — skip, never fake one
    try:
        endpoint = f"https://api.unpaywall.org/v2/{doi}?email={email}"
        response = session.get(endpoint, timeout=15)
        if response.status_code == 200:
            data = response.json()
            if data.get("is_oa"):
                best = (data.get("best_oa_location") or {}).get("url_for_pdf")
                if best:
                    urls.append(best)
                for loc in data.get("oa_locations", []):
                    u = loc.get("url_for_pdf")
                    if u and u not in urls:
                        urls.append(u)
        elif response.status_code == 404:
            logger.info(f"DOI {doi} not found in Unpaywall")
        else:
            logger.info(f"Unpaywall query for {doi} returned status {response.status_code}")
    except Exception as e:
        logger.warning(f"Unpaywall query failed for {doi}: {e}")
    return [u for u in urls if is_valid_url(u)]


def _openalex_pdf_urls(doi, session) -> list:
    """Query OpenAlex for a DOI; return open-access PDF urls, best first.

    A second OA aggregator alongside Unpaywall — its index sometimes carries a
    PDF location Unpaywall misses (different crawl), so it's a cheap, keyless
    fallback. Source surfaced by the prior-art audit of sciwrite-lint's MIT
    `fulltext/` cascade (arXiv/S2/OpenAlex/PMC/EuropePMC/Unpaywall/bioRxiv/CORE);
    reimplemented here in our own style — see docs/PRIOR_ART_REUSE.md #1.
    """
    urls = []
    try:
        # mailto puts us in OpenAlex's "polite pool" (faster, unthrottled);
        # without an email the query still works, just default-pool throttled.
        email = get_unpaywall_email()
        endpoint = (f"https://api.openalex.org/works/doi:{quote(doi, safe='')}"
                    + (f"?mailto={email}" if email else ""))
        response = session.get(endpoint, timeout=15)
        if response.status_code == 200:
            data = response.json()
            best = (data.get("best_oa_location") or {}).get("pdf_url")
            if best:
                urls.append(best)
            for loc in data.get("locations", []) or []:
                u = loc.get("pdf_url")
                if u and u not in urls:
                    urls.append(u)
            oa_url = (data.get("open_access") or {}).get("oa_url")
            if oa_url and oa_url.lower().endswith(".pdf") and oa_url not in urls:
                urls.append(oa_url)
        elif response.status_code == 404:
            logger.info(f"DOI {doi} not found in OpenAlex")
        else:
            logger.info(f"OpenAlex query for {doi} returned status {response.status_code}")
    except Exception as e:
        logger.warning(f"OpenAlex query failed for {doi}: {e}")
    return [u for u in urls if is_valid_url(u)]


def _publisher_pdf_patterns(final_url) -> list:
    """Common publisher PDF-URL guesses derived from a landing-page url (ported)."""
    parts = urlparse(final_url)
    patterns = [
        f"{final_url.rstrip('/')}.pdf", f"{final_url.rstrip('/')}/pdf",
        final_url.replace('/abs/', '/pdf/').replace('/abstract/', '/pdf/').replace('/full/', '/pdf/'),
        final_url.replace('/article/', '/content/pdf/'), final_url.replace('/doi/abs/', '/doi/pdf/'),
        final_url.replace('/doi/', '/doi/pdf/'), f"{final_url}/download/pdf",
        final_url.replace('.html', '.pdf'), final_url.replace('.htm', '/pdf'),
        parts._replace(path=parts.path + ".pdf").geturl(),
        re.sub(r'/article/pii/(S?\d+)', r'/pdfft/\1', final_url) if '/article/pii/' in final_url else None,
    ]
    return sorted({p for p in patterns if p and p != final_url and is_valid_url(p)},
                  key=len, reverse=True)


def _try_pmc(pmc_id, pdf_path, session) -> bool:
    base = f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmc_id}"
    for u in (f"{base}/pdf/", f"{base}/pdf/{pmc_id}.pdf"):
        if download_file(u, pdf_path, session):
            return True
    try:
        page = session.get(base, timeout=20)
        if page.status_code == 200:
            try:
                import lxml  # noqa: F401
                parser = 'lxml'
            except ImportError:
                parser = 'html.parser'
            soup = BeautifulSoup(page.text, parser)
            tag = soup.select_one('ul.format-menu a[href*="pdf"], ul.format-menu a[href$=".pdf"], '
                                  'a.int-view[href*="pdf"]')
            if tag and tag.get('href'):
                u = urlparse(urljoin(base, tag['href']))._replace(query='', fragment='').geturl()
                if is_valid_url(u) and download_file(u, pdf_path, session):
                    return True
    except Exception as e:
        logger.warning(f"Error scraping PMC page {base}: {e}")
    return False


def content_check(path, title, author=None) -> str:
    """
    Does the file's opening text look like the cited work? Returns 'ok',
    'mismatch', or 'unknown' (title not distinctive enough / file unreadable).
    Guards against fetching the WRONG document under the right name — the paper1
    audit found a Chinese fisheries plan saved as a CSIS report and a 1956
    aviation yearbook saved as an Epoch analysis, undetected.

    When the title fails, author surnames rescue the verdict: bibliographies
    sometimes carry the VENUE as the title ("Proceedings of the National Academy
    of Sciences"), which the correct paper never headlines — but its authors
    appear on page one.
    """
    sig = [w for w in re.sub(r"[^a-z0-9 ]", " ", (title or "").lower()).split()
           if len(w) > 3]
    if len(sig) < 3:
        return "unknown"   # generic/journal-name titles can't identify a document
    text = ""
    try:
        if path.lower().endswith(".pdf"):
            import PyPDF2
            with open(path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                text = "\n".join((p.extract_text() or "") for p in reader.pages[:5])
        else:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read(12000)
    except Exception as e:
        logger.warning(f"Could not read {os.path.basename(path)} for content check: {e}")
        return "unknown"
    if not text.strip():
        return "unknown"   # no extractable text (scan) — a different warning covers that
    norm = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    tokens = set(norm.split())
    compact = re.sub(r"\s+", "", norm)   # survives spaced-letter PDF artifacts ("P r epar e")
    covered = sum(1 for w in sig if w in tokens or w in compact)
    if covered / len(sig) >= 0.6:
        return "ok"
    names = [w for w in re.sub(r"[^a-z0-9 ]", " ", (author or "").lower()).split()
             if len(w) > 3 and w not in ("others",)]
    if names and any(w in tokens or w in compact for w in names):
        return "ok"
    return "mismatch"


def pdf_has_text(path, min_chars=200) -> bool:
    """Cheap sanity check that a downloaded PDF yields extractable text (else it's
    a scan/broken file that would silently produce an empty decomposition later)."""
    try:
        import PyPDF2
        with open(path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            chars = 0
            for page in reader.pages[:5]:
                chars += len(page.extract_text() or "")
                if chars >= min_chars:
                    return True
        return False
    except Exception as e:
        logger.warning(f"Could not text-check PDF {path}: {e}")
        return False


# --------------------------------------------------------------------------
# Per-entry download
# --------------------------------------------------------------------------

def download_source(entry: dict, sources_dir: str, session, force=False) -> dict:
    """
    Fetch one normalized entry into sources_dir as <key>.pdf or <key>.txt.
    Returns {key, outcome, filename, landing, detail}; outcome is one of
    'already_present' | 'pdf' | 'pdf_no_text' | 'text' | 'text_thin' |
    'not_fetchable'. 'landing' is the best human-visitable page for a manual
    download. force=True re-fetches even when a file already exists.
    """
    key = entry["key"]
    pdf_path = os.path.join(sources_dir, f"{key}.pdf")
    txt_path = os.path.join(sources_dir, f"{key}.txt")
    landing = _landing_url(entry)

    if not force:
        for path in (pdf_path, txt_path):
            if os.path.exists(path) and os.path.getsize(path) > 1000:
                logger.info(f"[{key}] already present: {os.path.basename(path)}")
                return {"key": key, "outcome": "already_present",
                        "filename": os.path.basename(path), "landing": landing, "detail": None}

    logger.info(f"[{key}] {entry.get('title') or 'Untitled'}")
    kind = classify(entry)
    fallback_page_urls = []

    title, author = entry.get("title"), entry.get("author")
    if kind == "web":
        url = entry["url"]
        # cheap PDF attempt first in case the url actually serves one
        if download_file(url, pdf_path, session):
            if title and content_check(pdf_path, title, author) == "mismatch":
                logger.warning(f"[{key}] fetched PDF is not the cited work — discarding")
                os.remove(pdf_path)
            else:
                return _pdf_result(key, pdf_path, landing)
        outcome, filename, detail = try_page(url, key, sources_dir, session,
                                             title=title, author=author)
        if outcome == "pdf":
            return _pdf_result(key, pdf_path, landing)
        if outcome:
            return {"key": key, "outcome": outcome, "filename": filename,
                    "landing": landing, "detail": detail}
        return {"key": key, "outcome": "not_fetchable", "filename": None,
                "landing": landing, "detail": detail}

    # ---- paper-shaped: PDF cascade ----
    if entry.get("oa_pdf_url") and download_file(entry["oa_pdf_url"], pdf_path, session):
        return _pdf_result(key, pdf_path, landing)

    url = entry.get("url")
    if url and url.lower().endswith(".pdf"):
        if download_file(url, pdf_path, session):
            return _pdf_result(key, pdf_path, landing)
    elif url and "doi.org" not in url:
        fallback_page_urls.append(url)

    if entry.get("arxiv_id"):
        fallback_page_urls.append(f"https://arxiv.org/abs/{entry['arxiv_id']}")
        if download_file(f"https://arxiv.org/pdf/{entry['arxiv_id']}.pdf", pdf_path, session):
            return _pdf_result(key, pdf_path, landing)

    if entry.get("doi"):
        for u in _unpaywall_pdf_urls(entry["doi"], session):
            if download_file(u, pdf_path, session):
                return _pdf_result(key, pdf_path, landing)
        # OpenAlex as a second OA index when Unpaywall has no PDF (queue #7).
        for u in _openalex_pdf_urls(entry["doi"], session):
            if download_file(u, pdf_path, session):
                return _pdf_result(key, pdf_path, landing)

    if entry.get("pmc_id") and _try_pmc(entry["pmc_id"], pdf_path, session):
        return _pdf_result(key, pdf_path, landing)

    if entry.get("doi"):
        try:
            doi_url = f"https://doi.org/{entry['doi']}"
            headers = session.headers.copy()
            headers['Accept'] = 'application/pdf, application/x-pdf, text/html;q=0.9'
            response = session.get(doi_url, headers=headers, allow_redirects=True, timeout=30)
            final_url = response.url
            logger.info(f"[{key}] DOI resolved to: {final_url}")
            fallback_page_urls.insert(0, final_url)
            if final_url.lower().endswith('.pdf') or '/pdf' in final_url.lower():
                if download_file(final_url, pdf_path, session):
                    return _pdf_result(key, pdf_path, landing)
            for pattern in _publisher_pdf_patterns(final_url):
                if download_file(pattern, pdf_path, session):
                    return _pdf_result(key, pdf_path, landing)
        except Exception as e:
            logger.warning(f"[{key}] Error following DOI {entry['doi']}: {e}")

    s2_id = entry.get("s2_paper_id")
    if s2_id:
        fallback_page_urls.append(f"https://www.semanticscholar.org/paper/{s2_id}")
        patterns = []
        if len(s2_id) >= 4:
            patterns.append(f"https://pdfs.semanticscholar.org/{s2_id[:2]}/{s2_id[2:4]}/{s2_id[4:]}.pdf")
        patterns.append(f"https://pdfs.semanticscholar.org/{s2_id[0]}/{s2_id}.pdf")
        for u in patterns:
            if is_valid_url(u) and download_file(u, pdf_path, session):
                return _pdf_result(key, pdf_path, landing)

    # ---- no PDF from the id-based cascade: try the landing page(s) — their
    # linked PDFs (e.g. NBER's citation_pdf_url) first, else their text ----
    for u in dict.fromkeys(fallback_page_urls):
        if not is_valid_url(u):
            continue
        outcome, filename, detail = try_page(u, key, sources_dir, session,
                                             title=title, author=author)
        if outcome == "pdf":
            return _pdf_result(key, pdf_path, landing)
        if outcome:
            return {"key": key, "outcome": outcome, "filename": filename,
                    "landing": landing, "detail": detail}

    return {"key": key, "outcome": "not_fetchable", "filename": None,
            "landing": landing, "detail": "no open PDF found and page text failed"}


def _pdf_result(key, pdf_path, landing) -> dict:
    if pdf_has_text(pdf_path):
        return {"key": key, "outcome": "pdf", "filename": os.path.basename(pdf_path),
                "landing": landing, "detail": None}
    return {"key": key, "outcome": "pdf_no_text", "filename": os.path.basename(pdf_path),
            "landing": landing,
            "detail": "PDF downloaded but yields no extractable text (scan/broken?) — "
                      "replace it manually or it will decompose to nothing"}


def _landing_url(entry) -> str:
    if entry.get("doi"):
        return f"https://doi.org/{entry['doi']}"
    if entry.get("url"):
        return entry["url"]
    if entry.get("arxiv_id"):
        return f"https://arxiv.org/abs/{entry['arxiv_id']}"
    if entry.get("s2_paper_id"):
        return f"https://www.semanticscholar.org/paper/{entry['s2_paper_id']}"
    return None
