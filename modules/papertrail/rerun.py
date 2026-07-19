"""
Incremental re-verification: match a new run's claims against the previous
analysis.json so UNCHANGED claims (same text + markers) reuse their verdicts —
only edited/new claims cost LLM calls. Pure matching, no API, no file I/O.

Source-content safety: verify_my_text.py records each cited file's content hash
in metadata.source_hashes; `changed_source_files` compares runs so claims citing
a replaced file are re-judged automatically. --full remains the manual override
(and the only guard against analyses that predate hash recording).
"""

import re
import difflib
from typing import List, Dict, Any

# Minimum similarity for the DIFF display's "this new claim is an edit of that
# old one" pairing. Pairing is presentation-only (the claim is re-judged either
# way), so a moderate bar is fine — below it the claim just shows as "new".
FUZZY_RATIO = 0.6


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _key(c: Dict[str, Any]):
    return (_norm(c.get("text")), tuple(sorted(c.get("markers") or [])))


def match_claims(prev_claims: List[Dict], new_claims: List[Dict]) -> Dict[str, Dict]:
    """For every new claim id: {"reuse": <previous out-claim> | None,
    "prev": {"text", "verdict"} | None}.

    - Exact match (normalized text + marker set) -> "reuse" (verdict carries over).
      Duplicate texts pair one-to-one in document order.
    - No exact match -> best-effort fuzzy pairing against the LEFTOVER previous
      claims (difflib ratio >= FUZZY_RATIO, same-marker pairs preferred) -> "prev"
      powers the viewer's "was: ..." diff note. The claim is still re-judged.
    - Neither -> both None (a brand-new claim).
    """
    prev_by_key: Dict[Any, List[Dict]] = {}
    for p in prev_claims:
        prev_by_key.setdefault(_key(p), []).append(p)

    out: Dict[str, Dict] = {}
    unmatched_new: List[Dict] = []
    for c in new_claims:
        lst = prev_by_key.get(_key(c))
        if lst:
            out[c["id"]] = {"reuse": lst.pop(0), "prev": None}
        else:
            unmatched_new.append(c)

    leftovers = [p for lst in prev_by_key.values() for p in lst]
    for c in unmatched_new:
        best, best_r = None, 0.0
        c_norm = _norm(c.get("text"))
        c_marks = tuple(sorted(c.get("markers") or []))
        for p in leftovers:
            r = difflib.SequenceMatcher(None, c_norm, _norm(p.get("text"))).ratio()
            if tuple(sorted(p.get("markers") or [])) == c_marks:
                r += 0.05                      # same citation -> likelier the same claim
            if r > best_r:
                best, best_r = p, r
        if best is not None and best_r >= FUZZY_RATIO:
            out[c["id"]] = {"reuse": None,
                            "prev": {"text": best.get("text"), "verdict": best.get("verdict")}}
            leftovers.remove(best)
        else:
            out[c["id"]] = {"reuse": None, "prev": None}
    return out


def changed_source_files(prev_hashes, current_hashes: Dict[str, str]):
    """Filenames whose content changed (or wasn't hashed) since the previous run.

    Verdict reuse is only honest if the cited source files are byte-identical to
    what the previous run judged against. Returns the set of current filenames
    whose hash differs from (or is absent in) the previous run's record — claims
    citing them must be re-judged. Returns None when the previous analysis
    predates hash recording (caller decides; historic behavior was to trust)."""
    if prev_hashes is None:
        return None
    return {fn for fn, h in current_hashes.items() if prev_hashes.get(fn) != h}


def reusable(prev_claim: Dict[str, Any]) -> bool:
    """Judged verdicts carry over; so do 'own' claims — the verdict itself is
    free to rebuild, but the own_kind tag on it was PAID for (own-split, one
    LLM call each) and lives nowhere else. Not carried: missing-file claims
    (the file may exist by now) and legacy uncited claims recorded as
    'unsupported'/'no_citation_marker' before the 'own' verdict existed —
    rebuilding those is free and upgrades them to indigo 'own'."""
    verdict = prev_claim.get("verdict")
    reason = str(prev_claim.get("reason", ""))
    if verdict == "own":
        return True
    return (verdict in ("supported", "unsupported")
            and not reason.startswith("source_file_missing")
            and reason != "no_citation_marker")
