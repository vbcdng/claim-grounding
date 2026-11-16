"""Card 398 (branch B): Fable's cheapest test of change 3. Among the pieces a decisions file rejects
(tuning part), count Opus's first answer (neutral question) separately for right pieces (wrong
rejections) and wrong pieces (correct rejections), and list the right ones.
Usage: opus_look.py DECISIONS [OPUS_ANSWERS]"""
import json
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
    path = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "answers" / "opus_neutral.jsonl"
    opus = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        if d.get("repeat", 0) == 0:
            opus[d["piece_id"]] = "failed" if d.get("failed") else d.get("answer")
    labels = records.load_labels(final=False)
    by = {"supported": Counter(), "not_supported": Counter()}
    for p, d in sorted(dec.items()):
        if p not in labels or d.get("answer") != "not_supported":
            continue
        lab = labels[p]["label"]
        by[lab][opus.get(p, "missing")] += 1
        if lab == "supported":
            print("wrongly rejected:", p, d.get("step"), "opus:", opus.get(p, "missing"))
    print("right pieces rejected, Opus answers:", dict(by["supported"]))
    print("wrong pieces rejected, Opus answers:", dict(by["not_supported"]))


if __name__ == "__main__":
    main()
