"""Card 398 (branch B): pieces whose decision differs between two decisions files, with the tuning label.
Usage: diffdec.py OLD_DECISIONS NEW_DECISIONS"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

records = sibling("records")
score = sibling("score")


def main():
    a, b = score.read_decisions(sys.argv[1]), score.read_decisions(sys.argv[2])
    labels = records.load_labels(final=False)
    n = 0
    for p in sorted(set(a) & set(b) & set(labels)):
        if a[p].get("answer") != b[p].get("answer"):
            n += 1
            print(p, "label:", labels[p]["label"], "|", a[p].get("answer"), a[p].get("step"), "->",
                  b[p].get("answer"), b[p].get("step"))
    print(f"changed: {n}")


if __name__ == "__main__":
    main()
