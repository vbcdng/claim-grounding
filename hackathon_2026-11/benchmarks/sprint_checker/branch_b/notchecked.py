"""Card 398 (branch B): the pieces a decisions file leaves "not_checked" (a failed or missing call on a
step the chain reads), with each step's first answer, Jev's score and the tuning label.
Usage: notchecked.py DECISIONS [STEP=answers.jsonl ...] (default steps: g, h, j, s of round 5)"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

records = sibling("records")
score = sibling("score")
DEFAULT = {"g": "gemma", "h": "haiku", "j": "jev", "s": "sonnet"}


def first(name):
    out = {}
    for line in (HERE / "answers" / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        if d.get("repeat", 0) == 0:
            out[d["piece_id"]] = d
    return out


def show(d):
    if d is None:
        return "missing"
    if d.get("failed") or d.get("answer") is None and d.get("jev_score") is None:
        return "FAILED"
    return str(d.get("jev_score")) if d.get("jev_score") is not None else d["answer"]


def main():
    dec = score.read_decisions(sys.argv[1])
    steps = dict(a.split("=", 1) for a in sys.argv[2:]) or DEFAULT
    ans = {k: first(v) for k, v in steps.items()}
    labels = records.load_labels(final=False)
    rows = [p for p, d in sorted(dec.items()) if d.get("answer") == "not_checked" and p in labels]
    for p in rows:
        print(p, labels[p]["label"], " ".join(f"{k}:{show(ans[k].get(p))}" for k in steps))
    print(f"not_checked tuning pieces: {len(rows)}")


if __name__ == "__main__":
    main()
