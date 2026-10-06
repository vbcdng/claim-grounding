#!/usr/bin/env python3
"""Task #19: how many rejected claim cards can actually say WHY they failed?

The card contract (docs/TASK19_CARD_WORDING_2026-08-07.md, case 4) is: a red
card must always state its objection in one sentence; an unexplained red card
is a bug, never shown as confident. This script measures how the runs on disk
fall out, so the contract is built against real numbers instead of a guess.

No model calls, no network: it only reads analysis.json files.

    venv/bin/python3 benchmarks/scan_red_card_explanations.py [root ...]

Buckets, one per rejected claim:
  parts_missing      the part list names at least one part that was not found,
                     so the card's ✗ rows already say what is missing
  all_found_reason   every part was found and the judges left an objection, so
                     the card quotes it (case 4, built 2026-09-02)
  all_found_silent   every part was found and no objection was recorded, so the
                     card says the verdict cannot be explained (case 4 tail)
  no_parts_reason    no part list at all, but an objection is recorded, so the
                     card prints it as the plain "Not supported: ..." line
  no_parts_silent    no part list and no objection: the card today says NOTHING
                     about why the sentence was rejected — the remaining hole
"""
import json
import sys
from collections import Counter
from pathlib import Path

BUCKETS = ["parts_missing", "all_found_reason", "all_found_silent",
           "no_parts_reason", "no_parts_silent"]


def bucket(claim):
    cc = claim.get("component_check") or {}
    found = cc.get("found") or []
    missing = cc.get("missing") or []
    reason = (claim.get("reason") or "").strip()
    if missing:
        return "parts_missing"
    if found:
        return "all_found_reason" if reason else "all_found_silent"
    return "no_parts_reason" if reason else "no_parts_silent"


MACHINE_REASON_PREFIXES = ("source_file_missing", "no_source_sentences", "judge_error",
                           "llm_error", "checks_failed", "no_evidence", "error")


def reason_flaw(reason, parts):
    """A red card can carry an objection and still not be readable. Returns a
    short label for the ways that happens, or "" when the objection is a plain
    sentence a reader can act on."""
    r = reason.strip()
    if not r:
        return ""
    if r.startswith(MACHINE_REASON_PREFIXES):
        return "machine_code"
    if len(r.split()) <= 2:
        return "too_short"
    for p in parts:
        if p and p.strip() and r.strip().rstrip(".") == p.strip().rstrip("."):
            return "repeats_a_part"
    return ""


def scan(paths):
    totals = Counter()
    flaws = Counter()
    flaw_examples = {}
    part_flaws = Counter()
    silent_examples = []
    per_run = []
    for analysis in sorted(paths):
        try:
            data = json.loads(analysis.read_text())
        except Exception as exc:                      # a half-written run
            print(f"skipped {analysis}: {exc}")
            continue
        counts = Counter()
        for claim in data.get("text_claims") or data.get("claims") or []:
            if claim.get("verdict") != "unsupported":
                continue
            b = bucket(claim)
            counts[b] += 1
            totals[b] += 1
            cc = claim.get("component_check") or {}
            parts = (cc.get("found") or []) + (cc.get("missing") or [])
            f = reason_flaw(str(claim.get("reason") or ""), parts)
            if f:
                flaws[f] += 1
                flaw_examples.setdefault(f, []).append(
                    (str(analysis.parent), claim.get("id"),
                     _one_line(str(claim.get("reason") or ""))[:140]))
            for p in parts:
                if not str(p).strip():
                    part_flaws["blank_part"] += 1
                elif str(p).strip().rstrip(".") == str(claim.get("text") or "").strip().rstrip("."):
                    part_flaws["part_is_whole_sentence"] += 1
            if b == "no_parts_silent" and len(silent_examples) < 25:
                silent_examples.append((str(analysis.parent), claim.get("id"),
                                        (claim.get("text") or "")[:120],
                                        claim.get("method") or "",
                                        bool(claim.get("citation_scope"))))
        if counts:
            per_run.append((str(analysis.parent), counts))
    return totals, per_run, silent_examples, flaws, flaw_examples, part_flaws


def _one_line(s):
    return " ".join(s.split())


def main(argv):
    roots = [Path(a) for a in argv[1:]] or [Path("data")]
    files = []
    for r in roots:
        files.extend(r.rglob("analysis.json"))
    totals, per_run, silent, flaws, flaw_examples, part_flaws = scan(files)
    total = sum(totals.values())
    print(f"analysis files read: {len(files)}")
    print(f"rejected (unsupported) claim cards: {total}")
    for b in BUCKETS:
        n = totals[b]
        share = f"{100.0 * n / total:.1f}%" if total else "-"
        print(f"  {b:<18} {n:>5}  {share}")
    print()
    print("runs holding a card with no part list and no objection:")
    for path, counts in per_run:
        if counts["no_parts_silent"]:
            print(f"  {counts['no_parts_silent']:>4}  {path}")
    print()
    print("examples of the silent class (run, claim id, text, method, scoped?):")
    for row in silent:
        print(f"  {row[0]} {row[1]} method={row[3]!r} scoped={row[4]} :: {row[2]}")
    print()
    print("objections that are recorded but not readable as they stand:")
    for f, n in flaws.most_common():
        share = f"{100.0 * n / total:.1f}%" if total else "-"
        print(f"  {f:<18} {n:>5}  {share}")
        for row in (flaw_examples.get(f) or [])[:6]:
            print(f"      {row[0]} {row[1]} :: {row[2]}")
    print()
    print("part-list flaws:")
    for f, n in part_flaws.most_common():
        print(f"  {f:<24} {n:>5}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
