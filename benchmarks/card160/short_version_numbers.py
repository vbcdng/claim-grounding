#!/usr/bin/env python3
"""Stability study (CARD160): every number of the short version, recomputed.

No model calls. Two ways to run it, both from the repository root:

  # read the saved scorer output (the committed table of 2026-10-14)
  ./venv/bin/python3 benchmarks/card160/short_version_numbers.py

  # recount from the run folders themselves (calls interim_table.build, which
  # calls the pre-registered stability_table.py unchanged; writes no file)
  ./venv/bin/python3 benchmarks/card160/short_version_numbers.py --recompute [--root data]

For each number the short version states, it prints the number, the field of
the scorer output it comes from, the value the short version states, and OK or
DIFFERENT. Exit status 1 when any number differs.

The short version's last sentence ("16 ... in 7,936") is not from this study:
it is an earlier measurement (made 2026-09-23) and is printed as such,
never as recomputed.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SAVED = os.path.join(HERE, "results", "interim_table_2026-10-14.json")


def arm(payload, judge, run, baseline):
    for a in payload["judges"][judge]["arms"]:
        if a.get("run") == run and a.get("baseline") == baseline and a.get("compared") is not None:
            return a
    raise KeyError(f"{judge}: no arm {run} against {baseline}")


def numbers(payload):
    """Return (label, field, value, stated) for every number of the short version
    and of the line under it that says where each number comes from."""
    p = "paid"
    r1, r2, r3 = "card160paidr1", "card160paidr2", "card160paidr3"
    rows = []

    def add(label, judge, run, base, key, stated):
        a = arm(payload, judge, run, base)
        rows.append((label, f"judges.{judge}: {run} against {base}: {key}", a[key], stated))

    add("texts' judged claims compared (\"of 85\")", p, r2, r1, "compared", 85)
    add("plain repeat r2 against r1: changed (\"2 of 85\")", p, r2, r1, "changed", 2)
    add("plain repeat r3 against r1: changed", p, r3, r1, "changed", 2)
    add("plain repeat r3 against r2: changed", p, r3, r2, "changed", 2)
    add("second judge: Sonnet r1 against paid r1: changed (\"14\")", "sonnet",
        "card160sonnetr1", r1, "changed", 14)
    add("second judge: Haiku r1 against paid r1: changed (15, line under)", "haiku",
        "card160haikur1", r1, "changed", 15)
    add("reworded instruction: changed (\"5\")", p, "card160paidprompt", r1, "changed", 5)
    add("evidence reordered: changed (\"1\")", p, "card160paidshuffle", r1, "changed", 1)
    add("confident-writer note: changed (\"4\")", p, "card160paidbias", r1, "changed", 4)
    add("doubting-reviewer note: changed (6, line under)", p, "card160paiddoubt", r1, "changed", 6)
    add("reworded claims: changed (\"4 of 85\")", p, "card160paidreworded", r1, "changed", 4)
    add("reworded claims: compared (\"of 85\")", p, "card160paidreworded", r1, "compared", 85)
    neg = arm(payload, p, "card160paidnegated", r1)
    rows.append(("claims turned into their opposite (\"53\")",
                 "judges.paid: card160paidnegated: negated_rows", neg["negated_rows"], 53))
    rows.append(("opposite claims caught (\"44\")",
                 "judges.paid: card160paidnegated: negation_caught", neg["negation_caught"], 44))
    add("paid r1 accuracy (\"53 of 56\")", p, r1, r1, "correct", 53)
    add("hand-checked rows (\"of 56\")", p, r1, r1, "hard_rows", 56)
    add("paid r2 accuracy (54, line under)", p, r2, r1, "correct", 54)
    add("paid r3 accuracy (52, line under)", p, r3, r1, "correct", 52)
    for tag in ("card160freer1",):
        add("free r1 accuracy (53, line under; part 2)", "free", tag, tag, "correct", 53)
    add("free r2 against r1: changed (0, line under; part 2)", "free",
        "card160freer2", "card160freer1", "changed", 0)
    add("free r3 against r1: changed (0, line under; part 2)", "free",
        "card160freer3", "card160freer1", "changed", 0)
    add("free r3 against r2: changed (0, line under; part 2)", "free",
        "card160freer3", "card160freer2", "changed", 0)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json", nargs="?", default=SAVED,
                    help="scorer output to read (default: the committed table of 2026-10-14)")
    ap.add_argument("--recompute", action="store_true",
                    help="recount from the run folders instead of reading a saved file")
    ap.add_argument("--root", default=os.path.join(REPO, "data"))
    args = ap.parse_args(argv)
    if args.recompute:
        sys.path.insert(0, HERE)
        import interim_table              # noqa: E402
        _, payload = interim_table.build(args.root, "recompute")
        source = f"recounted now from the run folders under {args.root}"
    else:
        with open(args.json, encoding="utf-8") as f:
            payload = json.load(f)
        source = f"read from {os.path.relpath(args.json, REPO)}"
    print(f"Short version numbers, {source}")
    bad = 0
    for label, field, value, stated in numbers(payload):
        ok = value == stated
        bad += not ok
        print(f"{'OK       ' if ok else 'DIFFERENT'} {label}: {value} (stated {stated}); field {field}")
    a = arm(payload, "paid", "card160paidr2", "card160paidr1")
    print(f"share: 2 of {a['compared']} = {100 * 2 / a['compared']:.1f} in every hundred (stated 2.4)")
    print("NOT RECOMPUTED HERE: '16 in 7,936' is an earlier measurement (made 2026-09-23), "
          "not part of this study; the study plan's section 11 describes it.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
