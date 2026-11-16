"""Card 398 (branch B), repair step 3: run shared/ask.py with the cited works read from the
repaired texts (repaired_texts/map.json, made by pdfrepair.py) instead of the raw PDF bytes. Every
other part of ask.py (question file, field set, limits, money guards, store) is unchanged; the
shared program itself is not edited. Arguments are those of ask.py.
`ask_repaired.py lists` writes the piece lists answers/repaired_*.txt from raw_pdf_works.json:
  repaired_all.txt      every piece citing a repaired work (both halves)
  repaired_main.txt     those without an earlier sentence (asked with piece_only)
  repaired_six.txt      those with an earlier sentence (asked with fields_v1 + neutral_v1_earlier.md)
  repaired_jev_f1.txt   Jev with fields_v1: all but the hedged main pieces (build_answers.py's rule)
  repaired_jev_po.txt   Jev with piece_only: the hedged main pieces
  repaired_opus.txt     the Opus pieces: tuning pieces of repaired_main.txt that already have an
                        Opus answer or whose (repaired) Sonnet answer is not_supported or unsure (the
                        pieces round 12 reads Opus on), plus every held-back main piece, chosen without
                        reading any held-back answer (saved, never scored).
Run `lists` again after answers/sonnet_repaired.jsonl is complete."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

records = sibling("records")
MAP = HERE / "repaired_texts" / "map.json"
ANS = HERE / "answers"


def install():
    mapping = json.loads(MAP.read_text(encoding="utf-8"))
    old = records.work_text

    def work_text(rel):
        return old(mapping.get(rel, rel))

    records.work_text = work_text
    return mapping


def _write(name, ids):
    (ANS / name).write_text("".join(p + "\n" for p in sorted(ids)), encoding="utf-8")
    print(f"answers/{name}: {len(ids)}")


def _answers(path):
    out = {}
    if path.exists():
        for x in path.read_text(encoding="utf-8").splitlines():
            if x.strip():
                d = json.loads(x)
                if d.get("repeat", 0) == 0:
                    out[d["piece_id"]] = d
    return out


def lists():
    listed = json.loads((HERE / "raw_pdf_works.json").read_text(encoding="utf-8"))
    tuning, held = set(listed["tuning_pieces"]), set(listed["held_back_pieces"])
    every = tuning | held
    recs = {r["piece_id"]: r for r in records.load_pieces() if r["piece_id"] in every}
    six = {p for p, r in recs.items() if r.get("earlier_sentence")}
    main = every - six
    hedged = {p for p in main if recs[p].get("certainty") in ("possible", "likely")}
    opus_old = _answers(ANS / "opus_neutral.jsonl")
    sonnet = _answers(ANS / "sonnet_repaired.jsonl")
    opus = {p for p in main & tuning
            if p in opus_old or (sonnet.get(p) or {}).get("answer") in ("not_supported", "unsure")}
    opus |= main & held
    _write("repaired_all.txt", every)
    _write("repaired_main.txt", main)
    _write("repaired_six.txt", six)
    _write("repaired_jev_f1.txt", every - hedged)
    _write("repaired_jev_po.txt", hedged)
    _write("repaired_opus.txt", opus)
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["lists"]:
        return lists()
    install()
    return sibling("ask").main(argv)


if __name__ == "__main__":
    sys.exit(main())
