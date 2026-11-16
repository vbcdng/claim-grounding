"""Card S1 (card 395): the 65 pieces of the field test of card S2, and their deliberately wrong values.

field_test_ids.json (made once by `make`, like split.json never changed):
- the 20 pieces whose certainty is "possible" or "likely",
- the 6 pieces that store an earlier sentence (one is also hedged),
- 40 ordinary pieces (in scope, certainty "stated", no earlier sentence, no tag), 20 wrong and
  20 right, at most one per claim, taken in the order of sha256("fieldtest:" + piece_id).
The choice reads round1.jsonl directly and never reads split.json.

wrong_values() for these pieces: "possible" or "likely" becomes "stated"; "stated" becomes
"possible"; an earlier sentence is replaced by the earlier sentence of the next of the 6 in id
order (the last takes the first). The original sentence has no wrong version (plan page).

Run: ./venv/bin/python3 benchmarks/sprint_checker/shared/wrong_fields.py make   (writes or checks)
     ./venv/bin/python3 benchmarks/sprint_checker/shared/wrong_fields.py show
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from sprint_shared_load import sibling  # noqa: E402

split = sibling("split")

IDS = HERE / "field_test_ids.json"
HEDGED = split.HEDGED


def ordinary(r):
    return (r["door"] == "in_scope" and r["certainty"] == "stated" and not r.get("earlier_sentence")
            and not r["tags"] and not r["separate_groups"])


def make(recs):
    hedged = sorted(r["piece_id"] for r in recs if r["certainty"] in HEDGED)
    earlier = sorted(r["piece_id"] for r in recs if r.get("earlier_sentence"))
    by_id = {r["piece_id"]: r for r in recs}
    picked = {"wrong": [], "right": []}
    claims = set()
    for pid in sorted((r["piece_id"] for r in recs if ordinary(r)),
                      key=lambda i: hashlib.sha256(("fieldtest:" + i).encode()).hexdigest()):
        r = by_id[pid]
        side = "wrong" if r["label"] == "not_supported" else "right"
        if len(picked[side]) < 20 and r["row_id"] not in claims:
            picked[side].append(pid)
            claims.add(r["row_id"])
    ids = sorted(set(hedged) | set(earlier) | set(picked["wrong"]) | set(picked["right"]))
    return {"about": "Card S1 (card 395): the 65 pieces of the field test (card S2). See wrong_fields.py.",
            "hedged": hedged, "earlier_sentence": earlier,
            "ordinary_wrong": sorted(picked["wrong"]), "ordinary_right": sorted(picked["right"]),
            "pieces": ids}


def load_ids(path=IDS):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def flip(certainty):
    return "stated" if certainty in HEDGED else "possible"


def wrong_values(recs=None, ids=None):
    """piece_id -> {certainty, earlier_sentence} wrong values, for the 65 pieces only."""
    if recs is None:
        records = sibling("records")
        recs = records.load_pieces()
    ids = ids if ids is not None else load_ids()
    by_id = {r["piece_id"]: r for r in recs}
    six = sorted(ids["earlier_sentence"])
    out = {}
    for pid in ids["pieces"]:
        r = by_id[pid]
        v = {"certainty": flip(r["certainty"])}
        if pid in six:
            v["earlier_sentence"] = by_id[six[(six.index(pid) + 1) % len(six)]]["earlier_sentence"]
        out[pid] = v
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Card S1: the field-test pieces and their wrong values.")
    ap.add_argument("cmd", choices=("make", "show"))
    ap.add_argument("--out", default=str(IDS))
    a = ap.parse_args(argv)
    if a.cmd == "make":
        fresh = make(split.read_raw())
        text = json.dumps(fresh, indent=1) + "\n"
        out = Path(a.out)
        if out.exists() and out.read_text(encoding="utf-8") != text:
            print(f"refused: {out} exists and differs from what this program makes now")
            return 1
        if not out.exists():
            out.write_text(text, encoding="utf-8")
        print({k: len(v) for k, v in fresh.items() if isinstance(v, list)})
        return 0
    for pid, v in wrong_values().items():
        print(pid, json.dumps(v, ensure_ascii=False)[:160])
    return 0


if __name__ == "__main__":
    sys.exit(main())
