#!/usr/bin/env python3
"""Compare the original and repaired Citation-Integrity runs — task #32, round 3.

Reads four finished runs (each batch judged twice: as it was, and with its
sibling citations restored) and reports what the repair changed. Pure counting:
no API calls, no AI model, no cost. Every number here can be recounted from the
`analysis.json` files it names.

WHAT IT REPORTS
---------------
1. Accuracy before and after, always split into rows that ask a FAIR question
   and rows that do not. Pooling the two reversed a recommendation once already
   (2026-08-06), so the split is not optional.
2. Every row whose verdict changed, with the direction and whether the change
   agrees with the answer key.
3. The count the author needs for the open decision: rows where the key says the
   citation under test is faulty, the tool now says supported, and the support
   comes from a SIBLING paper rather than the paper under test. On those rows
   the tool is right about the sentence and "wrong" about the row, which is the
   whole of the disagreement between judging a sentence and judging one citation.

Usage:
    python3 benchmarks/ci_sibling_compare.py [--prefix run32_]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from wice_bench import _tool_bucket                      # noqa: E402
from citation_integrity_bench import (strict_side, grounding_side,  # noqa: E402
                                      own_paper_side)

CI_DIR = os.path.join(ROOT, "data", "citation_integrity")
BATCHES = ("pilot100", "fresh50")
PASS_BUCKETS = {"supported"}


def load_run(path, expect_batch):
    """Load a run, refusing one that judged a different set of rows.

    A repaired run is started from a copy of its own baseline so that untouched
    rows keep their verdict without being asked again. That means the folder
    holds the BASELINE's analysis.json until the repaired run overwrites it — so
    an interrupted run leaves a file that looks like a finished result and is
    not one. The guard is the recorded input path: it must name the batch this
    arm was supposed to judge.
    """
    with open(os.path.join(path, "analysis.json"), encoding="utf-8") as f:
        analysis = json.load(f)
    used = (analysis.get("metadata") or {}).get("text_file") or ""
    if os.path.basename(os.path.dirname(used)) != expect_batch:
        raise SystemExit(
            f"{path} holds results for '{os.path.basename(os.path.dirname(used))}', "
            f"not '{expect_batch}' — that run was interrupted before it wrote its "
            f"own results, so there is nothing here to compare yet.")
    by_key = {}
    for claim in analysis.get("text_claims", []):
        for marker in claim.get("markers") or []:
            by_key.setdefault(marker, claim)
    return analysis, by_key


def side_of(claim):
    """pass = the tool is content with the citation; flag = it complains.

    This is the ROW-LEVEL reading, and it lumps together two very different
    complaints: "the cited paper does not support this" and "the sentence has a
    part no cited paper covers". The second one is exactly what a deleted
    sibling citation manufactures, so a repaired run must report both readings.
    """
    return "pass" if _tool_bucket(claim) == "supported" else "flag"


# The per-source reading is defined ONCE, in citation_integrity_bench, and
# imported here — since 2026-08-10 it is the reading the benchmark is scored
# on (author ruling, task #32 "Option B"), so the two files must agree on it.
own_side = own_paper_side


def why_flagged(claim):
    """Which of the two complaints produced a flag, in plain words."""
    if claim.get("verdict") != "supported":
        return "the cited paper does not support the sentence"
    bits = []
    if claim.get("proof_state") == "partial":
        bits.append("a part of the sentence has no proof shown")
    if (claim.get("covering") or {}).get("uncovered"):
        bits.append("named parts left uncovered")
    if claim.get("partial_support"):
        bits.append("a part is in none of the cited papers")
    return " and ".join(bits) or "no complaint"


def supporting_sources(claim):
    """Which source keys the tool actually rested its 'supported' on."""
    out = []
    for ev in (claim.get("evidences") or ([claim["evidence"]] if claim.get("evidence") else [])):
        if not isinstance(ev, dict):
            continue
        if ev.get("supported") is False:
            continue
        title = ev.get("source_title") or ev.get("paper_id")
        if title:
            out.append(str(title))
    return out


def failed_calls(analysis):
    """Verdicts produced by a refused or failed request, not by a judgment.

    A failed request is recorded as an unsupported verdict with a tell-tale
    reason (see task #37), so a run with an outage can look like a normal run.
    Any number here above zero makes the arm unusable.
    """
    n = 0
    for claim in analysis.get("text_claims", []):
        blob = json.dumps(claim.get("evidence") or {}) + str(claim.get("reason") or "")
        if "no LLM response" in blob or "no_llm_response" in blob:
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="run32_")
    ap.add_argument("--out", default=os.path.join(CI_DIR, "sibling_round3_comparison.json"))
    args = ap.parse_args()

    with open(os.path.join(CI_DIR, "sibling_repair_report.json"), encoding="utf-8") as f:
        repair = json.load(f)["rows"]

    rows, notes = [], []
    for batch in BATCHES:
        with open(os.path.join(CI_DIR, f"batch_dev_{batch}", "ci_ground_truth.json"),
                  encoding="utf-8") as f:
            gt = json.load(f)["claims"]
        old_a, old = load_run(os.path.join(CI_DIR, f"{args.prefix}{batch}_original"),
                              f"batch_dev_{batch}")
        new_a, new = load_run(os.path.join(CI_DIR, f"{args.prefix}{batch}_repaired"),
                              f"batch_dev_{batch}_repaired")
        for label, analysis in (("original", old_a), ("repaired", new_a)):
            bad = failed_calls(analysis)
            if bad:
                notes.append(f"{batch} {label}: {bad} verdict(s) came from a failed "
                             f"request, not a judgment — this arm is contaminated")

        for key, meta in gt.items():
            oc, nc = old.get(key), new.get(key)
            if oc is None or nc is None:
                notes.append(f"{batch}:{key} is missing from one of the runs")
                continue
            rep = repair.get(f"{batch}:{key}", {})
            sup = supporting_sources(nc)
            rows.append({
                "row": f"{batch}:{key}", "label": meta["label"],
                "strict": strict_side(meta["label"]),
                "grounding": grounding_side(meta["label"]),
                "fair": bool(rep.get("fair_question")),
                "repaired": rep.get("status") == "repaired",
                "siblings_added": rep.get("siblings_added", 0),
                "before": side_of(oc), "after": side_of(nc),
                "source_before": own_side(oc, key), "source_after": own_side(nc, key),
                "why_flagged_before": why_flagged(oc),
                "supported_by": sup,
                "supported_only_by_sibling": bool(sup) and all("_s" in s for s in sup),
            })

    def tally(subset, when):
        """Rows the tool got right, judged against the key's own line."""
        n = ok = 0
        for r in subset:
            if r["grounding"] is None:
                continue                      # label outside the tally by design
            if r.get(when) is None:
                continue                      # source not listed in this reading
            n += 1
            ok += (r[when] == r["grounding"])
        return ok, n

    fair = [r for r in rows if r["fair"]]
    unfair = [r for r in rows if not r["fair"]]
    changed = [r for r in rows if r["before"] != r["after"]]

    print(f"rows compared: {len(rows)}   (fair {len(fair)} / still unfair {len(unfair)})")
    print("\n--- agreement with the answer key, before and after ---")
    print("Two readings. WHOLE SENTENCE = did the tool end up content with the")
    print("sentence, counting every complaint. THAT ONE PAPER = what the tool")
    print("concluded about the single paper the answer key is actually about.")
    print(f"\n{'rows':<24}{'whole sentence':>26}{'that one paper':>26}")
    print(f"{'':<24}{'before':>13}{'after':>13}{'before':>13}{'after':>13}")
    for name, subset in (("all rows", rows), ("fair rows", fair),
                         ("still-unfair rows", unfair)):
        cells = []
        for when in ("before", "after", "source_before", "source_after"):
            ok, n = tally(subset, when)
            cells.append(f"{ok}/{n}")
        print(f"{name:<24}" + "".join(f"{c:>13}" for c in cells))

    src_moved = [r for r in rows if r["source_before"] and r["source_after"]
                 and r["source_before"] != r["source_after"]]
    rejudged = [r for r in rows if r["siblings_added"] and r["grounding"]]
    print(f"\nOf the {len(rejudged)} scored rows that gained a sibling and were judged")
    print(f"again, {len(src_moved)} changed the tool's verdict about the paper under test.")
    print("That is the noise floor: asking the same model the same question twice.")
    for r in src_moved:
        print(f"  {r['row']:<22} key {r['label']:<18} "
              f"{r['source_before']} -> {r['source_after']}")

    print(f"\n--- verdicts that changed: {len(changed)} ---")
    kinds = collections.Counter()
    for r in changed:
        direction = f"{r['before']} -> {r['after']}"
        agrees = ("now agrees with the key" if r["after"] == r["grounding"]
                  else "now disagrees with the key" if r["grounding"] else "row not scored")
        kinds[f"{direction}, {agrees}"] += 1
    for k, v in kinds.most_common():
        print(f"  {v:3d}  {k}")

    decision = [r for r in changed
                if r["grounding"] == "flag" and r["after"] == "pass"
                and r["source_after"] == "flag"]
    print(f"\n--- the open decision: {len(decision)} row(s) ---")
    print("Rows where the key says the citation under test is faulty, the tool STILL")
    print("says that paper does not support the sentence, and the sentence passes")
    print("anyway because a sibling covers it. The tool is right on both counts; only")
    print("the whole-sentence reading disagrees with a key written about one citation.")
    for r in decision:
        print(f"  {r['row']:<22} key {r['label']:<18} proof from {r['supported_by'] or 'the cited papers read together'}")

    accidental = [r for r in changed
                  if r["grounding"] == "flag" and r["after"] == "pass"
                  and r["source_after"] == "pass"]
    print(f"\n--- credit the baseline did not earn: {len(accidental)} row(s) ---")
    print("Here the tool passed the paper under test both before and after — it never")
    print("caught the fault the key describes. The baseline scored these correct only")
    print("because a deleted sibling made it complain about a missing part instead.")
    for r in accidental:
        print(f"  {r['row']:<22} key {r['label']:<18} flagged before because: {r['why_flagged_before']}")

    if notes:
        print("\n--- warnings ---")
        for n in notes:
            print("  " + n)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "changed": changed, "decision_rows": decision,
                   "warnings": notes}, f, indent=1)
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
