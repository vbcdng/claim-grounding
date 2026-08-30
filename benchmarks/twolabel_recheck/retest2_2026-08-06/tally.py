"""Tally the retest votes: majority per family per row, family agreement,
comparison to round-1 single votes, sentinel stability check.
Expects votes_fable_{1,2,3}.json and votes_opus_{1,2,3}.json next to manifest.json."""
import json, os
from collections import Counter

D = os.path.dirname(os.path.abspath(__file__))
manifest = json.load(open(f"{D}/manifest.json"))

def load(family):
    per_row = {}
    for i in (1, 2, 3):
        for v in json.load(open(f"{D}/votes_{family}_{i}.json")):
            per_row.setdefault(v["row_id"], []).append(v)
    return per_row

fable, opus = load("fable"), load("opus")

def majority(votes):
    c = Counter(v["label"] for v in votes)
    label, n = c.most_common(1)[0]
    return label, n, len(votes)

print(f"{'row':<12}{'kind':<10}{'Fable maj':<22}{'Opus maj':<22}{'agree':<7}round1 F/O")
for rid in manifest["round1_votes"]:
    kind = "TARGET" if rid in manifest["targets"] else "sentinel"
    fl, fn, ft = majority(fable[rid])
    ol, on, ot = majority(opus[rid])
    r1 = manifest["round1_votes"][rid]
    agree = "YES" if fl == ol else "no"
    print(f"{rid:<12}{kind:<10}{fl+f' ({fn}/{ft})':<22}{ol+f' ({on}/{ot})':<22}{agree:<7}{r1['fable'][:4]}/{r1['opus'][:4]}")

targets_agree = sum(majority(fable[r])[0] == majority(opus[r])[0] for r in manifest["targets"])
sent_agree = sum(majority(fable[r])[0] == majority(opus[r])[0] for r in manifest["sentinels"])
sent_stable = sum(
    majority(fable[r])[0] == manifest["round1_votes"][r]["fable"]
    and majority(opus[r])[0] == manifest["round1_votes"][r]["opus"]
    for r in manifest["sentinels"]
)
unanimous = sum(
    majority(fable[r])[1] == 3 and majority(opus[r])[1] == 3 for r in manifest["round1_votes"]
)
print(f"\ntargets: families agree on {targets_agree}/2 (round 1: 0/2)")
print(f"sentinels: families agree on {sent_agree}/3; labels unchanged from round 1 on {sent_stable}/3")
print(f"rows unanimous within BOTH families: {unanimous}/5")
