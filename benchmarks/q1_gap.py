"""Q1: is the WiCE-vs-CitationIntegrity score gap real, or composition? Pure, $0."""
import json, sys, os
ROOT = "/home/moje/Documents/python_projects/claim-grounding"
sys.path.insert(0, os.path.join(ROOT, "benchmarks")); sys.path.insert(0, ROOT)
from citation_integrity_bench import MAJOR, _collapse
from wice_bench import _tool_bucket
BASE = f"{ROOT}/data/citation_integrity"
gt = json.load(open(f"{BASE}/batch_dev_pilot100/ci_ground_truth.json"))["claims"]
dec = json.load(open(f"{BASE}/c2_decomposition.json"))
RUNS = [("old", "batch_dev_pilot100_run"), ("qwen_on", "batch_dev_pilot100_run_qwen37"),
        ("gemma_v2", "batch_dev_pilot100_run_gemma_v2"),
        ("gemma_prod", "batch_dev_pilot100_run_gemma_prod"),
        ("qwen_off", "batch_dev_pilot100_run_qwen37_nothink")]
KEY = dict(old="old", qwen_on="qwen37", gemma_v2="gemma_v2",
           gemma_prod="gemma_prod", qwen_off="qwen37_nothink")
acc = {k for k, g in gt.items() if g["label"] == "ACCURATE"}
bad = set(gt) - acc

# --- WiCE test split, from the published results doc (358 rows) ---
W_PASS, W_PROB = 111, 247            # supported / (partial + not_supported)
W_OVERFLAG, W_OVERCRED = 51, 33      # of the 84 two-label strict misses
w_fa = W_OVERFLAG / W_PASS
w_oc = W_OVERCRED / W_PROB
print("=== WiCE held-out TEST split (358 rows, the honest comparison set) ===")
print(f"  composition: {W_PASS} pass ({W_PASS/358:.0%}) / {W_PROB} problem ({W_PROB/358:.0%})")
print(f"  false-alarm rate on pass rows : {w_fa:.1%}  ({W_OVERFLAG}/{W_PASS})")
print(f"  over-credit rate on problem rows: {w_oc:.1%}  ({W_OVERCRED}/{W_PROB})")
print(f"  published two-label strict: 274/358 = {274/358:.1%}")
print("  (the quoted 83.4% COMBINES this with the 154 refuted rows scored 99.4% —")
print("   refuted rows are pre-broken claims the tool trivially flags.)\n")

print("=== Citation-Integrity dev-100 (50 pass / 50 problem) ===")
rows = []
for name, d in RUNS:
    a = json.load(open(f"{BASE}/{d}/analysis.json"))
    by = {}
    for c in a.get("text_claims", []):
        for k in c.get("markers") or []:
            by.setdefault(k, c)
    D = dec[KEY[name]]
    flagged_acc = set(D["red"]) | set(D["chip"])
    chips = set(D["chip"])
    over_cred = {k for k in bad if k in by and _collapse(_tool_bucket(by[k])) == "pass"}
    # chips that HELP: problem rows caught only by a chip (verdict still supported)
    chip_catch = {k for k in bad if k in by and by[k].get("verdict") == "supported"
                  and _collapse(_tool_bucket(by[k])) == "flag"}
    fa = len(flagged_acc) / len(acc); oc = len(over_cred) / len(bad)
    raw = 100 - len(flagged_acc) - len(over_cred)
    # viewer-realistic: a soft chip is not a red card
    viewer = raw + len(chips) - len(chip_catch)
    # re-weight this arm to WiCE's composition
    reweighted = (W_PASS * (1 - fa) + W_PROB * (1 - oc)) / 358
    rows.append((name, raw, len(flagged_acc), len(chips), len(over_cred),
                 fa, oc, viewer, reweighted, len(chip_catch)))
    print(f"  {name:>11}: raw {raw}/100 | false-alarm {fa:.0%} ({len(flagged_acc)}/50, "
          f"of which chips {len(chips)}) | over-credit {oc:.0%} ({len(over_cred)}/50) "
          f"| chips that CATCH a bad row: {len(chip_catch)}")

print("\n=== the two corrections, applied ===")
print(f"{'arm':>11} | {'raw':>4} | {'chips not counted as red':>24} | {'re-weighted to WiCE mix':>23}")
for n, raw, fl, ch, oc_, fa, ocr, viewer, rw, cc in rows:
    print(f"{n:>11} | {raw:>4} | {viewer:>21}/100 | {rw*100:>20.1f}/100")
print(f"\nWiCE test split for reference: {274/358*100:.1f}/100 (two-label strict)")
print("If WiCE test had CI's 50/50 mix instead:",
      f"{(0.5*(1-w_fa)+0.5*(1-w_oc))*100:.1f}/100")
