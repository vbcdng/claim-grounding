"""Card 398 (branch B): join the saved answers into one answers file per step for combine.py.

Field set: piece_only (card S2's Field decision). The 6 pieces that store an earlier sentence (the
work order of 14 Nov 16:41: they must be asked with the earlier sentence) take the answers to
questions/neutral_v1_earlier.md asked with field set fields_v1: that question is neutral_v1 without
the certainty block, so the prompt holds the piece, the earlier sentence and the works
(answers/<step>_six_earlier.jsonl). Until such an answer exists, the saved fields_v1 answer to
neutral_v1 is used (it also carries the certainty line). Every line keeps its own field_set and
question, so the mix is visible.

Jev: piece_only and fields_v1 send Jev the same input unless the certainty is possible/likely
(ask.jev_state); so its fields_v1 answers are used for the others and piece_only for the hedged ones.

Repaired texts (15 Nov, the orchestrating session's work order of 12:19): answers/<step>_repaired.jsonl
hold answers asked by ask_repaired.py with the cited works read from repaired_texts/ instead of raw PDF
bytes; they come last in each list, so a usable repaired answer replaces the old one of that piece.
Opus: the answers ask.py wrote are in answers/opus_neutral_asked.jsonl; answers/opus_neutral.jsonl (read
by drafts/r10 to r12) is built from it plus answers/opus_repaired.jsonl.

Output: answers/<step>.jsonl with step in gemma, jev, haiku, sonnet, opus_neutral. Re-run after new
answers arrive.
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

SOURCES = {
    # step: (piece_only files, fields_v1 file for the earlier-sentence pieces)
    "gemma": ([ANS / "gemma_paid_piece_only.jsonl", ANS / "gemma_repaired.jsonl"],
              SA / "gemma_paid_fields_v1.jsonl"),
    "haiku": ([SA / "haiku_piece_only.jsonl", ANS / "haiku_piece_only_rest.jsonl", ANS / "haiku_repaired.jsonl"],
              SA / "haiku_fields_v1_merged.jsonl"),
    "sonnet": ([SA / "sonnet_piece_only_fieldtest.jsonl", SA / "sonnet_piece_only_p0.jsonl",
                SA / "sonnet_piece_only_p1.jsonl", ANS / "sonnet_piece_only_rest.jsonl",
                ANS / "sonnet_repaired.jsonl"],
               SA / "sonnet_fields_v1_merged.jsonl"),
}


def read(path):
    if not Path(path).exists():
        return []
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


def best_lines(paths, field_set, only=None):
    """piece -> repeat -> line; a usable line beats a failed one, a later usable line beats an earlier."""
    out = {}
    for p in paths:
        for d in read(p):
            pid = d.get("piece_id")
            if not pid or d.get("field_set") != field_set or (only is not None and pid not in only):
                continue
            rep = d.get("repeat", 0)
            old = out.setdefault(pid, {}).get(rep)
            ok = not d.get("failed") and d.get("answer") is not None
            if old is None or ok or (old.get("failed") or old.get("answer") is None):
                out[pid][rep] = d
    return out


def write(step, table):
    ANS.mkdir(exist_ok=True)
    n = failed = 0
    with open(ANS / f"{step}.jsonl", "w", encoding="utf-8") as f:
        for pid in sorted(table):
            for rep in sorted(table[pid]):
                d = table[pid][rep]
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
                n += rep == 0
                failed += rep == 0 and (bool(d.get("failed")) or d.get("answer") is None)
    print(f"{step}: {n} pieces, {failed} failed first answers; answers/{step}.jsonl")


def main():
    pieces = records.load_pieces()
    six = {r["piece_id"] for r in pieces if r.get("earlier_sentence")}
    hedged = {r["piece_id"] for r in pieces if r.get("certainty") in ("possible", "likely")} - six
    for step, (po, f1) in SOURCES.items():
        t = best_lines(po, "piece_only")
        for pid in six:
            t.pop(pid, None)
        t.update(best_lines([f1], "fields_v1", only=six))
        own = best_lines([ANS / f"{step}_six_earlier.jsonl"], "fields_v1", only=six)
        for pid, reps in own.items():
            if 0 in reps and not reps[0].get("failed") and reps[0].get("answer") is not None:
                t[pid] = {0: reps[0]}
        write(step, t)
    # own question 1 (differs_v1): piece_only, the 6 earlier-sentence pieces with fields_v1 (the question
    # has no certainty block, so they get the piece, the earlier sentence and the works)
    t = best_lines([ANS / "gemma_differs_v1.jsonl"], "piece_only")
    for pid in six:
        t.pop(pid, None)
    t.update(best_lines([ANS / "gemma_differs_v1_six.jsonl"], "fields_v1", only=six))
    write("gemma_differs", t)
    t = best_lines([SA / "jev_fields_v1.jsonl", ANS / "jev_repaired.jsonl"], "fields_v1")
    for pid in hedged:
        t.pop(pid, None)
    t.update(best_lines([ANS / "jev_piece_only_hedged.jsonl"], "piece_only", only=hedged))
    write("jev", t)
    write("opus_neutral", best_lines([ANS / "opus_neutral_asked.jsonl", ANS / "opus_repaired.jsonl"], "piece_only"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
