#!/usr/bin/env python3
"""Task #31 hosting A/B/A — compare the three pilot100 legs row by row.

The question: is the score gap between two HOSTS bigger than the run-to-run
spread of the SAME model on the SAME host? Totals alone cannot answer it (two
runs give a weak variance estimate), so this counts per-row verdict flips:

  within-host  = gglA vs gglB   (same model, same host, two runs)
  between-host = gglA vs openrouter, gglB vs openrouter

Row identity and the pass/flag reading come from the benchmark's own scorer
(`evaluate()` in citation_integrity_bench), so this script and the published
scores cannot drift apart. Uses the SCORED reading (`own`) — the tool's verdict
for the one cited paper the answer key is about (author ruling 2026-08-10).

No API calls. Usage:
  venv/bin/python3 benchmarks/task31_hosting_compare.py \
      --gt data/citation_integrity/batch_dev_pilot100/ci_ground_truth.json \
      --leg gglA=<run dir> --leg gglB=<run dir> --leg openrouter=<run dir>
"""
import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from citation_integrity_bench import evaluate  # noqa: E402


def load_leg(run_dir, gt):
    """-> (rows_by_ci_id, usage_summary). rows keep the scored `own` reading."""
    with open(os.path.join(run_dir, "analysis.json"), encoding="utf-8") as f:
        analysis = json.load(f)
    res = evaluate(analysis, gt)
    # A claim the model never actually answered (task #37) still carries a
    # verdict field, and that verdict is an artifact of the outage, not a
    # judgment. Mark those rows so the comparison drops them instead of
    # counting an outage as a disagreement between hosts.
    unanswered_keys = set()
    for c in analysis.get("text_claims", []):
        if c.get("judge_error"):
            unanswered_keys.update(c.get("markers") or [])
    rows = {}
    for r in res["rows"]:
        if r["key"] in unanswered_keys:
            r["own"] = r["tool"] = r["adj"] = "UNANSWERED"
        rows[r.get("ci_id") or r["key"]] = r
    md = analysis.get("metadata", {})
    usage = md.get("llm_usage", {}) or {}
    calls = prompt_t = completion_t = 0
    cost = 0.0
    for per_model in usage.values():
        if not isinstance(per_model, dict):
            continue
        calls += per_model.get("calls") or 0
        prompt_t += per_model.get("prompt_tokens") or 0
        completion_t += per_model.get("completion_tokens") or 0
        cost += per_model.get("cost_usd") or 0.0
    # Task #37 (outage honesty): a claim the model never actually answered
    # carries judge_error / checks_failed instead of passing as a real verdict.
    # A leg with any of these is contaminated and its numbers must not be quoted.
    unanswered = sum(1 for c in analysis.get("text_claims", [])
                     if c.get("judge_error") or c.get("checks_failed"))
    return rows, {
        "model": md.get("model"),
        "minutes": round((md.get("processing_time_seconds") or 0) / 60),
        "calls": calls or None,
        "input_tokens": prompt_t or None,
        "output_tokens": completion_t or None,
        "cost_usd": round(cost, 4),
        "unanswered": unanswered,
    }


def agreement(rows, field="own"):
    """How often the leg agrees with the answer key, on rows it judged."""
    ok = n = 0
    for r in rows.values():
        tool = r.get(field)
        if tool in (None, "MISSING", "NOT_LISTED", "UNANSWERED"):
            continue
        n += 1
        ok += (tool == r["label_side"])
    return ok, n


def side(label):
    """The answer key's two-label side for a fine label."""
    from citation_integrity_bench import grounding_side
    return grounding_side(label)


