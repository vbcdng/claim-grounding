#!/usr/bin/env python3
"""Merge the owner's grading-queue export into the gold-label JSONL files.

Owner verdicts OUTRANK model labels (rule 2026-07-18): each graded row gets
owner_verdict / owner_note / owner_graded fields appended; fable_verdict is
kept untouched for the record. Twin rows (same claim verified in two runs)
inherit the verdict of their graded twin, marked owner_source=twin_copy.

Usage: python3 benchmarks/gold_labels/merge_owner_grades.py <export.json>
Run from repo root. Idempotent: re-running overwrites the owner_* fields.
"""
import json, sys, os

GOLD_DIR = "benchmarks/gold_labels"

# (file, claim_id) -> the graded twin it copies from
TWINS = {
    ("corpus/nightB_wice_final.jsonl", "t8"): ("corpus/first_check_run.jsonl", "t8"),
    ("corpus/nightB_wice_final.jsonl", "t9"): ("corpus/first_check_run.jsonl", "t9"),
    ("corpus/printing_press_reformation_project_verification.jsonl", "t5"):
        ("corpus/printing_press_fresh_2026-07-14.jsonl", "t5"),
    ("corpus/printing_press_reformation_project_verification.jsonl", "t6"):
        ("corpus/printing_press_fresh_2026-07-14.jsonl", "t6"),
}

def main(export_path):
    exp = json.load(open(export_path))
    date = exp.get("exported", "")[:10]
    graded = {}   # (file, claim_id) -> row
    for r in exp["rows"]:
        graded[(r["file"], r["claim_id"])] = r

    # resolve verdicts: clicked verdict, else twin copy
    resolved = {}
    for key, r in graded.items():
        v = r.get("verdict")
        src = "owner"
        if not v and key in TWINS:
            tw = graded.get(TWINS[key])
            if tw and tw.get("verdict"):
                v, src = tw["verdict"], "twin_copy"
        if v:
            resolved[key] = {"verdict": v, "note": r.get("note") or "", "src": src}

    by_file = {}
    for (fname, cid), o in resolved.items():
        by_file.setdefault(fname, {})[cid] = o
    n_rows = 0
    for fname, cids in by_file.items():
        path = os.path.join(GOLD_DIR, fname)
        rows = [json.loads(l) for l in open(path) if l.strip()]
        found = {row.get("claim_id") for row in rows}
        for cid in cids:
            if cid not in found:
                print(f"WARN: {fname} {cid} not found in gold — skipped")
        for row in rows:
            o = cids.get(row.get("claim_id"))
            if o:
                row["owner_verdict"] = o["verdict"]
                row["owner_note"] = o["note"]
                row["owner_graded"] = date
                row["owner_source"] = o["src"]
                n_rows += 1
        with open(path, "w") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"  {fname}: {len(cids)} owner verdict(s) written")

    print(f"merged {n_rows} rows from {export_path} (graded {len(resolved)}, "
          f"{sum(1 for o in resolved.values() if o['src']=='twin_copy')} twin copies)")
    # ungraded rows in the export (verdict None, no twin) — just report
    skipped = [k for k in graded if k not in resolved]
    print(f"not yet graded (left untouched): {len(skipped)}")

if __name__ == "__main__":
    main(sys.argv[1])
