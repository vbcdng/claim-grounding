#!/usr/bin/env python3
"""Tally the paper1 fork-row two-label votes (task #11 round 2).

Reads manifest.json + votes_fable_{1,2,3}.json + votes_opus_{1,2,3}.json
(each a list covering BOTH batches' rows, concatenated per voter index —
voter N of a family = batch-1 voter N's rows + batch-2 voter N's rows).
Prints per-row family majorities against the recorded fork positions.
"""
import json
import os
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
manifest = json.load(open(f"{HERE}/manifest.json"))

votes = {"fable": [], "opus": []}
for fam in votes:
    for i in (1, 2, 3):
        v = json.load(open(f"{HERE}/votes_{fam}_{i}.json"))
        votes[fam].append({row["row_id"]: row for row in v})

targets = {f"paper1_{k}": v for k, v in manifest["targets"].items()}
sentinels = {f"paper1_{k}": v for k, v in manifest["sentinels"].items()}
all_rows = [r for b in manifest["batches"].values() for r in b]


def majority(fam, rid):
    labels = [votes[fam][i][rid]["label"] for i in range(3)]
    c = Counter(labels)
    lab, n = c.most_common(1)[0]
    return lab, n


print(f"{'row':<12} {'kind':<9} {'Fable maj':<18} {'Opus maj':<18} agree?  recorded fork (Fable gold / Opus grader)")
agree_t = agree_s = 0
for rid in sorted(all_rows, key=lambda r: (r not in targets, r)):
    fl, fn = majority("fable", rid)
    ol, on = majority("opus", rid)
    ag = fl == ol
    kind = "TARGET" if rid in targets else "sentinel"
    if rid in targets:
        rec = targets[rid]
        fork = f"{rec['fable_verdict']} / {rec['grader_action']}"
        agree_t += ag
    else:
        rec = sentinels[rid]
        fork = f"agreed {rec['axis']} ({rec['fable_verdict']} / {rec['grader_action']})"
        agree_s += ag
    print(f"{rid:<12} {kind:<9} {fl} {fn}/3{'':<6} {ol} {on}/3{'':<6} {'YES' if ag else 'NO ':<6} {fork}")

print(f"\ntargets: families agree {agree_t}/{len(targets)}")
print(f"sentinels: families agree {agree_s}/{len(sentinels)}")

print("\nwithin-family unanimity:")
for rid in all_rows:
    for fam in ("fable", "opus"):
        lab, n = majority(fam, rid)
        if n < 3:
            print(f"  {rid} {fam}: {lab} only {n}/3")