def flips(a, b, field="own"):
    """Rows whose verdict differs between two legs, plus the unusable ones."""
    changed, same, unusable = [], 0, []
    for cid in sorted(set(a) | set(b)):
        ra, rb = a.get(cid), b.get(cid)
        if not ra or not rb:
            unusable.append((cid, "row absent in one leg"))
            continue
        va, vb = ra.get(field), rb.get(field)
        if va in (None, "MISSING", "NOT_LISTED", "UNANSWERED") or vb in (None, "MISSING", "NOT_LISTED", "UNANSWERED"):
            unusable.append((cid, f"{va} / {vb}"))
            continue
        if va == vb:
            same += 1
        else:
            changed.append((cid, va, vb, ra.get("label")))
    return changed, same, unusable


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", required=True)
    ap.add_argument("--leg", action="append", required=True,
                    help="name=run_dir (repeat; expects gglA, gglB, openrouter)")
    ap.add_argument("--field", default="own", choices=["own", "tool", "adj"],
                    help="own = the scored per-paper reading (default)")
    a = ap.parse_args()

    with open(a.gt, encoding="utf-8") as f:
        gt = json.load(f)

    legs, usage = {}, {}
    for spec in a.leg:
        name, _, run_dir = spec.partition("=")
        if not os.path.exists(os.path.join(run_dir, "analysis.json")):
            print(f"SKIP {name}: no analysis.json yet in {run_dir}")
            continue
        legs[name], usage[name] = load_leg(run_dir, gt)

    for rows in legs.values():
        for r in rows.values():
            r["label_side"] = side(r["label"])

    print("=== per-leg totals (scored reading: %s) ===" % a.field)
    print(f"{'leg':<12} {'agreed/judged':>14} {'model':<34} {'min':>5} "
          f"{'calls':>7} {'in_tok':>10} {'out_tok':>8} {'cost$':>7} {'unans':>6}")
    for name, rows in legs.items():
        ok, n = agreement(rows, a.field)
        u = usage[name]
        print(f"{name:<12} {f'{ok}/{n}':>14} {str(u['model']):<34} "
              f"{u['minutes']:>5} {str(u['calls']):>7} {str(u['input_tokens']):>10} "
              f"{str(u['output_tokens']):>8} {u['cost_usd']:>7} {u['unanswered']:>6}")
        if u["unanswered"]:
            print(f"  WARNING {name}: {u['unanswered']} claims the model never "
                  f"answered (judge_error / checks_failed). This leg is "
                  f"contaminated — do not quote its numbers until they are re-asked.")

    # Group the legs by host so any number of runs per host works: two Google
    # runs and two OpenRouter runs give two within-host pairs (one per host)
    # and four between-host pairs. A leg's host is the part of its name before
    # the trailing run letter/number, e.g. gglA and gglB are both "ggl".
    def host_of(name):
        return name.rstrip("0123456789ABCDEFGH") or name
    names = list(legs)
    pairs = []
    for i, x in enumerate(names):
        for y in names[i + 1:]:
            same = host_of(x) == host_of(y)
            pairs.append((x, y, f"within-host, {host_of(x)} (same model, same host)"
                          if same else "between-host"))
    print("\n=== per-row verdict flips ===")
    summary = {}
    for x, y, kind in pairs:
        if x not in legs or y not in legs:
            continue
        changed, same, unusable = flips(legs[x], legs[y], a.field)
        summary[(x, y)] = len(changed)
        print(f"\n{x} vs {y} — {kind}: {len(changed)} of {len(changed)+same} "
              f"comparable rows changed"
              + (f" ({len(unusable)} not comparable)" if unusable else ""))
        for cid, va, vb, lab in changed:
            print(f"    {cid:<26} {va:>4} -> {vb:<4}  key={lab}")

    withins = [v for k, v in summary.items() if host_of(k[0]) == host_of(k[1])]
    betweens = [v for k, v in summary.items() if host_of(k[0]) != host_of(k[1])]
    if withins and betweens:
        print("\n=== the answer ===")
        print(f"within-host flips:  {', '.join(str(v) for v in withins)}")
        print(f"between-host flips: {', '.join(str(b) for b in betweens)}")
        w = max(withins)
        if min(betweens) > 2 * max(w, 1):
            print("READING: between-host disagreement is clearly larger than the "
                  "same-host run-to-run spread — hosting matters.")
        elif max(betweens) <= w:
            print("READING: between-host disagreement is no larger than the "
                  "same-host spread — the earlier gap was ordinary variation.")
        else:
            print("READING: between-host disagreement is in the same range as the "
                  "same-host spread — no clear hosting effect at this sample size.")


if __name__ == "__main__":
    main()
