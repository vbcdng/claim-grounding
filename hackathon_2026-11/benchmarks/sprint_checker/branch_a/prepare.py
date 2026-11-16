"""Card 397 (branch A): which pieces each step still has to be asked, with no label read.

- checks that questions/neutral_v1_earlier.md under fields_v1 gives the same prompt as the shared
  neutral_v1.md under piece_only for every piece without an earlier sentence (so saved piece_only
  answers count as answers to it);
- writes answers/earlier6.txt (the pieces that store an earlier sentence) and
  answers/<step>_missing.txt (pieces of all 300 with no usable piece_only answer of that step) and
  answers/<step>_todo.txt (missing plus the earlier-sentence pieces: what ask.py is run on with
  questions/neutral_v1_earlier.md, field set fields_v1).
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent / "shared"
SAVED = HERE.parent / "saved_answers"
sys.path.insert(0, str(SHARED))

from sprint_shared_load import sibling  # noqa: E402

ask = sibling("ask")
records = sibling("records")

MINE = HERE / "questions" / "neutral_v1_earlier.md"
PIECE_ONLY = {"haiku": [SAVED / "haiku_piece_only.jsonl"],
              "sonnet": [SAVED / "sonnet_piece_only_fieldtest.jsonl", SAVED / "sonnet_piece_only_p0.jsonl",
                         SAVED / "sonnet_piece_only_p1.jsonl"]}
CHUNKS = {"haiku": 2, "sonnet": 3}


def main():
    recs = records.load_pieces()
    mine, base = records.load_question(MINE), records.load_question(SHARED / "questions" / "neutral_v1.md")
    same = differ = 0
    earlier = []
    for r in recs:
        if records.step0(r):
            continue
        if r.get("earlier_sentence"):
            earlier.append(r["piece_id"])
            continue
        a = records.render(mine, records.step_input(r, "fields_v1", "haiku"))
        b = records.render(base, records.step_input(r, "piece_only", "haiku"))
        same += a == b
        differ += a != b
    print(f"prompt check: {same} pieces give the same prompt, {differ} differ")
    out = HERE / "answers"
    out.mkdir(exist_ok=True)
    (out / "earlier6.txt").write_text("\n".join(sorted(earlier)) + "\n", encoding="utf-8")
    print(f"earlier sentence stored: {len(earlier)} pieces")
    asked = [r["piece_id"] for r in recs if not records.step0(r) and not r.get("earlier_sentence")]
    for step, files in PIECE_ONLY.items():
        have = set()
        for f in files:
            for pid, reps in ask.load_answers(f, label_free=True).items():
                if any(x["repeat"] == 0 and not x.get("failed") for x in reps):
                    have.add(pid)
        missing = sorted(set(asked) - have)
        (out / f"{step}_missing.txt").write_text("\n".join(missing) + "\n", encoding="utf-8")
        todo = sorted(missing + earlier)
        (out / f"{step}_todo.txt").write_text("\n".join(todo) + "\n", encoding="utf-8")
        n = CHUNKS[step]
        for k in range(n):  # parallel runs, one answers file each
            (out / f"{step}_todo_{k}.txt").write_text("\n".join(todo[k::n]) + "\n", encoding="utf-8")
        print(f"{step}: {len(have & set(asked))} piece_only answers saved, {len(missing)} missing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
