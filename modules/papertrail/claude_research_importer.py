"""
Importer: Claude Science (pandoc/citeproc markdown + BibTeX) -> this tool's input format.

Produces the three INPUT_FORMAT.md artifacts from a Claude Science research export:
  1. my_text.md          — the prose with citations converted to [[key]] markers,
                           relocated to the end of their sentence (the claim splitter
                           treats a marker as citing everything before it, so a
                           mid-sentence marker would truncate the claim mid-thought).
  2. my_text.md.refs.txt — key = filename map, pre-filled with suggested filenames
                           (<key>.pdf); adjust extensions after downloading sources.
  3. sources_manifest.json — one entry per cited key with title/author/year/url/doi
                           and a status field, the hand-off to the paper downloader
                           (ROADMAP item 2) or to manual downloading.

Citation-marker recognition and bibliography loading are isolated behind small
interfaces (CitationRecognizer, load_bibliography) so other export variants — a
different marker syntax, inline references instead of a .bib — can be added without
rewriting the importer. Do not over-fit new logic to one sample export.

No LLM/API calls anywhere in this module — it is pure parsing.
"""

import os
import re
import json
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Citation recognition (the extensible seam: one class per marker syntax)
# --------------------------------------------------------------------------

class Citation:
    """One citation occurrence in the text: its span and the keys it cites."""

    def __init__(self, start: int, end: int, keys: List[str]):
        self.start = start
        self.end = end
        self.keys = keys


class PandocCitationRecognizer:
    """
    Recognizes pandoc/citeproc bracketed citations as emitted by Claude Science:
      [@key]              single source
      [@key1; @key2]      multiple sources in one bracket
      [@key, p. 12]       locator text after the key (kept out of the key)
    Bare narrative citations (@key outside brackets) are deliberately not matched —
    too collision-prone with emails/handles; add a dedicated recognizer if a real
    export uses them.
    """

    name = "pandoc"
    _BRACKET_RE = re.compile(r"\[(@[^\[\]]+)\]")
    _KEY_RE = re.compile(r"@([A-Za-z0-9_][A-Za-z0-9_:.#$%&+?<>~/-]*)")

    def find_citations(self, text: str) -> List[Citation]:
        out = []
        for m in self._BRACKET_RE.finditer(text):
            keys = self._KEY_RE.findall(m.group(1))
            if keys:
                out.append(Citation(m.start(), m.end(), keys))
        return out


RECOGNIZERS = [PandocCitationRecognizer()]


def detect_recognizer(text: str):
    """Pick the first recognizer that finds any citations in the text."""
    for rec in RECOGNIZERS:
        if rec.find_citations(text):
            return rec
    return None


# --------------------------------------------------------------------------
# Bibliography loading (second seam: one loader per bibliography format)
# --------------------------------------------------------------------------

_BIB_ENTRY_START_RE = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,")
_DOI_URL_RE = re.compile(r"(?:doi\.org/|doi:\s*)(10\.\S+?)(?:[\s,}]|$)", re.IGNORECASE)


def load_bibliography(path: str) -> Dict[str, Dict]:
    """Dispatch on extension. Returns {key: {title, author, year, url, doi, entry_type}}."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".bib":
        return _parse_bibtex(path)
    raise ValueError(f"Unsupported bibliography format: {path} "
                     f"(only .bib supported so far — add a loader in claude_research_importer.py)")


def _parse_bibtex(path: str) -> Dict[str, Dict]:
    """
    Tolerant, dependency-free BibTeX parser: brace-counted field values, unescaped
    enough for our needs (title/author/year/url/doi). Not a full BibTeX grammar.
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    entries: Dict[str, Dict] = {}
    for m in _BIB_ENTRY_START_RE.finditer(raw):
        entry_type, key = m.group(1).lower(), m.group(2)
        if entry_type in ("comment", "preamble", "string"):
            continue
        body = _read_balanced(raw, raw.find("{", m.start()))
        fields = _parse_bib_fields(body)
        url = fields.get("url") or _first_url(fields.get("note", ""))
        doi = fields.get("doi") or _extract_doi(url or "") or _extract_doi(fields.get("note", ""))
        entries[key] = {
            "key": key,
            "entry_type": entry_type,
            "title": _strip_braces(fields.get("title", "")),
            "author": _strip_braces(fields.get("author", "")),
            "year": _strip_braces(fields.get("year", "")),
            "url": url,
            "doi": doi,
        }
    logger.info(f"Parsed {len(entries)} bibliography entr(ies) from {os.path.basename(path)}")
    return entries


