#!/usr/bin/env python3
"""Coverage gate scorer (owner-approved 2026-07-10) — the NEW-standard gate.

The 3-paper verdict gate (regression_check.py) only proves verdicts didn't
move; it says nothing about the owner standard ("the SHOWN sentences must
prove every component"). This scorer grades the covering-set output
(ARCHITECTURE §6.5) against a ground truth distilled from the Fable/Opus
grader's answer keys, which carry positive signal at the sentence level even
though the audited claims were all negative at the claim level:

- "must_cover" (the grader's tool-fetch rows — proof EXISTS in the source and
  was quoted): the claim must be supported, must have a covering set, and
  every listed anchor (a short distinctive substring of the grader-quoted
  proof) must appear in the union of covered sentences. Catches
  "everything's amber forever" over-strictness and retrieval regressions.
- "must_flag" (the grader's author-fix rows — a component is NOT provable
  from the cited source): the claim must either be judged unsupported, or
  carry an uncovered component matching one of the flag_terms. Catches
  over-claiming — the worst failure for trust.
- "watch": tracked and printed, never a failure (e.g. a known
  false-unsupported verdict the coverage pass can't reach yet).

No API calls; reads existing analysis.json files.

Usage: coverage_check.py --analysis <analysis.json> --ground-truth <gt.json>
"""
import argparse
import json
import re
import sys
import unicodedata


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"')
    s = s.replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", s).casefold().strip()


def check(analysis: dict, gt: dict):
    """Returns (failures, watch_lines, n_hard). Pure; testable offline."""
    claims = {c.get("id"): c for c in analysis.get("text_claims", [])}
    failures, watch_lines, n_hard = [], [], 0

    for row in gt.get("claims", []):
        cid, kind = row.get("id"), row.get("kind")
        c = claims.get(cid)
        if kind == "watch":
            v = (c or {}).get("verdict", "MISSING CLAIM")
            watch_lines.append(f"  {cid}: {v} — {row.get('note', '')}")
            continue
        n_hard += 1
        if c is None:
            failures.append(f"{cid}: claim not found in analysis")
            continue
        cov = c.get("covering") or {}
        covered_text = _norm(" ".join(e.get("sentence") or ""
                                      for e in cov.get("covered", [])))
        uncovered = [_norm(u) for u in (cov.get("uncovered") or [])]

        if kind == "must_cover":
            if c.get("verdict") != "supported":
                failures.append(f"{cid}: expected supported+covered, verdict is "
                                f"{c.get('verdict')}")
                continue
            if not cov.get("covered"):
                failures.append(f"{cid}: no covering set (proof exists in the "
                                f"source — grader quoted it)")
                continue
            missing = [a for a in row.get("anchors", [])
                       if _norm(a) not in covered_text]
            if missing:
                failures.append(f"{cid}: covering set lacks proof anchor(s): "
                                + "; ".join(repr(a) for a in missing))
        elif kind == "must_flag":
            if c.get("verdict") != "supported":
                continue          # flagged by verdict — pass
            terms = [_norm(t) for t in row.get("flag_terms", [])]
            if not any(t in u for t in terms for u in uncovered):
                failures.append(f"{cid}: OVER-CLAIMING — unprovable component "
                                f"({row.get('note', '?')}) not in the amber "
                                f"uncovered list")
        else:
            failures.append(f"{cid}: unknown ground-truth kind {kind!r}")
    return failures, watch_lines, n_hard


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--analysis", required=True)
    ap.add_argument("--ground-truth", required=True)
    args = ap.parse_args()

    with open(args.analysis, encoding="utf-8") as f:
        analysis = json.load(f)
    with open(args.ground_truth, encoding="utf-8") as f:
        gt = json.load(f)

    failures, watch_lines, n_hard = check(analysis, gt)

    print(f"\nCoverage expectations: {n_hard - len(failures)}/{n_hard} pass")
    if watch_lines:
        print("Watch (tracked, never a failure):")
        for w in watch_lines:
            print(w)
    if failures:
        print("\nFAILURES:")
        for x in failures:
            print("  ✗ " + x)
        print("\nREGRESSION on the owner evidence standard — do not ship.")
        sys.exit(1)
    print("\nOK — shown-evidence coverage matches the grader answer key.")


if __name__ == "__main__":
    main()
