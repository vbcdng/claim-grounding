"""Card 398 (branch B): Fable's test of change 2. For the rejections of a decisions file (tuning part),
count Haiku's kind_v1 answers (A to D) on right pieces wrongly rejected and on wrong pieces rightly
rejected, and over all 150 pieces. Labels only through records.load_labels. Usage: kindtable.py DECISIONS"""
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
sys.path.insert(0, str(HERE))
from sprint_shared_load import sibling  # noqa: E402
import kind  # noqa: E402

records = sibling("records")
score = sibling("score")


def main():
    dec = score.read_decisions(sys.argv[1])
    labels = records.load_labels(final=False)
    k = kind.load_kinds(HERE / "answers" / "haiku_kind_v1.jsonl")
    right, wrong = [], []
    for p, d in dec.items():
        if p in labels and d.get("answer") == "not_supported":
            (right if labels[p]["label"] == "supported" else wrong).append(p)
    print("right pieces wrongly rejected:", len(right), dict(Counter(k.get(p) for p in right)))
    for p in sorted(right):
        print("   ", p, k.get(p))
    print("wrong pieces rightly rejected:", len(wrong), dict(Counter(k.get(p) for p in wrong)))
    lab = Counter((labels[p]["label"], k.get(p)) for p in labels)
    print("all tuning pieces (label, kind):", dict(sorted(lab.items(), key=str)))


if __name__ == "__main__":
    main()
