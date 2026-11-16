"""Card 397 (branch A): the pieces where Haiku rewording 1 says not supported and Sonnet (neutral
question) did not say not supported: the pieces a Sonnet tie-break with the same reworded question
is asked on (all 300 pieces, no label read). Writes answers/tiebreak_todo_<k>.txt in N parts.

  pick_tiebreak.py --parts 3
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


def first_answers(path):
    """piece_id -> first answer, read the way ask.load_answers reads it (no label read)."""
    out = {}
    for pid, reps in ask.load_answers(path, label_free=True).items():
        r0 = next((r for r in reps if r.get("repeat", 0) == 0 and not r.get("failed")), None)
        if r0:
            out[pid] = str(r0.get("answer") or "").replace("_", " ")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--parts", type=int, default=3)
    a = ap.parse_args(argv)
    h2, s = first_answers(A / "haiku_ss_po_e.jsonl"), first_answers(A / "sonnet_po_e.jsonl")
    todo = sorted(p for p, ans in h2.items() if ans == "not supported" and s.get(p) != "not supported")
    for k in range(a.parts):
        (A / f"tiebreak_todo_{k}.txt").write_text("\n".join(todo[k::a.parts]) + "\n", encoding="utf-8")
    print(f"{len(todo)} pieces for the Sonnet tie-break in {a.parts} parts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