def _read_balanced(text: str, open_pos: int) -> str:
    """Return the content between the brace at open_pos and its matching close."""
    depth = 0
    for i in range(open_pos, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_pos + 1:i]
    return text[open_pos + 1:]


_BIB_FIELD_RE = re.compile(r"(\w+)\s*=\s*")


def _parse_bib_fields(body: str) -> Dict[str, str]:
    fields = {}
    pos = 0
    while True:
        m = _BIB_FIELD_RE.search(body, pos)
        if not m:
            break
        name = m.group(1).lower()
        i = m.end()
        if i < len(body) and body[i] == "{":
            val = _read_balanced(body, i)
            pos = i + len(val) + 2
        elif i < len(body) and body[i] == '"':
            j = body.find('"', i + 1)
            val = body[i + 1:j] if j != -1 else body[i + 1:]
            pos = (j + 1) if j != -1 else len(body)
        else:  # bare value (e.g. year = 2024)
            j = body.find(",", i)
            val = body[i:j] if j != -1 else body[i:]
            pos = (j + 1) if j != -1 else len(body)
        fields[name] = val.strip()
    return fields


def _strip_braces(s: str) -> str:
    s = re.sub(r"\\([&%$#_])", r"\1", s)  # common LaTeX escapes: \& \% \$ \# \_
    return re.sub(r"[{}]", "", s).strip()


_URL_RE = re.compile(r"https?://\S+")


def _first_url(s: str) -> Optional[str]:
    m = _URL_RE.search(s or "")
    return m.group(0).rstrip("}.,") if m else None


def _extract_doi(s: str) -> Optional[str]:
    m = _DOI_URL_RE.search(s or "")
    return m.group(1).rstrip("}.,") if m else None


# --------------------------------------------------------------------------
# Markdown handling
# --------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n(?:---|\.\.\.)\s*\n", re.DOTALL)


