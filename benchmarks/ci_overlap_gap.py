"""Q1 gap decomposition + Q3 overlap stats from existing artifacts. Pure, $0."""
import json, os, sys, itertools
ROOT = "/home/moje/Documents/python_projects/claim-grounding"
sys.path.insert(0, os.path.join(ROOT, "benchmarks"))
sys.path.insert(0, ROOT)
from citation_integrity_bench import MAJOR, _collapse
from wice_bench import _tool_bucket
from ci_blind_compare import sides as blind_sides

BASE = f"{ROOT}/data/citation_integrity"
gt = json.load(open(f"{BASE}/batch_dev_pilot100/ci_ground_truth.json"))["claims"]
blind = json.load(open(f"{BASE}/c0_blind_sonnet.json"))["votes"]
dec = json.load(open(f"{BASE}/c2_decomposition.json"))

RUNS = [("old", "batch_dev_pilot100_run"),
        ("qwen37", "batch_dev_pilot100_run_qwen37"),
        ("gemma_v2", "batch_dev_pilot100_run_gemma_v2"),
        ("gemma_prod", "batch_dev_pilot100_run_gemma_prod"),
        ("qwen37_nothink", "batch_dev_pilot100_run_qwen37_nothink")]

acc = {k for k, g in gt.items() if g["label"] == "ACCURATE"}
print("=== label distribution (fine) ===")
from collections import Counter
print(Counter(g["label"] for g in gt.values()).most_common())

print("\n=== Q1: where each arm's disagreements come from (strict mapping) ===")
for name, d in RUNS:
    a = json.load(open(f"{BASE}/{d}/analysis.json"))
    by_key = {}
    for c in a.get("text_claims", []):
        for k in c.get("markers") or []:
            by_key.setdefault(k, c)
    flagged_acc_red = dec[name]["red"]; flagged_acc_chip = dec[name]["chip"]
    hard = dec[name]["hard"]
    # non-ACCURATE rows the tool passed (strict disagreement, "false support" broadly)
    passed_bad = [k for k in gt if k not in acc and k in by_key
                  and _collapse(_tool_bucket(by_key[k])) == "pass"]
    passed_bad_blindpass = [k for k in passed_bad if blind_sides(blind[k])[0] == "pass"]
    agrees = 100 - len(flagged_acc_red) - len(flagged_acc_chip) - len(passed_bad)
    print(f"{name:>14}: agrees {agrees} | flagged-ACC {len(flagged_acc_red)+len(flagged_acc_chip)} "
          f"(chips {len(flagged_acc_chip)}, red-blind-agrees {len(flagged_acc_red)-len(hard)}, HARD {len(hard)}) "
          f"| passed-nonACC {len(passed_bad)} (blind also passes {len(passed_bad_blindpass)}) "
          f"| passed-nonACC ids: {sorted(passed_bad)}")

print("\n=== Q3: pairwise hard-misfire overlap ===")
names = [n for n, _ in RUNS]
sets = {n: set(dec[n]["hard"]) for n in names}
for a_, b_ in itertools.combinations(names, 2):
    i = len(sets[a_] & sets[b_]); u = len(sets[a_] | sets[b_])
    print(f"{a_:>14} vs {b_:<14} shared {i:2d}  jaccard {i/u:.2f}")

print("\n=== consensus structure of the 21 misfire rows ===")
allh = sorted(set().union(*sets.values()))
cnt = Counter()
for k in allh:
    cnt[sum(k in sets[n] for n in names)] += 1
for n_arms in sorted(cnt, reverse=True):
    rows = [k for k in allh if sum(k in sets[m] for m in names) == n_arms]
    print(f"failed by {n_arms}/5 arms: {cnt[n_arms]} rows {rows}")

print("\n=== qwen OFF vs ON vs gemma_prod ===")
print("OFF minus ON :", sorted(sets['qwen37_nothink'] - sets['qwen37']))
print("ON minus OFF :", sorted(sets['qwen37'] - sets['qwen37_nothink']))
print("OFF minus gemma_prod:", sorted(sets['qwen37_nothink'] - sets['gemma_prod']))
print("OFF ∩ old    :", len(sets['qwen37_nothink'] & sets['old']), "of OFF", len(sets['qwen37_nothink']))
