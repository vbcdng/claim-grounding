"""
Ingest manually-downloaded sources from an inbox folder (no LLM, no network).

The owner drops files (any names — publisher junk like `pnas.2413443122.pdf`
or `Bostrom - Superintelligence.pdf`) into an inbox; this module matches each
file to a manifest entry using deterministic signals only, strongest first:

  1. filename stem IS the key            (bostrom2014.pdf)
  2. key appears in the filename         (bostrom2014 (1).pdf)
  3. a DOI inside the file's text equals the entry's DOI
  4. entry title ≈ filename              (difflib on normalized strings)
  5. entry title appears in the file's first pages

A file is ingested only when exactly ONE entry matches at the strongest
matching level — ambiguity is reported, never guessed. `.html` files are
converted to extracted text (same extractor as the downloader); PDFs are
text-checked so a scanned/broken file is flagged.
"""

import os
import re
import difflib
import logging

logger = logging.getLogger(__name__)

from .direct_downloader import extract_page_text, pdf_has_text, THIN_TEXT_WORDS  # noqa: E402

_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)")
INGESTIBLE_EXTS = (".pdf", ".txt", ".html", ".htm")


def _norm(s) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())).strip()


def read_file_text(path, max_pdf_pages=5) -> str:
    """Best-effort text of a dropped file, for DOI/title matching."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".pdf":
            import PyPDF2
            with open(path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                return "\n".join((page.extract_text() or "")
                                 for page in reader.pages[:max_pdf_pages])
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
        if ext in (".html", ".htm"):
            from bs4 import BeautifulSoup
            return BeautifulSoup(raw, "html.parser").get_text(separator="\n")
        return raw
    except Exception as e:
        logger.warning(f"Could not read {os.path.basename(path)}: {e}")
        return ""


def _dois_in(text) -> set:
    return {m.rstrip(".,;)") .lower() for m in _DOI_RE.findall(text or "")}


def match_file(path, entries):
    """
    Match one dropped file against manifest entries.
    Returns (entry, how) on a unique confident match, else (None, note).
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    stem_norm = _norm(stem)
    stem_compact = re.sub(r"[^a-z0-9]", "", stem.lower())

    # 1+2: key-based (strongest — user renamed the file deliberately)
    exact = [e for e in entries if e["key"].lower() == stem.lower()]
    if len(exact) == 1:
        return exact[0], "filename is the key"
    contains = [e for e in entries if e["key"].lower() in stem_compact]
    if len(contains) == 1:
        return contains[0], "key found in filename"
    if len(contains) > 1:
        return None, f"filename matches several keys: {[e['key'] for e in contains]}"

    content = read_file_text(path)
    content_norm = _norm(content)

    # 3: DOI inside the file
    file_dois = _dois_in(content)
    if file_dois:
        doi_hits = [e for e in entries
                    if e.get("doi") and e["doi"].lower().rstrip(".,;)") in file_dois]
        if len(doi_hits) == 1:
            return doi_hits[0], f"DOI {doi_hits[0]['doi']} found in file"
        if len(doi_hits) > 1:
            return None, f"file contains DOIs of several entries: {[e['key'] for e in doi_hits]}"

    # Title-based strategies need a distinctive title. Short/generic ones —
    # typically the journal-name-as-title bib bug ("Nature", "International
    # Security") — would match half the world's PDFs, so they are excluded;
    # those entries can still be ingested by key-named files.
    def words(e):
        return len(_norm(e.get("title") or "").split())

    # 4: title ≈ filename
    fuzzy = [e for e in entries if words(e) >= 3
             and difflib.SequenceMatcher(None, _norm(e["title"]), stem_norm).ratio() >= 0.8]
    if len(fuzzy) == 1:
        return fuzzy[0], "title matches filename"
    if len(fuzzy) > 1:
        return None, f"filename resembles several titles: {[e['key'] for e in fuzzy]}"

    # 5: title inside the file's first pages (riskiest -> highest bar)
    in_text = [e for e in entries if words(e) >= 4
               and _norm(e["title"]) in content_norm]
    if len(in_text) == 1:
        return in_text[0], "title found inside the file"
    if len(in_text) > 1:
        return None, f"file contains several entry titles: {[e['key'] for e in in_text]}"

    return None, "no confident match — rename it to <key>.pdf (see the report's missing lists)"


