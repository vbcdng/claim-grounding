#!/usr/bin/env bash
# Task #18 step A-follow-up (free gemma, $0): the 15-row merged-prompt check came
# back 12/15 identical, 3 differing — cidev0020 (a good citation round 3 had cleared
# to green) is red again, plus two display-only differences on 0072 and 0099.
# One observation per arm cannot tell a real effect from run-to-run variation, so
# this runs the SAME three rows three times under EACH prompt: the merged wording
# and the measured round-3 wording. ~6 short runs.
#
# Reading the result:
#   - 0020 red 3/3 merged AND green 3/3 r3  -> the merge really broke it (task #1's
#     dedup sentence interacts with the round-3 rules); fix before promoting.
#   - 0020 mixed in EITHER arm               -> the row is a flip-flopper and round
#     3's "win" on it was never solid; report it as such and do not credit it.
#   - 0072/0099 differences stable           -> display-level only (chip / proof
#     badge), no verdict moves; note and proceed.
set -u
cd "$(dirname "$0")/.."
PY=venv/bin/python3
D=data/citation_integrity

run() {  # <arm> <judge-prompt> <split-prompt> <repeat>
  out=$D/task18_diff3_$1_$4
  mkdir -p "$out"
  [ -d "$out/embeddings" ] || cp -r $D/batch_dev_pilot100_run_task18_r3/embeddings "$out/embeddings"
  echo "=== arm=$1 repeat=$4 start $(date +%H:%M:%S) ==="
  $PY benchmarks/verify_with_prompts_task18.py "$2" "$3" \
    --text $D/my_text_task18_diff3.md \
    --sources $D/batch_dev_pilot100/sources \
    --references $D/batch_dev_pilot100/my_text.md.refs.txt \
    --output-dir "$out" \
    --model gemini/gemma-4-31b-it --no-arbiter --concurrency 2 --yes
  # identical second pass = retry for anything the free seat dropped
  $PY benchmarks/verify_with_prompts_task18.py "$2" "$3" \
    --text $D/my_text_task18_diff3.md \
    --sources $D/batch_dev_pilot100/sources \
    --references $D/batch_dev_pilot100/my_text.md.refs.txt \
    --output-dir "$out" \
    --model gemini/gemma-4-31b-it --no-arbiter --concurrency 2 --yes
  echo "=== arm=$1 repeat=$4 done $(date +%H:%M:%S) ==="
}

M_J=benchmarks/prompt_variants/pt_task18_r4merged_combined_judgment.txt
M_S=benchmarks/prompt_variants/pt_task18_r4merged_component_split.txt
R_J=benchmarks/prompt_variants/pt_task18_r3_combined_judgment.txt
R_S=benchmarks/prompt_variants/pt_task18_r3_component_split.txt

for i in 1 2 3; do run merged "$M_J" "$M_S" "$i"; done
for i in 1 2 3; do run r3     "$R_J" "$R_S" "$i"; done

echo
echo "=== RESULT: three rows x three repeats x two prompt versions ==="
$PY - <<'EOF'
import json, os
D = "data/citation_integrity"
rows = ["cidev0020", "cidev0072", "cidev0099"]
for arm in ("r3", "merged"):
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
