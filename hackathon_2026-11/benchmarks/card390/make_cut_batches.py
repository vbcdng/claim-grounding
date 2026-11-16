"""Card 390: input batches for cutting the 262 benchmark claims into pieces (no model call).

Each line: row_id, the claim, the text just before it (for filling in pointer words only),
the cited source names. The label is NOT given to the cutter, so the cut cannot lean on it.
Run: ./venv/bin/python3 benchmarks/card390/make_cut_batches.py
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BENCH = ROOT / "data" / "card191_verdict_bench_9b" / "expected.json"
OUT = HERE / "cut_batches"
N_BATCHES = 6
BEFORE_CHARS = 500


def text_before(row):
    """The writer's text just before the claim, from the row's context (may be empty)."""
    ctx = row.get("context") or ""
    claim = row["claim_text"]
    i = ctx.find(claim[:60])
    if i < 0:
        i = ctx.find("[[CITED]]")
    before = ctx[:i] if i >= 0 else ""
    return before[-BEFORE_CHARS:].strip()


def load_rows():
    return json.load(open(BENCH))["rows"]


def main():
    rows = load_rows()
    OUT.mkdir(exist_ok=True)
    size = -(-len(rows) // N_BATCHES)
    for b in range(N_BATCHES):
        part = rows[b * size:(b + 1) * size]
        with open(OUT / ("cut_%02d.jsonl" % (b + 1)), "w") as f:
            for r in part:
                f.write(json.dumps({"row_id": r["row_id"], "claim": r["claim_text"],
                                    "text_before": text_before(r),
                                    "cited": [s["name"] for s in r["sources"]]},
                                   ensure_ascii=False) + "\n")
        print("cut_%02d.jsonl" % (b + 1), len(part))


if __name__ == "__main__":
    main()
