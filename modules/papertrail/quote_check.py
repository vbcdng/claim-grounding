"""Quotation-integrity check (prototype, 2026-07-16).

Deterministic, no LLM. A verbatim quotation ("...") in an author's claim is the
highest-trust signal a reader gets; if the quoted string is NOT present in the
source the claim cites, that is a misattributed/fabricated quote — a harder error
than the generic "not proven as written" amber, and it should be flagged as such.

For each cited claim: pull the quoted spans, and check each (normalized, with a
fuzzy fallback for whitespace/OCR noise) against the FULL text of every source the
claim cites. A quote found in none of them is a finding.

Scare-quotes / emphasis ("not", "hyper-responder") are excluded by a length gate;
we only verify quotes long enough to be genuine source quotations.

Public API:
    load_source_texts(run_dir) -> {key: text, paper_id: text}
    extract_quotes(text) -> [str]                 # verify-worthy quoted spans
    quote_in_text(quote, source_text) -> (bool, float)
    check_claim(claim, source_texts) -> [finding dict]
    check_run(run_dir) -> [finding dict]          # every cited claim
"""
import json, os, re, difflib

# A quote must be at least this many words to be treated as a source quotation
# (filters scare-quotes / single-word emphasis like "not", "lower").
MIN_QUOTE_WORDS = 6
# Fuzzy acceptance: longest common block / quote length. Handles minor
# whitespace, hyphenation, and OCR differences without accepting a paraphrase.
FUZZY_THRESHOLD = 0.85

# Straight and curly double quotes. (Single quotes are too noisy — apostrophes,
# scare-quotes — so the prototype verifies double-quoted spans only.)
_QUOTE_RE = re.compile(r'["“”]([^"“”]{3,}?)["“”]')


def _norm(s):
    """Lowercase, unify quotes/dashes, collapse every non-alphanumeric run to one
    space. Makes the match robust to punctuation/whitespace/OCR noise."""
    s = (s or "").lower()
    s = s.replace("’", "'").replace("‘", "'")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def extract_quotes(text):
    """Verify-worthy double-quoted spans from a claim's text."""
    out = []
    for m in _QUOTE_RE.finditer(text or ""):
        q = m.group(1).strip()
        if len(q.split()) >= MIN_QUOTE_WORDS:
            out.append(q)
    return out


def quote_in_text(quote, source_text):
    """(found, score). Exact normalized-substring match, else a fuzzy longest-
    common-block ratio against the quote length."""
    nq, ns = _norm(quote), _norm(source_text)
    if not nq:
        return False, 0.0
    if nq in ns:
        return True, 1.0
    sm = difflib.SequenceMatcher(None, nq, ns, autojunk=False)
    _, _, size = sm.find_longest_match(0, len(nq), 0, len(ns))
    score = size / len(nq)
    return score >= FUZZY_THRESHOLD, round(score, 3)


def load_source_texts(run_dir):
    """{key: fulltext, paper_id: fulltext} from the run's source_claims cache."""
    out = {}
    sc_dir = os.path.join(run_dir, "source_claims")
    if not os.path.isdir(sc_dir):
        return out
    for fn in os.listdir(sc_dir):
        if not fn.endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(sc_dir, fn), encoding="utf-8"))
        except Exception:
            continue
        text = " ".join(s.get("text", "") for s in (d.get("sentences") or []))
        for k in (d.get("key"), d.get("paper_id")):
            if k:
                out[k] = text
    return out


def check_claim(claim, source_texts):
    """Findings for one claim: each verify-worthy quote not present in ANY source
    the claim cites. Uncited claims and claims with no long quotes yield nothing."""
    keys = list(claim.get("markers") or []) + list(claim.get("paper_ids") or [])
    cited = [source_texts[k] for k in keys if k in source_texts]
    if not cited:
        return []
    findings = []
    for q in extract_quotes(claim.get("text", "")):
        best, best_score = False, 0.0
        for txt in cited:
            found, score = quote_in_text(q, txt)
            best_score = max(best_score, score)
            if found:
                best = True
                break
        if not best:
            findings.append({
                "id": claim.get("id"),
                "quote": q,
                "markers": claim.get("markers"),
                "best_score": best_score,
                "verdict": claim.get("verdict"),
            })
    return findings


def check_run(run_dir):
    analysis = json.load(open(os.path.join(run_dir, "analysis.json"), encoding="utf-8"))
    source_texts = load_source_texts(run_dir)
    findings = []
    for c in analysis.get("text_claims", []):
        if c.get("verdict") in ("supported", "unsupported"):
            findings.extend(check_claim(c, source_texts))
    return findings


if __name__ == "__main__":
    import sys
    run = sys.argv[1] if len(sys.argv) > 1 else "data/eggs_run"
    fs = check_run(run)
    print(f"Quotation-integrity check on {run}: {len(fs)} unverified quote(s)\n")
    for f in fs:
        print(f"  {f['id']} [{f['verdict']}] cite={f['markers']} best_match={f['best_score']}")
        print(f"    “{f['quote']}”\n")
