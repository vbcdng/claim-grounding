#!/usr/bin/env bash
# Task #18 step A fix candidate (free gemma, $0). The diff3 probes proved the first
# merged wording deterministically flips cidev0020 to red (3/3 both arms stable), so
# the merge is broken, not noisy. Candidate B keeps task #1's two behaviors (each
# missing fact listed once; no parenthetical explanations) but (1) moves the rule
# AFTER the "supported => empty list" consistency rule, (2) frames it as formatting
# of the FINISHED list that never changes which facts are listed or the decision,
# and (3) drops the "appears to be"/"is" example that reads as license to treat the
# source's hedged wording as a gap. This runs the same 3 rows x 3 repeats under
# candidate B and prints all three arms side by side (r3 and merged results are
# already on disk from run_task18_diff3_probes.sh).
#
# Pass mark: 0020 supported+chip/full 3/3 (matches r3). 0072/0099 matching either
# stable arm is acceptable (display-level); a NEW third state = investigate.
set -u
cd "$(dirname "$0")/.."
PY=venv/bin/python3
D=data/citation_integrity

B_J=benchmarks/prompt_variants/pt_task18_r4mergedB_combined_judgment.txt
B_S=benchmarks/prompt_variants/pt_task18_r4mergedB_component_split.txt

for i in 1 2 3; do
  out=$D/task18_diff3_mergedB_$i
  mkdir -p "$out"
  [ -d "$out/embeddings" ] || cp -r $D/batch_dev_pilot100_run_task18_r3/embeddings "$out/embeddings"
  echo "=== arm=mergedB repeat=$i start $(date +%H:%M:%S) ==="
  $PY benchmarks/verify_with_prompts_task18.py "$B_J" "$B_S" \
    --text $D/my_text_task18_diff3.md \
    --sources $D/batch_dev_pilot100/sources \
    --references $D/batch_dev_pilot100/my_text.md.refs.txt \
    --output-dir "$out" \
    --model gemini/gemma-4-31b-it --no-arbiter --concurrency 2 --yes
  # identical second pass = retry for anything the free seat dropped
  $PY benchmarks/verify_with_prompts_task18.py "$B_J" "$B_S" \
    --text $D/my_text_task18_diff3.md \
    --sources $D/batch_dev_pilot100/sources \
    --references $D/batch_dev_pilot100/my_text.md.refs.txt \
    --output-dir "$out" \
    --model gemini/gemma-4-31b-it --no-arbiter --concurrency 2 --yes
  echo "=== arm=mergedB repeat=$i done $(date +%H:%M:%S) ==="
done

echo
echo "=== RESULT: three rows x three repeats x three prompt versions ==="
$PY - <<'EOF'
import json, os
D = "data/citation_integrity"
rows = ["cidev0020", "cidev0072", "cidev0099"]
for arm in ("r3", "merged", "mergedB"):
    print(f"\n--- {arm} prompt ---")
    for cid in rows:
        cells, refused = [], 0
        for i in (1, 2, 3):
            p = f"{D}/task18_diff3_{arm}_{i}/analysis.json"
            if not os.path.exists(p):
                cells.append("MISSING"); continue
            d = json.load(open(p))
            refused += open(p).read().count('"no LLM response"')
            c = next((c for c in d["text_claims"] if cid in (c.get("markers") or [])), None)
            cells.append("row-not-found" if c is None else
                         f"{c.get('verdict')}{'+chip' if c.get('partial_support') else ''}"
                         f"/{c.get('proof_state','-')}")
        agree = "STABLE" if len(set(cells)) == 1 else "FLIPS"
        print(f"  {cid}: {' | '.join(cells)}   -> {agree}"
              + (f"   [{refused} refused calls]" if refused else ""))
EOF
