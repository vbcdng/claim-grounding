"""Card 397 (branch A): write answers/trial30.txt, every tenth of the 300 pieces in id order (no
label read), for the trial tenth of a new Claude question before it is asked on all 300.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

records = sibling("records")


def main():
    ids = sorted(r["piece_id"] for r in records.load_pieces())
    pick = ids[::10]
    (HERE / "answers" / "trial30.txt").write_text("\n".join(pick) + "\n", encoding="utf-8")
    print(f"{len(pick)} of {len(ids)} pieces in answers/trial30.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