def ingest_file(path, key, sources_dir, dry_run=False, copy=False):
    """
    File a matched download into sources_dir as <key>.<ext>; .html becomes
    extracted .txt. copy=True leaves the original in place (for shared folders
    like ~/Downloads); default moves it. Returns (filename, warning_or_None).
    """
    ext = os.path.splitext(path)[1].lower()
    warning = None

    if ext in (".html", ".htm"):
        filename = f"{key}.txt"
        if not dry_run:
            from bs4 import BeautifulSoup
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                soup = BeautifulSoup(f.read(), "html.parser")
            text = extract_page_text(soup)
            with open(os.path.join(sources_dir, filename), "w", encoding="utf-8") as f:
                f.write(f"Source file: {os.path.basename(path)}\n\n---\n\n" + text)
            if not copy:
                os.remove(path)
            if len(text.split()) < THIN_TEXT_WORDS:
                warning = f"extracted only {len(text.split())} words from the HTML"
    else:
        filename = f"{key}{'.pdf' if ext == '.pdf' else '.txt'}"
        target = os.path.join(sources_dir, filename)
        if not dry_run:
            if copy:
                import shutil
                shutil.copy2(path, target)
            else:
                os.replace(path, target)
            if ext == ".pdf" and not pdf_has_text(target):
                warning = "PDF has no extractable text (scan/broken?)"

    if not dry_run:
        # remove a stale counterpart (e.g. old thin key.txt replaced by key.pdf)
        for other in (f"{key}.pdf", f"{key}.txt"):
            stale = os.path.join(sources_dir, other)
            if other != filename and os.path.exists(stale):
                os.remove(stale)
                logger.info(f"Removed stale {other} (replaced by {filename})")
    return filename, warning


def plan_ingest(files, entries, has_file):
    """
    Decide what to do with each inbox file BEFORE touching anything.
    has_file(key) -> bool says whether sources/ already has that key.
    Returns (to_ingest, blocked, unmatched):
      to_ingest: [(path, entry, how)]   blocked: [(path, key, why)]
      unmatched: [(path, note)]
    Two inbox files matching the same key block each other — never
    first-come-wins on an ambiguous pair.
    """
    matched, unmatched = [], []
    for path in files:
        entry, how = match_file(path, entries)
        if entry is None:
            unmatched.append((path, how))
        else:
            matched.append((path, entry, how))

    per_key = {}
    for m in matched:
        per_key.setdefault(m[1]["key"], []).append(m)

    to_ingest, blocked = [], []
    for key, ms in per_key.items():
        if len(ms) > 1:
            for path, entry, how in ms:
                blocked.append((path, key,
                                f"{len(ms)} inbox files match this key — keep the right "
                                f"one and rename it to {key}.pdf ({how})"))
            continue
        path, entry, how = ms[0]
        # replacing an existing source is deliberate-only: key-named or DOI match
        if has_file(key) and not ("key" in how or "DOI" in how):
            blocked.append((path, key,
                            f"already has a source file — rename to {key}.pdf to "
                            f"replace it deliberately ({how})"))
        else:
            to_ingest.append((path, entry, how))
    return to_ingest, blocked, unmatched


def scan_inbox(inbox_dir) -> list:
    files = []
    for name in sorted(os.listdir(inbox_dir)):
        path = os.path.join(inbox_dir, name)
        if os.path.isfile(path) and name.lower().endswith(INGESTIBLE_EXTS):
            files.append(path)
    return files
