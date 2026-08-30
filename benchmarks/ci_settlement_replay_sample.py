#!/usr/bin/env python3
"""Cut an arbiter-replay sample down to the frozen settlement rows (task #30, step 3a).

`benchmarks/arbiter_replay.py replay` runs whatever `sample.json` in its
workspace holds. The 2026-08-02 workspaces hold every arbiter-touched claim of
each batch — 69 on pilot100, 39 on fresh50 — but step 3a only asks about the 29
rows frozen by `benchmarks/ci_settlement_rows.py`: 18 on pilot100, 11 on
fresh50. Replaying the full workspaces would spend ~3.7x the money on rows
nobody is asking about, so this script writes a new workspace per batch whose
`sample.json` carries only the settlement rows.

Each new workspace gets:
  * `inventory.json` — copied unchanged from the 2026-08-02 workspace, so the
    replay harness's `--all` path and the report generator still work;
  * `sample.json` — the same row dicts, filtered to the settlement claim ids,
    plus a `settlement_source` block recording where the id list came from.

Pure: no API calls, no network, reads only frozen artifacts.

    python3 benchmarks/ci_settlement_replay_sample.py \
        --settlement docs/settlement_rows_2026-08-04/settlement_rows.json \
        --out docs/arbiter_replay_2026-08-04

Then, per batch, per candidate arm:

    python3 benchmarks/arbiter_replay.py replay \
        --out docs/arbiter_replay_2026-08-04/pilot100 \
        --data-dir data/citation_integrity/batch_dev_pilot100_run_gemma_0802 \
        --model <candidate>
"""

import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# batch tag -> (2026-08-02 replay workspace, the run dir its inventory indexes)
SOURCE_WORKSPACES = {
    "pilot100": ("docs/arbiter_replay_2026-08-02/pilot100",
                 "data/citation_integrity/batch_dev_pilot100_run_gemma_0802"),
    "fresh50": ("docs/arbiter_replay_2026-08-02/fresh50",
                "data/citation_integrity/batch_dev_fresh50_run_gemma_0802"),
}


def wanted_ids(settlement_path):
    """{batch tag: {claim_id: qualified row id}} from the frozen settlement list."""
    with open(settlement_path, "r", encoding="utf-8") as f:
        frozen = json.load(f)
    per_batch = {}
    for row in frozen["rows"]:
        per_batch.setdefault(row["batch"], {})[row["judge"]["claim_id"]] = row["row"]
    return frozen, per_batch


def build(settlement_path, out_root):
    frozen, per_batch = wanted_ids(settlement_path)
    made = []
    for batch, ids in sorted(per_batch.items()):
        if batch not in SOURCE_WORKSPACES:
            raise SystemExit(f"unknown batch tag {batch!r} in {settlement_path}")
        src_ws, run_dir = SOURCE_WORKSPACES[batch]
        inv_path = os.path.join(ROOT, src_ws, "inventory.json")
        with open(inv_path, "r", encoding="utf-8") as f:
            inv = json.load(f)
        rows = [c for c in inv["claims"] if c["claim_id"] in ids]
        found = {c["claim_id"] for c in rows}
        missing = sorted(set(ids) - found)
        if missing:
            raise SystemExit(f"{batch}: settlement claim(s) absent from "
                             f"{inv_path}: {missing}")
        out_ws = os.path.join(out_root, batch)
        os.makedirs(out_ws, exist_ok=True)
        shutil.copyfile(inv_path, os.path.join(out_ws, "inventory.json"))
        sample = {
            "settlement_source": {
                "settlement_rows": os.path.relpath(settlement_path, ROOT),
                "batch": batch,
                "run_dir": run_dir,
                "source_workspace": src_ws,
                "row_ids": [ids[c["claim_id"]] for c in rows],
            },
            "counts": {"settlement_rows": len(rows),
                       "inventory_claims": len(inv["claims"])},
            "rows": rows,
        }
        with open(os.path.join(out_ws, "sample.json"), "w", encoding="utf-8") as f:
            json.dump(sample, f, indent=1, ensure_ascii=False)
        made.append((batch, out_ws, run_dir, len(rows), len(inv["claims"])))
    return frozen, made


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--settlement",
                    default="docs/settlement_rows_2026-08-04/settlement_rows.json")
    ap.add_argument("--out", required=True,
                    help="workspace root; one sub-dir per batch is written")
    args = ap.parse_args(argv)
    settlement = args.settlement
    if not os.path.isabs(settlement):
        settlement = os.path.join(ROOT, settlement)
    out_root = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    frozen, made = build(settlement, out_root)
    total = sum(m[3] for m in made)
    if total != frozen["n_rows"]:
        raise SystemExit(f"wrote {total} rows but the frozen list has "
                         f"{frozen['n_rows']} — refusing to look complete.")
    for batch, ws, run_dir, n, n_inv in made:
        print(f"{batch}: {n} settlement rows (of {n_inv} arbiter-touched) "
              f"→ {os.path.relpath(ws, ROOT)}   --data-dir {run_dir}")
    print(f"total {total} rows == frozen n_rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
