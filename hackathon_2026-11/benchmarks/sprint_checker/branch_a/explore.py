"""Card 397 (branch A): score one or more rule files on the tuning part, five-fold when the rule
has a fitted line, and print one row per rule file in the layout of rounds.md. Tuning part only;
never --final. The answers each rule file names are what it is scored on.

  explore.py rules/chain_v1.json [more rule files]
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))

from sprint_shared_load import sibling  # noqa: E402

combine = sibling("combine")
records = sibling("records")
score = sibling("score")


def decisions(rule_path):
    rule = json.loads(Path(rule_path).read_text(encoding="utf-8"))
    chain = combine.Chain(rule, Path(rule_path).parent)
    if rule.get("fit"):
        dec, chosen = combine.five_fold(chain)
    else:
        dec, chosen = {p: chain.decide(p) for p in chain.piece_ids()}, []
    return rule, dec, chosen


def row(rule_path):
    rule, dec, chosen = decisions(rule_path)
    labels = records.load_labels()
    g = score.groups(labels, sorted(labels))
    out = {"rule": rule.get("name"), "fitted": [v for _, v in chosen]}
    for name, key in (("main group (pieces in scope)", "main"), ("all checked pieces", "all")):
        r = score.rates(g[name], labels, dec)
        out[key] = {"wrongly_passed": r["wrongly_passed"][:2], "wrongly_rejected": r["wrongly_rejected"][:2],
                    "undecided": r["undecided"][:2]}
    ids = g["all checked pieces"]
    out["settled_by"] = dict(Counter(dec[p]["step"] for p in ids if p in dec))
    # per rule: how many of the pieces it settled are wrong pieces (label not supported)
    out["wrong_among"] = {k: sum(1 for p in ids if dec.get(p, {}).get("step") == k
                                 and labels[p]["label"] == "not_supported") for k in out["settled_by"]}
    out["usd_all"] = round(sum(dec[p].get("cost_usd") or 0 for p in ids if p in dec), 4)
    wp = [p for p in ids if labels[p]["label"] == "not_supported" and dec.get(p, {}).get("answer") == "supported"]
    out["wrongly_passed_by"] = dict(Counter(dec[p]["step"] for p in wp))
    return out, dec


def fmt(o):
    m, a = o["main"], o["all"]
    f = lambda t: f"{t[0]} of {t[1]}"  # noqa: E731
    return (f"{o['rule']}: main wrongly passed {f(m['wrongly_passed'])}, wrongly rejected "
            f"{f(m['wrongly_rejected'])}, undecided {f(m['undecided'])} | all: {f(a['wrongly_passed'])}, "
            f"{f(a['wrongly_rejected'])}, {f(a['undecided'])} | settled by {o['settled_by']} | wrong pieces "
            f"among them {o['wrong_among']} | wrongly passed by "
            f"{o['wrongly_passed_by']} | fitted {o['fitted']} | ${o['usd_all']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("rules", nargs="+")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    for p in a.rules:
        o, _ = row(p)
        print(json.dumps(o) if a.json else fmt(o))
    return 0


if __name__ == "__main__":
    sys.exit(main())
