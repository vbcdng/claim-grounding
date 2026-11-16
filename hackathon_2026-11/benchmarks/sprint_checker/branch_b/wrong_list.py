"""Card 398 (branch B): list the right pieces a decisions file rejects, with the rule and each step's first
answer, so a round's remaining wrong rejections can be read. Tuning part only. Usage: wrong_list.py DECISIONS"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

records = sibling("records")
score = sibling("score")
STEPS = {"g": "gemma", "h": "haiku", "s": "sonnet", "j": "jev"}


def first_answers(step):
    out = {}
    for line in (HERE / "answers" / f"{step}.jsonl").read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        if d.get("repeat", 0) == 0:
            out[d["piece_id"]] = d.get("jev_score") if step == "jev" else d.get("answer")
    return out


def main():
    dec = score.read_decisions(sys.argv[1])
    labels = records.load_labels(final=False)
    ans = {k: first_answers(v) for k, v in STEPS.items()}
    for p, d in sorted(dec.items()):
        if p in labels and d.get("answer") == "not_supported" and labels[p]["label"] == "supported":
            print(p, d.get("step"), {k: ans[k].get(p) for k in STEPS})


if __name__ == "__main__":
    main()
