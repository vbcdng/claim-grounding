"""Card 397 (branch A): the pieces a later step must re-read because Haiku or Sonnet said "supported"
in the merged piece_only answers (all 300 pieces, no label read; the same rule picks the same
pieces on the held-back half). Writes answers/yes_todo_<k>.txt in N parts, skipping pieces already
in the given answers file.

  pick_yes.py --skip answers/haiku_ss_v1.jsonl --parts 4
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
A = HERE / "answers"
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

ask = sibling("ask")


def yes_ids(path):
    """Pieces whose first answer is supported, read the way ask.load_answers reads it (no label read)."""
    out = set()
    for pid, reps in ask.load_answers(path, label_free=True).items():
        r0 = next((r for r in reps if r.get("repeat", 0) == 0 and not r.get("failed")), None)
        if r0 and r0.get("answer") == "supported":
            out.add(pid)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--skip")
    ap.add_argument("--parts", type=int, default=4)
    a = ap.parse_args(argv)
    want = yes_ids(A / "haiku_po_e.jsonl") | yes_ids(A / "sonnet_po_e.jsonl")
    done = set()
    if a.skip and Path(a.skip).exists():
        done = {json.loads(x)["piece_id"] for x in Path(a.skip).read_text().splitlines() if x.strip()
                and not json.loads(x).get("failed")}
    todo = sorted(want - done)
    for k in range(a.parts):
        (A / f"yes_todo_{k}.txt").write_text("\n".join(todo[k::a.parts]) + "\n", encoding="utf-8")
    print(f"{len(want)} pieces with a Haiku or Sonnet yes; {len(want & done)} already answered; "
          f"{len(todo)} to ask in {a.parts} parts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
