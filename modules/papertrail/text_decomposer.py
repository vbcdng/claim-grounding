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

# --- Narrative-citation attribution stubs -----------------------------------
#
# A marker normally closes a completed sentence ("...ferrets [[a]]."), so "the
# text preceding a marker is the claim it cites" holds. But NARRATIVE citations
# put the marker mid-sentence, right after an attribution phrase that opens the
# sentence: "Kim et al.[[a]] demonstrated airborne transmission in ferrets." /
# "In contrast to other reports[[a]] we found that...". Splitting there yields
# a claim that's just a name (judged unsupported — a name is not a claim) and
# strands the real assertion with no marker (judged "own"/uncited). Both wrong.
#
# The fix: when the segment before a marker-group is an ATTRIBUTION STUB — not
# a claim in its own right — carry it (text + markers) forward instead of
# emitting it, so it merges into the following segment. Deliberately narrow
# (word cap, no internal sentence punctuation, closed shape list): a false
# positive here would swallow a real claim, so when in doubt this returns
# False and today's split-before-the-marker behaviour is kept.

# Closed list of narrative-citation "frame openers" — a stub matching shape
# (c) below starts with one of these and has no other clause. Extend this list
# (not the matching logic) if a new framing phrase turns up.
_FRAME_OPENERS = (
    "in contrast to", "by contrast", "in line with", "consistent with", "unlike",
    "compared with", "compared to", "according to", "based on", "using",
    "as reported by", "as shown by", "as described by", "following",
    "similar to", "similarly to",
)

_AUTHOR_TOKEN = r"[A-Z][A-Za-z'’\-]*"
_YEAR = r"\(?\d{4}\)?"

# Shape (a): "Kim et al" / "Kim et al." optionally followed by a comma,
# parenthesis or year, e.g. "Kim et al. (2020)", "Kim and Lee et al.,".
_ET_AL_STUB_RE = re.compile(
    rf"^{_AUTHOR_TOKEN}(?:\s+(?:and|&)\s+{_AUTHOR_TOKEN})?"
    rf"\s+et\s+al\.?[.,)]*\s*(?:{_YEAR})?[.,)]*$",
    re.IGNORECASE,
)

# Shape (b): a bare author-name phrase -- e.g. "Kim and Lee", "Kim (2019)",
# "Kim, Lee & Park (2019)". A SINGLE capitalised word on its own is NOT enough:
# an enumeration of proper nouns ("...in the United States[[a]], China[[b]], and
# Japan[[c]]") would otherwise read every item as a stub and merge the list into
# one claim, losing the per-item citations — the exact list shape the tool is
# supposed to keep separate. So a lone name must be followed by a year, and
# anything shorter stays a claim of its own.
_AUTHOR_LIST_STUB_RE = re.compile(
    rf"^{_AUTHOR_TOKEN}(?:"
    rf"(?:\s*(?:,|and|&)\s*{_AUTHOR_TOKEN})+\s*(?:{_YEAR})?"   # two or more names
    rf"|\s*{_YEAR}"                                            # one name + a year
    rf")$"
)

# Used only to decide condition 2 (no sentence-ending punctuation INSIDE the
# stub): "et al."'s own period is an abbreviation, not a sentence end, so it's
# stripped out before checking for a stray '.'/'!'/'?'.
_ET_AL_TOKEN_RE = re.compile(r"et\s+al\.?", re.IGNORECASE)


def _starts_with_frame_opener(lowered: str) -> bool:
    return any(lowered == opener or lowered.startswith(opener + " ")
               for opener in _FRAME_OPENERS)


def _is_attribution_stub(seg: str) -> bool:
    """True if `seg` (the text immediately before a marker-group, markers
    already stripped) is a narrative-citation attribution stub — a sentence
    OPENER that names or frames the source and carries no claim of its own —
    rather than the assertion the marker is citing.

    All of these must hold, per the design spec (docs/ARCHITECTURE.md §5.1):
    short (<=6 words), no sentence-ending punctuation inside it (so it reads
    as an opener, not the tail of a prior sentence), and one of: ends in
    "et al[.]" (+ optional comma/paren/year), is a bare author-name list, or
    starts with a closed frame-opener phrase. Conservative by construction —
    every check narrows, never widens, what counts as a stub."""
    seg = seg.strip()
    if not seg:
        return False
    if len(seg.split()) > 6:
        return False
    if re.search(r"[.!?]", _ET_AL_TOKEN_RE.sub("", seg)):
        return False
    if _ET_AL_STUB_RE.match(seg):
        return True
    if _AUTHOR_LIST_STUB_RE.match(seg):
        return True
    return _starts_with_frame_opener(seg.lower())


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
    preceding claim instead.

    Narrative citations (see _is_attribution_stub above): when the segment
    before a marker-group is an attribution stub ("Kim et al.", "In contrast
    to other reports"), it is not emitted on its own — it's carried forward
    (text + markers) and merged into the NEXT segment in this block, so the
    marker ends up on the sentence it actually cites instead of on a bare
    name. If the stub reaches the end of the block with nothing to merge into
    (a paragraph ending right after the stub), it's emitted as-is — never
    dropped, never merged across a paragraph boundary."""
    out: List[tuple] = []
    pending: tuple = None    # (stub_text, stub_markers) carried forward, not yet emitted
    pos = 0
    for m in _MARKER_GROUP_RE.finditer(text):
        seg = _strip_orphan_punct(_clean(text[pos:m.start()]))
        markers = MARKER_RE.findall(m.group(0))
        if _has_content(seg):
            if pending is not None:
                stub_text, stub_markers = pending
                seg = f"{stub_text} {seg}".strip()
                markers = stub_markers + markers
                pending = None
            if _is_attribution_stub(seg):
                pending = (seg, markers)     # carry forward instead of emitting
            else:
                out.append((seg, markers))
        elif pending is not None:
            # marker with no new text -> attach to the pending stub, not out[-1]
            stub_text, stub_markers = pending
            pending = (stub_text, stub_markers + markers)
        elif out:                       # marker with no new text -> previous claim
            out[-1] = (out[-1][0], out[-1][1] + markers)
        pos = m.end()
    tail = _strip_orphan_punct(_clean(text[pos:]))
    if pending is not None:
        stub_text, stub_markers = pending
        if _has_content(tail):
            out.append((f"{stub_text} {tail}".strip(), stub_markers))
        else:
            # nothing left in this block to merge into -- keep today's
            # behaviour rather than dropping the stub.
            out.append((stub_text, stub_markers))
    elif _has_content(tail):
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
