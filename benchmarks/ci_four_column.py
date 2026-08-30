#!/usr/bin/env python3
"""Four-column comparison for the Citation-Integrity pilot: benchmark label vs
blind human-style reader vs one or more tool runs (old stack, new stack(s)).

Pure — no API, no network. Every definition of "pass" is imported from the
benchmark harnesses so the columns cannot drift apart:
`strict_side` / `grounding_side` for the published label, `_tool_bucket` +
`_collapse` for a tool run, and the blind vote's own defect taxonomy for the
reader (a provenance-only defect passes under the grounding mapping, mirroring
how INDIRECT labels are treated).

    python3 benchmarks/ci_four_column.py \
        --batch data/citation_integrity/batch_dev_pilot100 \
        --blind data/citation_integrity/c0_blind_sonnet.json \
        --run old=data/citation_integrity/batch_dev_pilot100_run \
        --run new=data/citation_integrity/batch_dev_pilot100_run_<model> \
        --markdown docs/ci_pilot_table.md

Any number of --run NAME=DIR pairs is accepted (the author may pick several
replacement models). Each run must have its OWN output dir: pointing two models
at one dir would hand the second model the first one's cached verdicts.

Three error counts are printed separately and never averaged, per the standing
rule: false-support on flagged rows, false-flag on ACCURATE rows, and rows the
run failed to judge at all. The final section lists the Phase D candidates —
rows where the blind reader AND every run agree against the published label,
which is where a label is most likely to be the thing that is wrong.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from citation_integrity_bench import (  # noqa: E402
    strict_side, grounding_side, _tool_bucket, _collapse,
)
from ci_blind_compare import sides as blind_sides  # noqa: E402
from ci_batch_ids import batch_tag, qualify  # noqa: E402


def load_run(path):
    """Output dir or analysis.json -> {marker key: pass/flag}."""
    if os.path.isdir(path):
        path = os.path.join(path, "analysis.json")
    analysis = json.load(open(path))
    out = {}
    for c in analysis.get("text_claims", []):
        for key in c.get("markers") or []:
            out.setdefault(key, _collapse(_tool_bucket(c)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True)
    ap.add_argument("--blind", required=True)
    ap.add_argument("--run", action="append", default=[],
                    metavar="NAME=DIR", help="repeatable; one per judge stack")
    ap.add_argument("--markdown", help="also write the per-row table here")
    a = ap.parse_args()

    gt = json.load(open(os.path.join(a.batch, "ci_ground_truth.json")))["claims"]
    blind = json.load(open(a.blind))["votes"]

    runs = {}
    for spec in a.run:
        if "=" not in spec:
            sys.exit(f"--run wants NAME=DIR, got {spec!r}")
        name, path = spec.split("=", 1)
        runs[name] = load_run(path)
    if len(set(os.path.realpath(s.split('=', 1)[1]) for s in a.run)) != len(a.run):
        sys.exit("two runs point at the same directory — a second judge would "
                 "inherit the first one's cached verdicts")

    columns = ["blind"] + list(runs)
    ids = sorted(gt)
    tag = batch_tag(a.batch)
    print(f"batch {tag} ({len(ids)} rows) — row ids below are written "
          f"{tag}:cidevNNNN, because every batch numbers its rows from cidev0001 "
          f"and the same number means a different row elsewhere")

    def column_side(col, cid, mapping):
        if col == "blind":
            v = blind.get(cid)
            if not v:
                return "MISSING"
            return blind_sides(v)[0 if mapping == "strict" else 1]
        return runs[col].get(cid, "MISSING")

    for mapping in ("strict", "grounding"):
        side_of = strict_side if mapping == "strict" else grounding_side
        print(f"\n=== {mapping} mapping ===")
        head = f"{'column':<14}{'agrees':>8}{'false-support':>15}{'false-flag':>12}{'unjudged':>10}"
        print(head)
        for col in columns:
            agree = fs = ff = miss = 0
            for cid in ids:
                want = side_of(gt[cid]["label"])
                got = column_side(col, cid, mapping)
                if got == "MISSING":
                    miss += 1
                elif got == want:
                    agree += 1
                elif want == "flag":
                    fs += 1      # benchmark says defective, column passed it
                else:
                    ff += 1      # benchmark says ACCURATE, column flagged it
            n = len(ids)
            print(f"{col:<14}{agree:>4}/{n:<3}{fs:>15}{ff:>12}{miss:>10}")

        # agreement with the blind reader, reported SEPARATELY (never averaged
        # with the line above): it is the same rows scored against a second,
        # independent reading of the sources rather than against the labels.
        print("  agreement with the blind reader (separate view, not a correction):")
        for col in runs:
            agree = comparable = 0
            for cid in ids:
                b = column_side("blind", cid, mapping)
                t = column_side(col, cid, mapping)
                if "MISSING" in (b, t):
                    continue
                comparable += 1
                agree += (b == t)
            print(f"    {col:<12} {agree}/{comparable}")

    print("\n=== per-row (strict mapping) ===")
    lines = ["| id | published label | " + " | ".join(columns) + " |",
             "|---|---|" + "---|" * len(columns)]
    for cid in ids:
        cells = [column_side(c, cid, "strict") for c in columns]
        lines.append(f"| {qualify(tag, cid)} | {gt[cid]['label']} | "
                     + " | ".join(cells) + " |")
    print("\n".join(lines[:4]) + f"\n  ... {len(ids)} rows"
          + (f"; full table -> {a.markdown}" if a.markdown else ""))
    if a.markdown:
        open(a.markdown, "w").write("\n".join(lines) + "\n")

    print("\n=== Phase D candidates: blind reader AND every run disagree with "
          "the published label (strict) ===")
    n_cand = 0
    for cid in ids:
        want = strict_side(gt[cid]["label"])
        got = [column_side(c, cid, "strict") for c in columns]
        if got and all(g != "MISSING" and g != want for g in got):
            n_cand += 1
            print(f"  {qualify(tag, cid):<22} label={gt[cid]['label']:<20} "
                  f"everyone said {got[0]}")
    if not n_cand:
        print("  (none)")
    print(f"\n{n_cand} candidate(s) for the Opus/Fable panel. Model votes never "
          "move a label; the author rules disputes.")


if __name__ == "__main__":
    main()
