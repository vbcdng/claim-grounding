"""Honest rendering of a proof quote on a claim card (task #4, 2026-09-02).

The card's job is to show the reader the source's own words. Three ways the
stored sentence index breaks that promise, all display-side, all fixed here:

1. **Glued sentences.** PDF text puts superscript reference markers on the line
   ("...carcinoma (HCC).2 , 3 Cirrhosis is characterised by...") and the
   sentence splitter keeps both sentences as one. The card then quotes two
   statements from different places as if they were one continuous sentence —
   the fresh50:cidev0028 defect, where a prevalence statistic and a hedged
   clause read together as a causal chain. `split_glued` cuts them apart so the
   card can quote them separately and say they are not contiguous.

2. **Quotes cut off mid-sentence.** The same splitter sometimes ends a stored
   sentence in the middle of a clause ("...leading to concerns that these").
   The judged window holds the rest of that sentence verbatim, so
   `complete_cutoff` finishes the quote from the window — still the source's
   own words, never invented.

3. **Not prose at all.** A reference-list line or a dumped table row can reach
   the card as "proof" (fresh50:cidev0005, pilot100:cidev0060). `flaws` names
   what it is so the card can label it instead of passing it off as a
   statement the source makes.

Pure string work: no model calls, no network, no verdict field is read or
written. Every function is safe on empty or malformed input.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# --- 1. superscript-citation glue -------------------------------------------
# "(HCC).2 , 3 Cirrhosis is" — a sentence end, then the reference numbers the
# PDF printed as superscripts, then the next sentence. Deliberately narrow:
#   * the character before the full stop must NOT be a digit, so a decimal in a
#     table ("Diatomite 2.7 The relatively high...") never matches;
#   * the marker run is 1-3 digits, optionally repeated with commas/dashes,
#     which is what reference numbering looks like — never a year or a measure;
#   * the next sentence must start with a capitalised word of three letters or
#     more, so initials and units do not trigger it.
_GLUE_RE = re.compile(
    r"(?<=[^\d\s])([.!?][\"')\]]?)"          # a real sentence end
    r"\s*(\d{1,3}(?:\s*[,;–—-]\s*\d{1,3})*)"  # the superscript reference run
    r"\s+(?=[A-Z][a-z]{2,})")                 # the next sentence starts here

_MIN_PART_WORDS = 4       # a shorter tail is a fragment, not a second sentence


def split_glued(sentence: str) -> List[str]:
    """The one stored 'sentence' cut back into the separate source sentences it
    actually holds. A sentence with no glue comes back unchanged, as one part."""
    s = (sentence or "").strip()
    if not s:
        return []
    parts: List[str] = []
    last = 0
    for m in _GLUE_RE.finditer(s):
        head = s[last:m.start()] + m.group(1)
        if len(head.split()) >= _MIN_PART_WORDS:
            parts.append(head.strip())
            last = m.end()
    tail = s[last:].strip()
    if tail:
        if parts and len(tail.split()) < _MIN_PART_WORDS:
            parts[-1] = (parts[-1] + " " + tail).strip()
        else:
            parts.append(tail)
    return parts or [s]


# --- 2. quotes cut off mid-sentence -----------------------------------------
_ENDS_SENTENCE_RE = re.compile(r"[.!?][\"')\]]?\s*$")
_MAX_COMPLETION_CHARS = 300   # never glue a whole paragraph onto a quote


def ends_mid_sentence(sentence: str) -> bool:
    """True when the stored quote stops without any sentence-ending mark."""
    s = (sentence or "").strip()
    return bool(s) and not _ENDS_SENTENCE_RE.search(s)


def complete_cutoff(sentence: str, window: Optional[str]) -> Tuple[str, bool]:
    """(quote, was_completed). When the quote stops mid-sentence and the judged
    window — which is source text — carries the rest of that sentence, the rest
    is appended verbatim. Nothing is invented: with no window, or no
    continuation inside it, the quote comes back exactly as stored."""
    s = (sentence or "").strip()
    if not s or not window or not ends_mid_sentence(s):
        return s, False
    i = window.find(s)
    if i < 0:
        return s, False
    rest = window[i + len(s):]
    m = re.match(r"[^.!?]{1,%d}[.!?][\"')\]]?" % _MAX_COMPLETION_CHARS, rest)
    if not m:
        return s, False
    completed = (s + m.group(0)).strip()
    return completed, True


# --- 3. the quote is not prose ----------------------------------------------
_TABLE_PIPES = 3          # "| 322 (43.2%)| 274 (46.1%)| ..." — a dumped table row
_NUM_TOKEN_RE = re.compile(r"^[\[(]?[-+±<>=]?\d[\d.,;:%–—/()\[\]-]*$")
_WORD_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z'-]*$")


def looks_like_table_row(sentence: str) -> bool:
    """A dumped table row: either several column bars, or a token run that is
    mostly bare numbers. A prose sentence quoting statistics ('OR 3.11; 95% CI
    2.12-4.55; p<0.001') keeps enough real words to stay clear of this."""
    s = (sentence or "").strip()
    if not s:
        return False
    if s.count("|") >= _TABLE_PIPES:
        return True
    toks = s.split()
    if len(toks) < 8:
        return False
    nums = sum(1 for t in toks if _NUM_TOKEN_RE.match(t))
    words = sum(1 for t in toks if _WORD_TOKEN_RE.match(t))
    return nums >= 8 and nums > words


def flaws(sentence: str, unusable=None) -> List[str]:
    """Names for what is wrong with this quote as a piece of proof: 'reference'
    (a bibliography or citation line), 'table' (a row of numbers). `unusable` is
    the matcher's own reference/citation-line test, injected so this module has
    no import back into the matcher; when it is not supplied only the table test
    runs."""
    s = (sentence or "").strip()
    out: List[str] = []
    if not s:
        return out
    if unusable is not None:
        try:
            if unusable(s):
                out.append("reference")
        except Exception:
            pass
    if looks_like_table_row(s):
        out.append("table")
    return out


# --- what a card should render ----------------------------------------------
def prepare(sentence: str, window: Optional[str] = None, unusable=None) -> Dict:
    """Everything a card needs to quote this sentence honestly.

    Returns `parts` (one entry per real source sentence inside the stored one),
    `completed` (the quote was finished from the judged window), `split` (the
    stored sentence held more than one source sentence), `cut_off` (it still
    stops mid-sentence after any completion) and `flaws` (see above)."""
    s = (sentence or "").strip()
    if not s:
        return {"parts": [], "completed": False, "split": False,
                "cut_off": False, "flaws": []}
    parts = split_glued(s)
    completed = False
    if len(parts) == 1:
        parts[0], completed = complete_cutoff(parts[0], window)
    else:
        last, completed = complete_cutoff(parts[-1], window)
        parts[-1] = last
    return {"parts": parts,
            "completed": completed,
            "split": len(parts) > 1,
            "cut_off": ends_mid_sentence(parts[-1]),
            "flaws": flaws(s, unusable)}


def primary_text(sentence: str, window: Optional[str] = None) -> str:
    """The text a Copy button should copy and a source search should look for:
    the corrected FIRST source sentence, never the glued blob."""
    p = prepare(sentence, window)
    return p["parts"][0] if p["parts"] else (sentence or "")
