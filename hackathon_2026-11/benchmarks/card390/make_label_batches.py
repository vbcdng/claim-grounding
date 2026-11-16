"""Card 390: input batches for labelling the pieces of the 128 wrong (not_supported) claims.

The old benchmark's label of each claim (card 34, benchmarks/reference_checker/batches/
batch_NN_run/labels.jsonl, last record per row) already holds the decider's parts, each with a
classification and a verbatim quote, plus the checker's reason. A piece label is decided from
those parts (decide step) and checked against the source file (check step). No model call here.
Run: ./venv/bin/python3 benchmarks/card390/make_label_batches.py
"""
import glob
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BENCH = ROOT / "data" / "card191_verdict_bench_9b" / "expected.json"
LABELS = "benchmarks/reference_checker/batches/batch_0*_run/labels.jsonl"
OUT = HERE / "label_batches"
N_BATCHES = 4


def old_labels():
    """Last record per row_id over the nine batch runs (card 191's rule: last record wins)."""
    last = {}
    for f in sorted(glob.glob(str(ROOT / LABELS))):
        for line in open(f):
            if line.strip():
                r = json.loads(line)
                last[r["row_id"]] = r
    return last


def parts_of(rec):
    """The parts of the record's decision (settled decision if there is one)."""
    dec = rec.get("settle") or rec.get("decide") or {}
    return [{"part": p.get("part"), "classification": p.get("classification"),
             "quote": p.get("quote"), "source": p.get("source")} for p in dec.get("parts") or []]


def main():
    rows = {r["row_id"]: r for r in json.load(open(BENCH))["rows"]}
    old = old_labels()
    pieces = [json.loads(l) for l in open(HERE / "pieces_262.jsonl")]
    items = []
    for rid, row in rows.items():
        if row["label"] != "not_supported":
            continue
        rec = old.get(rid)
        if rec is None or rec.get("final_label") != "not_supported":
            raise SystemExit("%s: no matching old label record" % rid)
        mine = [{"piece_id": p["piece_id"], "piece": p["piece"], "certainty": p["certainty"],
                 "certainty_cue": p["certainty_cue"]} for p in pieces
                if p["row_id"] == rid and p["door"] != "skipped"]
        if not mine:
            continue
        items.append({"row_id": rid, "claim": row["claim_text"], "old_kind": row.get("kind"),
                      "old_note": rec.get("final_note") or row.get("note"),
                      "old_check_reason": (rec.get("check") or {}).get("reason", "")[:3000],
                      "old_parts": parts_of(rec),
                      "source_files": [s["file"] for s in row["sources"]],
                      "pieces": mine})
    OUT.mkdir(exist_ok=True)
    size = -(-len(items) // N_BATCHES)
    for b in range(N_BATCHES):
        part = items[b * size:(b + 1) * size]
        with open(OUT / ("label_%02d.jsonl" % (b + 1)), "w") as f:
            for it in part:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
        print("label_%02d.jsonl" % (b + 1), len(part), "claims",
              sum(len(it["pieces"]) for it in part), "pieces")


if __name__ == "__main__":
    main()
