"""Card 398 (branch B): for the TUNING pieces citing a repaired work, print each step's old answer (raw
PDF bytes) beside its new one (repaired text). No label is read; held-back pieces are left out.
Usage: repaircompare.py"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ANS = HERE / "answers"
SA = HERE.parent / "saved_answers"
OLD = {"gemma": [ANS / "gemma_paid_piece_only.jsonl"],
       "jev": [SA / "jev_fields_v1.jsonl"],
       "haiku": [SA / "haiku_piece_only.jsonl", ANS / "haiku_piece_only_rest.jsonl"],
       "sonnet": [SA / "sonnet_piece_only_fieldtest.jsonl", SA / "sonnet_piece_only_p0.jsonl",
                  SA / "sonnet_piece_only_p1.jsonl", ANS / "sonnet_piece_only_rest.jsonl"],
       "opus": [ANS / "opus_neutral_asked.jsonl"]}


def first(paths, ids):
    out = {}
    for p in paths:
        if not p.exists():
            continue
        for x in p.read_text(encoding="utf-8").splitlines():
            if not x.strip():
                continue
            d = json.loads(x)
            if d.get("piece_id") in ids and d.get("repeat", 0) == 0:
                a = "failed" if d.get("failed") else d.get("answer")
                if d.get("answer") == "true" or isinstance(d.get("jev_probability"), (int, float)):
                    a = f"{a} p={d.get('jev_probability')}"
                if out.get(d["piece_id"]) in (None, "failed") or a != "failed":
                    out[d["piece_id"]] = a
    return out


def main():
    ids = set(json.loads((HERE / "raw_pdf_works.json").read_text(encoding="utf-8"))["tuning_pieces"])
    rows = {s: (first(OLD[s], ids), first([ANS / f"{s}_repaired.jsonl"], ids)) for s in OLD}
    for pid in sorted(ids):
        print(pid + "  " + "  ".join(f"{s}: {rows[s][0].get(pid)} -> {rows[s][1].get(pid, '-')}" for s in OLD))
    return 0


if __name__ == "__main__":
    sys.exit(main())
