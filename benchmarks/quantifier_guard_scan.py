"""Blast-radius scan for the quantifier guard (task #75). Offline, $0.

Walks every data/*/analysis.json, replays matcher._quantifier_overreach on
each SUPPORTED claim's used evidence (sentence + window) and lists the claims
the guard would have flipped. For paper1 gate folders the ground-truth
expectation is printed next to the hit so a false fire is visible at once.

Run:  venv/bin/python3 benchmarks/quantifier_guard_scan.py [data_dir]
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.papertrail import matcher  # noqa: E402


def _gt(path):
    try:
        rows = json.load(open(path))
        rows = rows.get("claims", rows) if isinstance(rows, dict) else rows
        return {r["id"]: r for r in rows if isinstance(r, dict) and "id" in r}
    except Exception:  # noqa
        return {}


def main(data_dir="data"):
    gts = {"paper1": _gt("benchmarks/paper1_ground_truth.json"),
           "bentonite": _gt("benchmarks/bentonite_ground_truth.json"),
           "chimp": _gt("benchmarks/chimpanzee_ground_truth.json")}
    scanned = supported = fires = 0
    seen_texts = set()
    unique_fires = {}
    for p in sorted(glob.glob(os.path.join(data_dir, "*", "analysis.json"))):
        folder = os.path.basename(os.path.dirname(p))
        try:
            a = json.load(open(p))
        except Exception:  # noqa
            continue
        claims = a.get("text_claims") or []
        gt = next((g for k, g in gts.items() if k in folder), {})
        for c in claims:
            if not isinstance(c, dict) or c.get("verdict") != "supported":
                scanned += 1
                continue
            scanned += 1
            supported += 1
            evs = [e for e in (c.get("evidences") or []) if e.get("supported")] \
                or [e for e in (c.get("evidences") or []) if e.get("sentence")]
            if not evs and c.get("evidence"):
                evs = [c["evidence"]]
            q = matcher._quantifier_overreach(
                c.get("text", ""), [e.get("sentence") for e in evs],
                [e.get("window") for e in evs])
            if q:
                fires += 1
                exp = (gt.get(c.get("id")) or {}).get("expect", "")
                key = (c.get("text", "")[:80], q["evidence_phrase"])
                unique_fires.setdefault(key, []).append(folder)
                seen_texts.add(c.get("text", "")[:80])
                print(f"FIRE {folder} {c.get('id')} method={c.get('method')} "
                      f"gt={exp or '-'} | claim: '{q['claim_phrase']}' | "
                      f"evidence: '{q['evidence_phrase']}'")
    print(f"\nclaims scanned: {scanned}; supported: {supported}; guard fires: {fires}; "
          f"distinct (claim, evidence phrase) pairs: {len(unique_fires)}")
    for (t, ep), folders in unique_fires.items():
        print(f"  - {len(folders)} folder(s): {t!r} <- {ep!r}")


if __name__ == "__main__":
    main(*sys.argv[1:])