def split_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    """Return (simple key: value frontmatter dict, body). Tolerates no frontmatter."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip("\"'")
    return meta, text[m.end():]


# Sentence-end detection for marker relocation. A candidate boundary is .!? followed
# by whitespace-then-capital/quote/digit or end of block; guarded against common
# abbreviations, initials, and decimals so "et al." or "3.5" don't end a sentence.
_ABBREVIATIONS = ("et al", "e.g", "i.e", "cf", "vs", "etc", "fig", "figs", "no",
                  "vol", "pp", "p", "ca", "approx", "dr", "prof", "mr", "ms", "st")
_BOUNDARY_RE = re.compile(r"[.!?](?=[\"')\]]*(?:\s|$))")


def _sentence_end(text: str, from_pos: int) -> int:
    """Position just after the sentence-ending punctuation at/after from_pos
    (falls back to end of text)."""
    for m in _BOUNDARY_RE.finditer(text, from_pos):
        i = m.start()
        before = text[:i]
        # decimal number: digit on both sides
        if text[i] == "." and i + 1 < len(text) and text[i + 1].isdigit():
            continue
        tail = before[-8:].lower()
        if text[i] == "." and any(tail.endswith(a) for a in _ABBREVIATIONS):
            continue
        # single-capital initial like "B. F. Jones"
        if text[i] == "." and re.search(r"(?:^|\s)[A-Z]$", before):
            continue
        # include trailing close-quotes/brackets after the punctuation
        j = i + 1
        while j < len(text) and text[j] in "\"')]":
            j += 1
        return j
    return len(text)


def convert_block(block: str, recognizer) -> Tuple[str, List[str]]:
    """
    Convert one paragraph/block: strip its citations, re-insert them as [[key]]
    markers at the end of the sentence each citation appeared in.
    Returns (converted_block, cited_keys_in_order).
    """
    citations = recognizer.find_citations(block)
    if not citations:
        return block, []

    # boundary position (in original coords) -> keys to insert there, in order
    insertions: Dict[int, List[str]] = {}
    all_keys: List[str] = []
    for cit in citations:
        # Boundary search must skip other citation spans (a bracket like
        # "[@k, p. 3.]" could contain punctuation) — search on a masked copy.
        boundary = _sentence_end(_mask_spans(block, citations), cit.end)
        insertions.setdefault(boundary, []).extend(cit.keys)
        all_keys.extend(cit.keys)

    # Build output left to right: drop citation spans, add markers at boundaries.
    events = sorted(
        [(c.start, c.end, None) for c in citations] +
        [(b, b, keys) for b, keys in insertions.items()]
    )
    out, pos = [], 0
    for start, end, keys in events:
        out.append(block[pos:start])
        if keys is not None:
            out.append(" " + " ".join(f"[[{k}]]" for k in dict.fromkeys(keys)))
        pos = end
    out.append(block[pos:])
    converted = "".join(out)
    # tidy whitespace left behind by removed citations
    converted = re.sub(r"[ \t]+([.,;:!?])", r"\1", converted)
    converted = re.sub(r"[ \t]{2,}", " ", converted)
    return converted, all_keys


def _mask_spans(text: str, citations: List[Citation]) -> str:
    chars = list(text)
    for c in citations:
        for i in range(c.start, c.end):
            chars[i] = "_"
    return "".join(chars)


# --------------------------------------------------------------------------
# Top-level import
# --------------------------------------------------------------------------

def run_import(input_md: str, output_dir: str, bib_path: Optional[str] = None) -> Dict:
    """
    Import one Claude Science markdown export. Writes my_text.md,
    my_text.md.refs.txt, sources_manifest.json, and an empty sources/ dir into
    output_dir. Returns a summary dict (also what the CLI prints).
    """
    with open(input_md, "r", encoding="utf-8") as f:
        raw = f.read()

    meta, body = split_frontmatter(raw)

    if not bib_path and meta.get("bibliography"):
        bib_path = os.path.join(os.path.dirname(os.path.abspath(input_md)),
                                meta["bibliography"])
    if not bib_path:
        sibling = os.path.splitext(os.path.abspath(input_md))[0] + ".bib"
        if os.path.exists(sibling):
            logger.info(f"Using the sibling bibliography: {sibling}")
            bib_path = sibling
    bib = {}
    if bib_path and os.path.exists(bib_path):
        bib = load_bibliography(bib_path)
    elif bib_path:
        logger.warning(f"Bibliography not found: {bib_path}")
    else:
        logger.warning("No bibliography given or referenced in frontmatter")

    recognizer = detect_recognizer(body)
    if recognizer is None:
        raise ValueError("No known citation syntax found in the text "
                         f"(recognizers tried: {[r.name for r in RECOGNIZERS]})")
    logger.info(f"Citation syntax detected: {recognizer.name}")

    blocks = re.split(r"(\n\s*\n)", body)  # keep separators to preserve layout
    converted_parts: List[str] = []
    cited_keys: List[str] = []
    n_citations = 0
    for part in blocks:
        if part.strip():
            conv, keys = convert_block(part, recognizer)
            converted_parts.append(conv)
            cited_keys.extend(keys)
            n_citations += len(recognizer.find_citations(part))
        else:
            converted_parts.append(part)
    converted = "".join(converted_parts)
    unique_keys = list(dict.fromkeys(cited_keys))

    summary = write_artifacts(output_dir, converted, unique_keys, bib,
                              manifest_extra={
                                  "input_markdown": os.path.abspath(input_md),
                                  "bibliography": os.path.abspath(bib_path) if bib_path else None,
                                  "citation_syntax": recognizer.name,
                              },
                              title=meta.get("title", "")
                              or os.path.splitext(os.path.basename(input_md))[0])
    summary["citations_converted"] = n_citations
    return summary


def write_artifacts(output_dir: str, converted_text: str, unique_keys: List[str],
                    bib: Dict[str, Dict], manifest_extra: Optional[Dict] = None,
                    title: str = "") -> Dict:
    """
    Write the four INPUT_FORMAT.md artifacts — shared by every importer so the
    refs-merge semantics and manifest schema live in exactly one place.
      my_text.md            — converted_text as-is (a `title:` frontmatter block
                              is prepended when the export names one — the
                              decomposer skips it; the viewer names the review
                              file after it)
      my_text.md.refs.txt   — key = filename; EXISTING mappings win (a re-import
                              must not clobber extensions the downloader fixed)
      sources_manifest.json — manifest_extra fields first, then sources /
                              unresolved_keys / uncited_bibliography_keys
      sources/              — created empty
    bib: {key: {title, author, year, url, doi, ...}}. Returns the summary dict.
    """
    unresolved = [k for k in unique_keys if k not in bib]
    uncited_bib = [k for k in bib if k not in unique_keys]

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(os.path.join(output_dir, "sources"), exist_ok=True)

    text_out = os.path.join(output_dir, "my_text.md")
    with open(text_out, "w", encoding="utf-8") as f:
        if title:
            f.write(f'---\ntitle: "{title}"\n---\n\n')
        f.write(converted_text.lstrip("\n"))

    # Re-imports must not clobber refs the downloader (or the owner) already
    # fixed up — existing mappings win; only new keys get a suggested name.
    refs_out = text_out + ".refs.txt"
    existing = {}
    if os.path.exists(refs_out):
        with open(refs_out, "r", encoding="utf-8") as f:
            for line in f.read().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() and v.strip():
                        existing[k.strip()] = v.strip()
    with open(refs_out, "w", encoding="utf-8") as f:
        f.write("# key = filename (in sources/). Suggested names below — fix the\n"
                "# extension if you saved a source as .txt/.html instead of .pdf.\n")
        for k in unique_keys:
            f.write(f"{k} = {existing.get(k, k + '.pdf')}\n")

    manifest = dict(manifest_extra or {})
    manifest["sources"] = [_manifest_entry(k, bib.get(k)) for k in unique_keys]
    manifest["unresolved_keys"] = unresolved
    manifest["uncited_bibliography_keys"] = uncited_bib
    manifest_out = os.path.join(output_dir, "sources_manifest.json")
    with open(manifest_out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    needs_search = [s for s in manifest["sources"] if s["status"] == "needs_search"]
    return {
        "text": text_out,
        "refs": refs_out,
        "manifest": manifest_out,
        "unique_keys": len(unique_keys),
        "needs_search": [s["key"] for s in needs_search],
        "unresolved_keys": unresolved,
        "uncited_bibliography_keys": uncited_bib,
    }


# --------------------------------------------------------------------------
# Merge mode (the review loop's return path): a follow-up Claude Science report
# was asked to find REPLACEMENT sources for claims the verifier rejected. Its
# text is throwaway; only its bibliography matters — merge those sources into
# an EXISTING project (manifest + refs) instead of creating a new one, so the
# downloader can fetch them and the article can re-cite them.
# --------------------------------------------------------------------------

def _norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _norm_url(u: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", (u or "").lower()).rstrip("/")


def _same_title(a: str, b: str) -> bool:
    """Normalized equality, or containment for subtitle variants ('Report 2025'
    vs 'Report 2025: A matter of choice') — guarded by a minimum length so a
    short generic title can't swallow everything."""
    if not a or not b:
        return False
    if a == b:
        return True
    return min(len(a), len(b)) >= 20 and (a in b or b in a)


