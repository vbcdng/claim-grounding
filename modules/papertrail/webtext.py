"""Boilerplate filters for web-page source text (owner walkthrough 2026-07-07,
todo item 8): saved web pages carried bylines ("By Konstantin F. Pilz, …"),
publish dates ("Published On 19 Jun 2026"), photo-credit captions
("… [Patrick Sison/AP Photo]"), and whole related-articles headline dumps
(agenceeurope2026: ~40 unrelated headlines) into the sentence index, where they
resurfaced verbatim as a claim's "evidence". Two consumers:

- `direct_downloader.extract_page_text` runs `drop_boilerplate_lines` on every
  page save (plus its own DOM-level nav/link-density stripping);
- `source_decomposer.read_source_pages` runs it when reading a `.txt` source
  that carries the downloader's "Source URL:" preamble — so the sources already
  on disk get clean sentence indexes on the (no-LLM) schema upgrade, without a
  re-download.

Every rule is deliberately NARROW: dropping real prose is worse than keeping a
stray byline. Prose paragraphs in saved pages are single lines ending with
sentence punctuation; every rule requires the absence of that, a hard pattern,
or both.
"""
import re
from typing import List

# Lines that are dropped on their own, wherever they appear.
_TERMINAL = '.!?"”’)'          # sentence-final punctuation (incl. curly quotes)
_LINE_RES = [
    # publish/update stamps: "Published On 19 Jun 2026", "Updated 19 June 2026 10:02"
    re.compile(r"(?i)^(published|updated|last updated|posted)\s*(on|at|:)?\s.*\d{4}.*$"),
    # bare dates: "19 Jun 2026", "May 1, 2026", "Jun. 5, 2025", "15/06/2026"
    re.compile(r"(?i)^\d{1,2}\s+\w{3,9}\.?\s+\d{4}$"),
    re.compile(r"(?i)^\w{3,9}\.?\s+\d{1,2},?\s+\d{4}$"),
    re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$"),
    # photo credits: a line ending in "[Someone/Agency]" or "[… Photo]"
    re.compile(r"(?i)\[[^\[\]]*(?:/|photo|image|getty|reuters|afp|ap|epa)[^\[\]]*\]$"),
]
# Site chrome (share bars, login walls, nav stubs) — short lines only: a real
# paragraph that happens to open with "Subscribe" keeps its full-sentence length.
_CHROME_RE = re.compile(
    r"(?i)^(skip to (main )?content|share (this|on)|follow us|sign ?up|"
    r"subscribe|log ?in|please log ?in|related (articles?|stories|posts)|"
    r"read (more|next)|advertisement|sponsored|cookie(s| policy| settings)|"
    r"accept (all )?cookies|newsletter|table of contents|jump to|back to top|"
    r"print this|save this article|listen to this article)\b")
_CHROME_MAX_WORDS = 8       # the site-chrome rule only fires on short lines
_BYLINE_MAX_WORDS = 20

# Headline-dump runs (related-articles/nav sections that survived DOM filters):
# >= _RUN_MIN consecutive headline-shaped lines — starts upper/digit, 4..30 words,
# NO terminal punctuation — are dropped as a block. Prose never looks like this:
# saved pages keep one full paragraph per line, ending with punctuation.
_RUN_MIN = 4
_HEADLINE_MIN_WORDS = 4
_HEADLINE_MAX_WORDS = 30


def _is_byline(line: str) -> bool:
    """"By Konstantin F. Pilz, Robi Rahman, and Lennart Heim" — a By-prefixed
    name list without sentence-final punctuation. A prose sentence starting
    with 'By' ("By 2030, demand doubles.") ends with punctuation and survives."""
    if not re.match(r"By\s+[A-Z]", line):
        return False
    words = line.split()
    return len(words) <= _BYLINE_MAX_WORDS and not line.rstrip().endswith(tuple(_TERMINAL))


def _is_headline_shaped(line: str) -> bool:
    s = line.strip()
    if not s or s.rstrip().endswith(tuple(_TERMINAL)):
        return False
    n = len(s.split())
    return (_HEADLINE_MIN_WORDS <= n <= _HEADLINE_MAX_WORDS
            and (s[0].isupper() or s[0].isdigit()))


def _drop_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False                      # blank lines are structure, keep
    if _is_byline(s):
        return True
    # all-caps section headers ("SECTORAL POLICIES /", "ECONOMY - FINANCE") —
    # never lines with digits: "1. USA, 74.5%" is a DATA ROW (audit t6 keeps
    # numeric table rows as real evidence)
    if (len(s.split()) <= 6 and s.upper() == s and re.search(r"[A-Z]{3}", s)
            and not re.search(r"\d", s)):
        return True
    if len(s.split()) <= _CHROME_MAX_WORDS and _CHROME_RE.search(s):
        return True
    return any(rx.search(s) for rx in _LINE_RES)


def drop_boilerplate_lines(text: str) -> str:
    """Filter web boilerplate from page text, line-wise + headline-run-wise."""
    lines = [ln for ln in (text or "").splitlines() if not _drop_line(ln)]
    out: List[str] = []
    run: List[str] = []
    for ln in lines + [""]:               # sentinel flushes the last run
        if _is_headline_shaped(ln):
            run.append(ln)
            continue
        if run:
            if len(run) < _RUN_MIN:
                out.extend(run)           # short run: probably real headings/prose
            run = []
        out.append(ln)
    while out and not out[-1].strip():    # drop the sentinel / trailing blanks
        out.pop()
    return "\n".join(out)
