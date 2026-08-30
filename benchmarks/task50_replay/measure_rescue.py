#!/usr/bin/env python3
"""Task #50 half two, question 1+2: sweep every analysis.json on disk and
count component-rescue activity per run.

Per run:
  claims               total text claims
  unsupported          final unsupported count
  cc_blocks            claims carrying a component_check record (rescue ran
                       and found >=1 part; a rescue that found nothing leaves
                       NO record, so this undercounts attempts)
  comps distribution   len(found)+len(missing) per component_check block
  all_found            blocks with missing == [] (reached the final re-judge)
  flips                blocks with rescued == True
  method_cr            claims whose FINAL method is component_rescue
  method_tail          tail_rescue (context)
"""
import json, sys, glob, os
from collections import Counter

roots = sys.argv[1:] or ["data"]
paths = []
for r in roots:
    paths += glob.glob(os.path.join(r, "*", "analysis.json"))
paths = sorted(set(os.path.realpath(p) for p in paths))

tot = Counter()
comp_dist = Counter()
flip_rows = []
runs = 0
for p in paths:
    try:
        a = json.load(open(p))
    except Exception:
        continue
    tc = a.get("text_claims")
    if not isinstance(tc, list) or not tc or not isinstance(tc[0], dict):
        continue
    runs += 1
    name = os.path.basename(os.path.dirname(p))
    n = len(tc)
    unsup = sum(1 for c in tc if c.get("verdict") == "unsupported")
    ccs = [c for c in tc if isinstance(c.get("component_check"), dict)]
    all_found = 0
    flips = 0
    for c in ccs:
        cc = c["component_check"]
        k = len(cc.get("found") or []) + len(cc.get("missing") or [])
        comp_dist[k] += 1
        if not cc.get("missing"):
            all_found += 1
        if cc.get("rescued"):
            flips += 1
            flip_rows.append((name, c.get("id"), (c.get("text") or "")[:70]))
    mcr = sum(1 for c in tc if c.get("method") == "component_rescue")
    mtail = sum(1 for c in tc if c.get("method") == "tail_rescue")
    tot.update(dict(claims=n, unsup=unsup, cc=len(ccs), allfound=all_found,
                    flips=flips, mcr=mcr, mtail=mtail))
    if len(ccs) or mcr:
        print(f"{name:42s} claims={n:3d} unsup={unsup:3d} cc_blocks={len(ccs):2d} "
              f"all_found={all_found:2d} flips={flips} method_cr={mcr} tail={mtail}")

print(f"\nruns scanned: {runs}")
print("totals:", dict(tot))
print("components-per-recorded-rescue distribution:", dict(sorted(comp_dist.items())))
print("\nflipped claims:")
for r in flip_rows:
    print(" ", r)
