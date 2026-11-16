"""Card 405: one answers file for step "on" (Opus, neutral question, field set piece_only) on both halves,
so the frozen chain can run on the held-back half too. No label is read.

- tuning: answers/opus_po_unsure.jsonl (asked by card 405 on the Sonnet-unsure tuning pieces, pick_unsure.py)
- held-back: the rows of saved_answers_night/opus_piece_only.jsonl (card 400, same question and field set)
  for the held-back pieces where Sonnet's neutral answer (answers/sonnet_po_e.jsonl, repeat 0) is "unsure"

  ./venv/bin/python3 benchmarks/sprint_checker/branch_a/merge_unsure.py
writes answers/opus_po_unsure_all.jsonl.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

ask = sibling("ask")
split = sibling("split")

NIGHT = HERE.parent / "saved_answers_night" / "opus_piece_only.jsonl"
OUT = HERE / "answers" / "opus_po_unsure_all.jsonl"


def heldback_unsure():
    tuning = split.allowed_ids(False)
    out = set()
    for pid, reps in ask.load_answers(HERE / "answers" / "sonnet_po_e.jsonl", label_free=True).items():
        r0 = next((r for r in reps if r["repeat"] == 0), None)
        if pid not in tuning and r0 and not r0.get("failed") and r0.get("answer") == "unsure":
            out.add(pid)
    return out


def main():
    want = heldback_unsure()
    lines = [x for x in (HERE / "answers" / "opus_po_unsure.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    n_t = len(lines)
    for x in NIGHT.read_text(encoding="utf-8").splitlines():
        if x.strip() and json.loads(x)["piece_id"] in want:
            lines.append(x)
    OUT.write_text("".join(x + "\n" for x in lines), encoding="utf-8")
    print(f"{n_t} tuning rows + {len(lines) - n_t} held-back rows ({len(want)} held-back pieces Sonnet-unsure) -> {OUT}")


if __name__ == "__main__":
    main()
