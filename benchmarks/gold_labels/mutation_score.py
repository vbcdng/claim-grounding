#!/usr/bin/env python3
"""Score the mutation bench: did each mutated claim get CAUGHT?

Usage: mutation_score.py <workdir>
<workdir> is the mutation-bench scratch dir holding mutation_manifest.json,
out_mut/analysis.json and (optionally) out_mut_baseline.json — see
docs/MUTATION_BENCH_PLAN.md. Without a baseline file the pre-mutation run
defaults to data/eggs_rerun_20260715/analysis.json in the repo."""
import json, pathlib, sys
if len(sys.argv) != 2:
    sys.exit(__doc__.strip())
SCR = pathlib.Path(sys.argv[1])
REPO = pathlib.Path(__file__).resolve().parents[2]
base = {c['id']: c for c in json.load(open(SCR/"out_mut_baseline.json"))['text_claims']} if (SCR/"out_mut_baseline.json").exists() else \
       {c['id']: c for c in json.load(open(REPO/"data/eggs_rerun_20260715/analysis.json"))['text_claims']}
mut = {c['id']: c for c in json.load(open(SCR/"out_mut"/"analysis.json"))['text_claims']}
manifest = json.load(open(SCR/"mutation_manifest.json"))

def flags(c):
    f = []
    if c.get('partial_support'): f.append('partial_support')
    cov = c.get('covering') or {}
    if cov.get('uncovered'): f.append(f"uncovered:{len(cov['uncovered'])}")
    arb = c.get('arbiter') or {}
    if arb.get('action'): f.append(f"arb:{arb['action']}")
    return f

print(f"{'claim':5} {'kind':9} {'baseline':10} -> {'mutated':12} {'newflags':28} VERDICT")
caught = 0
rows = []
for m in manifest:
    cid = m['claim']; b = base.get(cid, {}); n = mut.get(cid, {})
    bv, nv = b.get('verdict','?'), n.get('verdict','?')
    bflags, nflags = set(flags(b)), set(flags(n))
    added = nflags - bflags
    is_caught = (nv != 'supported') or bool(added)
    caught += is_caught
    verdict = "CAUGHT" if is_caught else "*** MISSED ***"
    print(f"{cid:5} {m['kind']:9} {bv:10} -> {nv:12} {(','.join(sorted(added)) or '-'):28} {verdict}")
    rows.append({**m, "baseline_verdict": bv, "mutated_verdict": nv, "added_flags": sorted(added), "caught": bool(is_caught)})
print(f"\nCATCH RATE: {caught}/{len(manifest)} = {caught/len(manifest)*100:.0f}%")
json.dump({"catch_rate": f"{caught}/{len(manifest)}", "rows": rows}, open(SCR/"score_result.json","w"), indent=2)
