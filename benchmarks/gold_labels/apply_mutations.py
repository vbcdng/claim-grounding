#!/usr/bin/env python3
"""Mutation bench: apply minimal single-fact corruptions to supported eggs claims.
Ground truth = each mutated claim SHOULD no longer read as cleanly supported
(source contradicts the new value). We measure how many the pipeline catches."""
import json, sys, pathlib

# Usage: apply_mutations.py <workdir> — the mutation-bench scratch dir with
# eggs_mut/my_text.md (see docs/MUTATION_BENCH_PLAN.md and mutation_score.py).
if len(sys.argv) != 2:
    sys.exit("usage: apply_mutations.py <workdir>")
SCR = pathlib.Path(sys.argv[1])
txt_path = SCR / "eggs_mut" / "my_text.md"
text = txt_path.read_text()

# (claim_id, kind, old, new)  -- each `old` must be unique in the text
MUTATIONS = [
    ("t8",  "negate",   "the liver compensates by downregulating its own synthesis",
                        "the liver compensates by upregulating its own synthesis"),
    ("t17", "num_10x",  "covering more than three million person-years",
                        "covering more than thirty million person-years"),
    ("t18", "flip_dir", "carried no excess cardiovascular risk (hazard ratio 0.93)",
                        "carried substantial excess cardiovascular risk (hazard ratio 1.93)"),
    ("t20", "flip_dir", "actually had a *lower* risk of cardiovascular disease than non-consumers (hazard ratio 0.89)",
                        "actually had a *higher* risk of cardiovascular disease than non-consumers (hazard ratio 1.89)"),
    ("t22", "num",      "associated with a 17% higher risk of incident cardiovascular disease (HR 1.17)",
                        "associated with a 70% higher risk of incident cardiovascular disease (HR 1.70)"),
    ("t29", "num",      "a 42% higher risk of type 2 diabetes (relative risk 1.42)",
                        "a 22% higher risk of type 2 diabetes (relative risk 1.22)"),
    ("t30", "num",      "coronary heart disease risk 54% higher (RR 1.54)",
                        "coronary heart disease risk 154% higher (RR 2.54)"),
    ("t39", "num",      "capped cholesterol at 300 mg/day, about one and a half eggs",
                        "capped cholesterol at 500 mg/day, about two and a half eggs"),
]

manifest = []
for cid, kind, old, new in MUTATIONS:
    n = text.count(old)
    if n != 1:
        print(f"ERROR {cid}: old string count={n} (need 1)"); sys.exit(1)
    text = text.replace(old, new)
    manifest.append({"claim": cid, "kind": kind, "old": old, "new": new})

txt_path.write_text(text)
(SCR / "mutation_manifest.json").write_text(json.dumps(manifest, indent=2))
print(f"applied {len(manifest)} mutations to {txt_path}")
for m in manifest:
    print(f"  {m['claim']:4} [{m['kind']}]")
