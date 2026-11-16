"""Card 398 (branch B): one short summary of a decisions file (tuning part) for rounds.md.

Prints, for the main group (in scope) and all checked pieces: wrongly passed, wrongly rejected, left
undecided; then pieces settled per rule and dollars. Usage: roundrow.py DECISIONS
"""
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

records = sibling("records")
score = sibling("score")


def main():
    dec = score.read_decisions(sys.argv[1])
    labels = records.load_labels(final=False)
    ids = [p for p in dec if p in labels]
    g = score.groups(labels, ids)
    for name in ("main group (pieces in scope)", "all checked pieces"):
        r = score.rates(g[name], labels, dec)
        wp, wr, und = r["wrongly_passed"], r["wrongly_rejected"], r["undecided"]
        print(f"{name}: wrongly passed {wp[0]} of {wp[1]}; wrongly rejected {wr[0]} of {wr[1]}; "
              f"undecided {und[0]} of {und[1]} (not checked {r['not_checked']})")
    steps = Counter(d.get("step") for d in dec.values())
    usd = sum(float(d.get("cost_usd") or 0) for d in dec.values())
    print("settled per rule:", dict(steps.most_common()), f"; dollars for these {len(dec)} pieces: {usd:.4f}")
    wrong = Counter(d.get("step") for p, d in dec.items() if p in labels and d.get("answer") == "not_supported"
                    and labels[p]["label"] == "supported")
    print("right pieces wrongly rejected, per rule:", dict(wrong.most_common()))


if __name__ == "__main__":
    main()
