"""Card 397 (branch A): pieces settled per rule step in a decisions file (tuning part, all 150), with
how many of them are wrong by the tuning labels (records.load_labels), for the rounds.md column.

  count_steps.py <decisions.jsonl>
"""
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

records = sibling("records")


def main(argv):
    labels = records.load_labels()
    n, wrong = Counter(), Counter()
    for line in Path(argv[0]).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if d["piece_id"] not in labels:
            continue
        key = (d["step"], d["answer"])
        n[key] += 1
        lab = labels[d["piece_id"]]["label"]
        if d["answer"] in ("supported", "not_supported") and d["answer"] != lab:
            wrong[key] += 1
        if d["answer"] not in ("supported", "not_supported") and lab != "supported":
            wrong[key] += 1  # for a hold: pieces rightly held back (labelled wrong)
    for (step, ans), k in sorted(n.items(), key=lambda x: -x[1]):
        what = "wrong" if ans in ("supported", "not_supported") else "labelled not supported"
        print(f"{step} -> {ans}: {k} ({wrong[(step, ans)]} {what})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
