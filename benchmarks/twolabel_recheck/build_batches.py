"""Build blind judging batches from rows.json: row_id + claim + extract only.
Deterministic shuffle (md5 of row_id) mixes controls among problem rows."""
import json, hashlib, os

D = os.path.dirname(os.path.abspath(__file__))
rows = json.load(open(f"{D}/rows.json"))["rows"]
print("distinct fable_gold:", sorted({str(r['fable_gold_label']) for r in rows}))
print("distinct opus:", sorted({str(r['opus_label']) for r in rows}))
print("distinct wice:", sorted({str(r['wice_label']) for r in rows}))

rows_sorted = sorted(rows, key=lambda r: hashlib.md5(r["row_id"].encode()).hexdigest())
BATCH = 6
n = 0
for i in range(0, len(rows_sorted), BATCH):
    n += 1
    blind = [{"row_id": r["row_id"],
              "claim": r["claim_text"],
              "extract": r["evidence_extract"]} for r in rows_sorted[i:i+BATCH]]
    with open(f"{D}/blind_batch_{n}.json", "w") as f:
        json.dump(blind, f, indent=1)
print(f"wrote {n} batches, {len(rows_sorted)} rows")
