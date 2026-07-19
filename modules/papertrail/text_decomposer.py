"""
Stage 2 (PaperTrail-adapted): decompose the user's own writing into claims.

Because the user cites sources with explicit per-sentence [[key]] markers, each
sentence is treated as one claim and the markers on it are its citations. This
preserves the marker->source attribution exactly (and costs no LLM calls), which
is more reliable for marker-based input than LLM re-atomization would be.
"""

import os
import re
import logging
from typing import List, Dict, Tuple

logger = logging.getLogger(__name__)

MARKER_RE = re.compile(r"\[\[([A-Za-z0-9_-]+)\]\]")
_REFERENCES_HEADER_RE = re.compile(r"^\s*\[References\]\s*$", re.IGNORECASE | re.MULTILINE)
_FRONTMATTER_RE = re.compile(r"\A﻿?\s*---[ \t]*\n(.*?)\n---[ \t]*\n", re.S)


def strip_frontmatter(text: str) -> Tuple[str, str]:
    """(title, body). A leading pandoc-style `---` frontmatter block never
    becomes claims; its `title:` value names the piece (the viewer uses it for
    the review filename). Tolerates no frontmatter — ("", text)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return "", text
    title = ""
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        if k.strip().lower() == "title":
            title = v.strip().strip("'\"")
    return title, text[m.end():]


def parse_references(text: str, refs_path: str = None, text_path: str = None) -> Tuple[Dict[str, str], str]:
    """
    Resolve the marker -> filename map and return (refs_map, body_text).

    Order of precedence: explicit --references file, then <text>.refs.txt sibling,
    then a trailing "[References]" block inside the text itself.
    """
    _, text = strip_frontmatter(text)
    body = text
    raw = None

    if refs_path and os.path.exists(refs_path):
        with open(refs_path, "r", encoding="utf-8") as f:
            raw = f.read()
    elif text_path:
        sibling = text_path + ".refs.txt"
        if os.path.exists(sibling):
            with open(sibling, "r", encoding="utf-8") as f:
                raw = f.read()

    if raw is None:
        m = _REFERENCES_HEADER_RE.search(text)
        if m:
            raw = text[m.end():]
            body = text[:m.start()]

    refs_map = _parse_refs_lines(raw) if raw else {}
    logger.info(f"Parsed {len(refs_map)} reference mapping(s): {list(refs_map.keys())}")
    return refs_map, body


def _parse_refs_lines(raw: str) -> Dict[str, str]:
    refs = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, filename = line.split("=", 1)
        key, filename = key.strip(), filename.strip()
        if key and filename:
            refs[key] = filename
    return refs


# Adjacent markers form ONE citation group even when the author separates them
# with ';' or ',' (grouped citations like "([[a]]; [[b]])"), not only whitespace —
# otherwise the ';' between them and the ')' after them fall out as their own
# punctuation-only "claims".
_MARKER_GROUP_RE = re.compile(r"(?:\[\[[A-Za-z0-9_-]+\]\][\s;,]*)+")

# Punctuation orphaned once a citation group is removed: a trailing open bracket
# ("...as shown (") or leading close/scaffolding punctuation ("). Reviews ...").
_ORPHAN_OPEN_RE = re.compile(r"[\s([{]+$")
_ORPHAN_CLOSE_RE = re.compile(r"^[\s)\]}.;,]+")


def extract_claims(body_text: str) -> List[Dict]:
    """
    Split the body into claims using the [[key]] MARKERS as delimiters (not sentence
    boundaries). A marker cites the text that precedes it; text up to and including a
    marker-group is one claim. This is robust to abbreviations like "et al." / "e.g."
    that would otherwise break a sentence splitter.

    Recommended authoring: put a marker at the end of EACH cited sentence (not
    once per paragraph) so each source-statement becomes its own claim; a marker
    on a multi-sentence run makes the whole run one claim to prove against that
    source. Text with no marker becomes an uncited ("own") claim. See the
    authoring guidance in docs/CONVERT_MY_TEXT_PROMPT.md.

    Returns ordered list of {id, text, markers:[key,...]}.
    """
    paragraphs = re.split(r"\n\s*\n", body_text)
    units: List[tuple] = []  # (clean_text, [markers])
    for para in paragraphs:
        units.extend(_segment_by_markers(para))

    claims: List[Dict] = []
    for text, markers in units:
        claims.append({"id": f"t{len(claims)}", "text": text,
                       "markers": list(dict.fromkeys(markers))})
    logger.info(f"Extracted {len(claims)} claim(s) from the text")
    return claims


def _segment_by_markers(text: str) -> List[tuple]:
    """Segment one block into (clean_text, [markers]) units, splitting on marker-groups.

    Grouped citations ([[a]]; [[b]]) count as ONE group, and punctuation-only
    segments (the ')' / ';' scaffolding left between or after markers) are never
    emitted as claims — a marker with no new text before it attaches to the
    preceding claim instead."""
    out: List[tuple] = []
    pos = 0
    for m in _MARKER_GROUP_RE.finditer(text):
        seg = _strip_orphan_punct(_clean(text[pos:m.start()]))
        markers = MARKER_RE.findall(m.group(0))
        if _has_content(seg):
            out.append((seg, markers))
        elif out:                       # marker with no new text -> previous claim
            out[-1] = (out[-1][0], out[-1][1] + markers)
        pos = m.end()
    tail = _strip_orphan_punct(_clean(text[pos:]))
    if _has_content(tail):
        out.append((tail, []))          # trailing uncited text
    return out


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", MARKER_RE.sub("", s)).strip()


def _has_content(s: str) -> bool:
    """A real claim has at least one letter or digit — pure punctuation is scaffolding."""
    return bool(re.search(r"[A-Za-z0-9]", s))


def _strip_orphan_punct(s: str) -> str:
    """Trim bracket/scaffolding punctuation stranded at a claim boundary once the
    citation markers were removed (trailing '(' or leading ')'/'.'/';')."""
    return _ORPHAN_OPEN_RE.sub("", _ORPHAN_CLOSE_RE.sub("", s)).strip()


def _sentence_split(text: str) -> List[str]:
    # Normalise newlines to spaces but keep markers attached to their sentence.
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    try:
        import nltk
        return [s.strip() for s in nltk.sent_tokenize(text) if s.strip()]
    except Exception:
        return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
