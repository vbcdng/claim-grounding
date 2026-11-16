"""Card 398 (branch B): list the pieces that still lack a usable piece_only answer per step.

Writes answers/missing_<step>.txt (one piece id per line). No labels are read.
The 6 pieces that store an earlier sentence are not listed: branch B takes their fields_v1 answers
(the only saved field set that holds the earlier sentence; see build_answers.py).
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

records = sibling("records")
SA = HERE.parent / "saved_answers"
ANS = HERE / "answers"

PIECE_ONLY = {
    "haiku": [SA / "haiku_piece_only.jsonl", ANS / "haiku_piece_only_rest.jsonl"],
    "sonnet": [SA / "sonnet_piece_only_fieldtest.jsonl", SA / "sonnet_piece_only_p0.jsonl",
               SA / "sonnet_piece_only_p1.jsonl", ANS / "sonnet_piece_only_rest.jsonl"],
    "gemma_paid": [ANS / "gemma_paid_piece_only.jsonl"],
}


def earlier_ids():
    return sorted(r["piece_id"] for r in records.load_pieces() if r.get("earlier_sentence"))


def usable(paths, field_set="piece_only"):
    ok = set()
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("piece_id") and d.get("repeat", 0) == 0 and d.get("field_set") == field_set \
                    and not d.get("failed") and d.get("answer") is not None:
                ok.add(d["piece_id"])
    return ok


def main():
    ANS.mkdir(exist_ok=True)
    allp = sorted(r["piece_id"] for r in records.load_pieces())
    skip = set(earlier_ids())
    for step, paths in PIECE_ONLY.items():
        miss = [p for p in allp if p not in skip and p not in usable(paths)]
        (ANS / f"missing_{step}.txt").write_text("".join(p + "\n" for p in miss), encoding="utf-8")
        print(step, "missing", len(miss))
    # Jev: fields_v1 and piece_only send the same input unless the certainty is possible/likely
    # (ask.jev_state adds a lead) or an earlier sentence is stored; only the hedged ones are re-asked.
    hedged = [r["piece_id"] for r in records.load_pieces()
              if r.get("certainty") in ("possible", "likely") and r["piece_id"] not in skip]
    miss = [p for p in sorted(hedged) if p not in usable([ANS / "jev_piece_only_hedged.jsonl"])]
    (ANS / "missing_jev.txt").write_text("".join(p + "\n" for p in miss), encoding="utf-8")
    print("jev missing (hedged only)", len(miss))
    return 0


if __name__ == "__main__":
    sys.exit(main())