def _find_refs_file(project_dir: str) -> Optional[str]:
    preferred = os.path.join(project_dir, "my_text.md.refs.txt")
    if os.path.exists(preferred):
        return preferred
    cands = sorted(f for f in os.listdir(project_dir) if f.endswith(".refs.txt"))
    return os.path.join(project_dir, cands[0]) if cands else None


def merge_sources(input_path: str, project_dir: str, bib_path: Optional[str] = None) -> Dict:
    """
    Merge the bibliography of a Claude Science export (a .md report or a .bib
    directly) into an existing project dir (must hold sources_manifest.json and
    a *.refs.txt). Duplicates are skipped — matched by DOI, then by normalized
    title, never guessed; a key collision with a DIFFERENT work gets a numeric
    suffix. New keys are APPENDED to the refs file (existing lines untouched).
    Returns a summary dict. No LLM/API calls.
    """
    manifest_path = os.path.join(project_dir, "sources_manifest.json")
    if not os.path.exists(manifest_path):
        raise ValueError(f"Not a project dir (no sources_manifest.json): {project_dir}")
    refs_path = _find_refs_file(project_dir)
    if refs_path is None:
        raise ValueError(f"No *.refs.txt found in {project_dir}")

    if input_path.lower().endswith(".bib"):
        bib = load_bibliography(input_path)
        bib_used = input_path
    else:
        with open(input_path, "r", encoding="utf-8") as f:
            meta, _ = split_frontmatter(f.read())
        if not bib_path and meta.get("bibliography"):
            bib_path = os.path.join(os.path.dirname(os.path.abspath(input_path)),
                                    meta["bibliography"])
        if not bib_path:
            sibling = os.path.splitext(os.path.abspath(input_path))[0] + ".bib"
            if os.path.exists(sibling):
                logger.info(f"Using the sibling bibliography: {sibling}")
                bib_path = sibling
        if not bib_path or not os.path.exists(bib_path):
            raise ValueError("No bibliography found: pass --bib, put a <input>.bib next "
                             "to the input, point --input at the .bib itself, or export "
                             "a report whose frontmatter names one")
        bib = load_bibliography(bib_path)
        bib_used = bib_path

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    existing = manifest.get("sources", [])
    by_key = {s["key"]: s for s in existing}
    by_doi = {s["doi"].lower(): s for s in existing if s.get("doi")}
    by_url = {_norm_url(s.get("url")): s for s in existing if _norm_url(s.get("url"))}

    added: List[Dict] = []
    skipped: List[Dict] = []
    renamed: List[Dict] = []
    for key, entry in bib.items():
        doi = (entry.get("doi") or "").lower()
        url = _norm_url(entry.get("url"))
        title = _norm_title(entry.get("title"))
        dup, why = None, None
        if doi and by_doi.get(doi):
            dup, why = by_doi[doi], "same DOI"
        elif url and by_url.get(url):
            dup, why = by_url[url], "same URL"
        elif title:
            for s in existing:
                if _same_title(title, _norm_title(s.get("title"))):
                    dup, why = s, "same title"
                    break
        if dup is not None:
            skipped.append({"key": key, "existing_key": dup["key"], "why": why})
            continue
        new_key = key
        if new_key in by_key:                       # same key, different work
            n = 2
            while f"{key}{n}" in by_key:
                n += 1
            new_key = f"{key}{n}"
            renamed.append({"from": key, "to": new_key})
        m_entry = _manifest_entry(new_key, entry)
        existing.append(m_entry)
        by_key[new_key] = m_entry
        if doi:
            by_doi[doi] = m_entry
        if url:
            by_url[url] = m_entry
        added.append(m_entry)

    if added:
        manifest["sources"] = existing
        manifest.setdefault("merges", []).append({
            "input": os.path.abspath(input_path),
            "bibliography": os.path.abspath(bib_used),
            "added_keys": [s["key"] for s in added],
        })
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        with open(refs_path, "a", encoding="utf-8") as f:
            f.write(f"# merged from {os.path.basename(input_path)}\n")
            for s in added:
                f.write(f"{s['key']} = {s['key']}.pdf\n")
        os.makedirs(os.path.join(project_dir, "sources"), exist_ok=True)

    logger.info(f"Merge: {len(added)} new source(s), {len(skipped)} duplicate(s) skipped, "
                f"{len(renamed)} key(s) renamed")
    return {"manifest": manifest_path, "refs": refs_path,
            "added": [s["key"] for s in added], "skipped": skipped, "renamed": renamed,
            "needs_search": [s["key"] for s in added if s["status"] == "needs_search"]}


def _manifest_entry(key: str, bib_entry: Optional[Dict]) -> Dict:
    if bib_entry is None:
        return {"key": key, "title": None, "author": None, "year": None,
                "url": None, "doi": None, "suggested_filename": f"{key}.pdf",
                "status": "not_in_bibliography"}
    has_link = bool(bib_entry.get("url") or bib_entry.get("doi"))
    entry = {
        "key": key,
        "title": bib_entry.get("title") or None,
        "author": bib_entry.get("author") or None,
        "year": bib_entry.get("year") or None,
        "url": bib_entry.get("url"),
        "doi": bib_entry.get("doi"),
        "suggested_filename": f"{key}.pdf",
        # "has_link": a fetch can be attempted (item 2); "needs_search": no url/DOI
        # was ever given — find it by title/author, don't attempt a fetch.
        "status": "has_link" if has_link else "needs_search",
    }
    # Rich ids from a database-backed importer (paper_importer): carried into the
    # manifest so the downloader can skip lookups it has already done. Absent for
    # .bib imports — backward compatible.
    for f in ("arxiv_id", "pmc_id", "s2_paper_id", "oa_pdf_url"):
        if bib_entry.get(f):
            entry[f] = bib_entry[f]
    return entry
