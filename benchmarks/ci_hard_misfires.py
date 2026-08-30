"""Hard-misfire decomposition for a Citation-Integrity pilot batch.

The benchmark's pass/flag line scores a soft "partial support?" chip on a
supported verdict exactly like a rejection; the viewer does not. So a raw
false-flag count overstates what a reader would call a false alarm. This splits
the flagged ACCURATE rows into red cards (verdict unsupported) and chips, and
crosses each with the blind reader's independent vote. A HARD MISFIRE is a red
card on a row the blind reader also passed — the metric the model swap is scored
on. It also counts false-supports on major-error rows, the safety side.

Pure: no API calls, no network. Every pass/flag definition is imported from the
bench modules rather than re-implemented, so this can never drift from them.

    venv/bin/python3 benchmarks/ci_hard_misfires.py
"""
import json, os, sys
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))
from citation_integrity_bench import MAJOR, _collapse
from wice_bench import _tool_bucket
from ci_blind_compare import sides as blind_sides
from ci_batch_ids import batch_tag, qualify, qualify_all

BASE = os.path.join(os.path.dirname(_HERE), "data", "citation_integrity")
BATCH = "batch_dev_pilot100"
TAG = batch_tag(BATCH)
gt = json.load(open(f"{BASE}/{BATCH}/ci_ground_truth.json"))["claims"]
blind = json.load(open(f"{BASE}/c0_blind_sonnet.json"))["votes"]

RUNS = [("old", "batch_dev_pilot100_run"),
        ("qwen37", "batch_dev_pilot100_run_qwen37"),
        ("gemma_v2", "batch_dev_pilot100_run_gemma_v2"),
        ("gemma_prod", "batch_dev_pilot100_run_gemma_prod"),
        ("qwen37_nothink", "batch_dev_pilot100_run_qwen37_nothink"),
        ("gemma_0802", "batch_dev_pilot100_run_gemma_0802")]

acc_rows = sorted(k for k, g in gt.items() if g["label"] == "ACCURATE")
major_rows = sorted(k for k, g in gt.items() if g["label"] in MAJOR)
print(f"ACCURATE rows: {len(acc_rows)} | major-error rows: {len(major_rows)}\n")

out = {}
for name, d in RUNS:
    a = json.load(open(f"{BASE}/{d}/analysis.json"))
    by_key = {}
    for c in a.get("text_claims", []):
        for k in c.get("markers") or []:
            by_key.setdefault(k, c)

    red = []          # verdict unsupported on an ACCURATE row
    chip = []         # supported but flagged by chip (partial etc.)
    for k in acc_rows:
        c = by_key.get(k)
        if c is None:
            continue
        b = _tool_bucket(c)
        if _collapse(b) == "pass":
            continue
        (red if c.get("verdict") != "supported" else chip).append(k)

    def cross(keys):
        agree = [k for k in keys if blind_sides(blind[k])[0] == "flag"]
        return len(agree), [k for k in keys if k not in agree]

    r_ag, r_dis = cross(red)
    c_ag, c_dis = cross(chip)
    fs = [k for k in major_rows
          if by_key.get(k) is not None
          and _collapse(_tool_bucket(by_key[k])) == "pass"]
    out[name] = dict(red=red, chip=chip, hard=r_dis, fs=fs)
    print(f"=== {name} ===")
    print(f"  ACCURATE flagged: {len(red)+len(chip)}  "
          f"(red cards {len(red)}: blind agrees {r_ag} / HARD MISFIRES {len(r_dis)}; "
          f"chips {len(chip)}: blind agrees {c_ag} / disagrees {len(c_dis)})")
    print(f"  hard misfires: {qualify_all(TAG, sorted(r_dis))}")
    print(f"  major-error false-supports: {len(fs)}/{len(major_rows)} "
          f"{qualify_all(TAG, sorted(fs))}")
    print()

# overlap of hard misfires across runs
allh = sorted(set().union(*[set(v["hard"]) for v in out.values()]))
print(f"=== hard-misfire matrix, batch {TAG} (rows = any run's hard misfire) ===")
print(f"{'id':<22}" + "  ".join(f"{n:>10}" for n, _ in RUNS))
for k in allh:
    print(f"{qualify(TAG, k):<22}"
          + "  ".join(f"{'X' if k in out[n]['hard'] else '.':>10}" for n, _ in RUNS))
# ids inside are within-batch (that is what the run dirs and analysis.json use);
# `_batch` says which batch they belong to, so a consumer can qualify them.
json.dump(dict(out, _batch=TAG), open(f"{BASE}/c2_decomposition.json", "w"), indent=1)
print(f"\nwritten -> {BASE}/c2_decomposition.json")
