"""Card S1 (card 395): the quote check of step 0. No model.

check(piece, quote, work_texts) answers two questions:
- quote_in_work: does the quote stand in one of the cited works word for word? Spacing, line
  breaks, curly or straight quotation marks, dashes, soft hyphens and upper or lower case are
  evened out first; nothing else. A quote with "..." or "[...]" is cut there and every part must
  stand in the same work, in order.
- covers_piece: does every number, year and name of the piece stand in the quote? A number
  counts as found when the same number stands in the quote ("40 percent" and "40%" both hold 40;
  "1,200" and "1200" are the same). A name is a word with a capital letter that is not the first
  word of the piece, or a word in capitals such as "IoT" or "PCR".

What it cannot check: a condition or a meaning. "Without climate change, about 40 percent" passes
when the quote holds 40 and the names, even if the work never states the condition. A branch
that needs this asks a model (plan page, worked example).
"""
import argparse
import json
import re
import sys
import unicodedata

_TRANS = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
                        "“": '"', "”": '"', "„": '"', "″": '"',
                        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
                        "−": "-", " ": " ", "­": None})
_GAP = re.compile(r"\s*(?:\[\s*(?:\.\s*){3}\]|\.\s*\.\s*\.|…)\s*")
_NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")
# capitalised words that are not names when they open a clause or are common
_NOT_NAMES = {"The", "A", "An", "In", "On", "Of", "And", "But", "Or", "If", "It", "Its", "This",
              "That", "These", "Those", "There", "They", "We", "I", "He", "She", "For", "As", "At",
              "By", "To", "With", "From", "While", "When", "Since", "Some", "Most", "Many", "One"}


def norm(text):
    t = unicodedata.normalize("NFKC", text or "").translate(_TRANS)
    t = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", t)      # a word broken over a line end
    return re.sub(r"\s+", " ", t).strip().lower()


def quote_in_work(quote, work_texts):
    parts = [norm(p) for p in _GAP.split(quote or "")]
    parts = [p.strip(" \"'") for p in parts if p.strip(" \"'.")]
    if not parts:
        return False
    for text in work_texts:
        t, pos, ok = norm(text), 0, True
        for p in parts:
            i = t.find(p, pos)
            if i < 0:
                ok = False
                break
            pos = i + len(p)
        if ok:
            return True
    return False


def numbers(text):
    out = []
    for m in _NUM.finditer(unicodedata.normalize("NFKC", text or "")):
        n = m.group(0).rstrip(",").replace(",", "")
        if n.endswith(".0"):
            n = n[:-2]
        out.append(n)
    return out


def names(text):
    words = _WORD.findall(unicodedata.normalize("NFKC", text or "").translate(_TRANS))
    out = []
    for i, w in enumerate(words):
        caps = sum(1 for c in w if c.isupper())
        if caps >= 2 or (i > 0 and w[0].isupper() and w not in _NOT_NAMES):
            out.append(w)
    return out


def items(piece):
    """The numbers (and years) and names of a piece, each once, in order."""
    seen, out = set(), []
    for kind, vals in (("number", numbers(piece)), ("name", names(piece))):
        for v in vals:
            if (kind, v) not in seen:
                seen.add((kind, v))
                out.append((kind, v))
    return out


def check(piece, quote, work_texts):
    q_nums = set(numbers(quote))
    q_words = {w.lower() for w in _WORD.findall(norm(quote))}
    missing = []
    for kind, v in items(piece):
        found = v in q_nums if kind == "number" else v.lower() in q_words
        if not found:
            missing.append(f"{kind}:{v}")
    found = quote_in_work(quote, work_texts)
    return {"quote_in_work": found, "covers_piece": not missing, "missing": missing,
            "passed": bool(found and not missing)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Card S1: does a quote stand in the cited work, and does it hold "
                                             "every number and name of the piece? No model.")
    ap.add_argument("--piece", required=True)
    ap.add_argument("--quote", required=True)
    ap.add_argument("--work", action="append", required=True, help="a cited work file (repeatable)")
    a = ap.parse_args(argv)
    texts = [open(p, encoding="utf-8", errors="replace").read() for p in a.work]
    print(json.dumps(check(a.piece, a.quote, texts), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
