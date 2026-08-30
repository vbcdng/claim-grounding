"""Task #18 loop: compare a round arm against the gemma_0802 baseline. Pure, $0.

Replays the hard-misfire definition (ci_hard_misfires.py) for two run dirs and
prints a per-row diff. Prints ids, labels, verdicts, methods and buckets ONLY —
never row text (Fable-safety). Writes nothing.

Usage: ci_task18_compare.py [round_run_dirname]   (default batch_dev_pilot100_run_task18_r1)
"""
import json, os, sys
ROOT = "/home/moje/Documents/python_projects/claim-grounding"
sys.path.insert(0, os.path.join(ROOT, "benchmarks"))
sys.path.insert(0, ROOT)
from citation_integrity_bench import MAJOR, _collapse
from wice_bench import _tool_bucket
from ci_blind_compare import sides as blind_sides

BASE = f"{ROOT}/data/citation_integrity"
gt = json.load(open(f"{BASE}/batch_dev_pilot100/ci_ground_truth.json"))["claims"]
blind = json.load(open(f"{BASE}/c0_blind_sonnet.json"))["votes"]

BASELINE = ("gemma_0802", "batch_dev_pilot100_run_gemma_0802")
ROUND = ("round", sys.argv[1] if len(sys.argv) > 1 else "batch_dev_pilot100_run_task18_r1")

TARGETS = ["cidev0007", "cidev0072", "cidev0009", "cidev0052", "cidev0045", "cidev0088"]
CONTROLS = ["cidev0044", "cidev0046", "cidev0047", "cidev0063", "cidev0074",
            "cidev0080", "cidev0086"]

acc_rows = sorted(k for k, g in gt.items() if g["label"] == "ACCURATE")
major_rows = sorted(k for k, g in gt.items() if g["label"] in MAJOR)

def index(dirname):
    a = json.load(open(f"{BASE}/{dirname}/analysis.json"))
    by_key = {}
    for c in a.get("text_claims", []):
        for k in c.get("markers") or []:
            by_key.setdefault(k, c)
    return by_key

def stats(by_key):
    red, chip = [], []
    for k in acc_rows:
        c = by_key.get(k)
        if c is None:
            continue
        b = _tool_bucket(c)
        if _collapse(b) == "pass":
            continue
        (red if c.get("verdict") != "supported" else chip).append(k)
    hard = [k for k in red if blind_sides(blind[k])[0] != "flag"]
    fs = [k for k in major_rows
          if by_key.get(k) is not None
          and _collapse(_tool_bucket(by_key[k])) == "pass"]
    return red, chip, hard, fs

runs = {}
for name, d in (BASELINE, ROUND):
    by_key = index(d)
    red, chip, hard, fs = stats(by_key)
    runs[name] = dict(by_key=by_key, red=red, chip=chip, hard=hard, fs=fs)
    print(f"=== {name} ({d}) ===")
    print(f"  red cards on ACCURATE rows: {len(red)}  ({' '.join(red)})")
    print(f"  hard misfires (blind reader also passed): {len(hard)}  ({' '.join(hard)})")
    print(f"  chips on ACCURATE rows: {len(chip)}")
    print(f"  major-error false-supports: {len(fs)}/{len(major_rows)}  ({' '.join(fs)})")
    print()

b, r = runs["gemma_0802"], runs["round"]

def verdict_of(run, k):
    c = run["by_key"].get(k)
    if c is None:
        return "ABSENT"
    v = c.get("verdict", "?")
    m = c.get("method", "?")
    bucket = _tool_bucket(c)
    return f"{v}/{m}/{bucket}"

print("=== target rows (must clear where gemma had them red) ===")
for k in TARGETS:
    mark = " <-- was red in baseline" if k in b["red"] else ""
    print(f"  {k}: baseline {verdict_of(b, k)}  ->  round {verdict_of(r, k)}{mark}")

print("\n=== control rows (author ruled red CORRECT; must stay red where red) ===")
for k in CONTROLS:
    mark = " <-- was red in baseline" if k in b["red"] else ""
    print(f"  {k}: baseline {verdict_of(b, k)}  ->  round {verdict_of(r, k)}{mark}")

cleared = [k for k in b["red"] if k not in r["red"]]
new_red = [k for k in r["red"] if k not in b["red"]]
new_fs = [k for k in r["fs"] if k not in b["fs"]]
fixed_fs = [k for k in b["fs"] if k not in r["fs"]]
print("\n=== diff vs baseline ===")
print(f"  red cleared: {len(cleared)}  ({' '.join(cleared)})")
print(f"  NEW red on ACCURATE rows: {len(new_red)}  ({' '.join(new_red)})")
print(f"  NEW major-error false-supports: {len(new_fs)}  ({' '.join(new_fs)})")
print(f"  major-error false-supports fixed: {len(fixed_fs)}  ({' '.join(fixed_fs)})")

flips = []
for k in sorted(set(list(gt))):
    cb, cr = b["by_key"].get(k), r["by_key"].get(k)
    if cb is None or cr is None:
        continue
    vb, vr = cb.get("verdict"), cr.get("verdict")
    if vb != vr:
        flips.append((k, gt[k]["label"], f"{vb}/{cb.get('method','?')}",
                      f"{vr}/{cr.get('method','?')}"))
print(f"\n=== every verdict flip ({len(flips)}) ===")
for k, label, was, now in flips:
    print(f"  {k} [{label}]: {was} -> {now}")
