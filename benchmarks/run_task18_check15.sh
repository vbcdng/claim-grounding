#!/usr/bin/env bash
# Task #18 step A (free gemma, $0): does the MERGED prompt still behave like the
# measured round-3 prompt? The merge = round-3 wording + task #1's dedup rule
# from config/prompts (commit ef628fc), so it is a prompt nobody has measured.
# 15 rows only (probes + wins + controls + watch), so this takes ~20 min, not 4h.
# PASS = every one of the 15 verdicts matches batch_dev_pilot100_run_task18_r3.
set -u
cd "$(dirname "$0")/.."
PY=venv/bin/python3
D=data/citation_integrity
OUT=$D/task18_check15_r4merged

mkdir -p "$OUT"
[ -d "$OUT/embeddings" ] || cp -r $D/batch_dev_pilot100_run_task18_r3/embeddings "$OUT/embeddings"

$PY benchmarks/verify_with_prompts_task18.py \
  benchmarks/prompt_variants/pt_task18_r4merged_combined_judgment.txt \
  benchmarks/prompt_variants/pt_task18_r4merged_component_split.txt \
  --text $D/my_text_task18_check15.md \
  --sources $D/batch_dev_pilot100/sources \
  --references $D/batch_dev_pilot100/my_text.md.refs.txt \
  --output-dir "$OUT" \
  --model gemini/gemma-4-31b-it --no-arbiter --concurrency 2 --yes

# Retry any dropped claims (a refused call silently becomes "unsupported" — #37).
$PY benchmarks/verify_with_prompts_task18.py \
  benchmarks/prompt_variants/pt_task18_r4merged_combined_judgment.txt \
  benchmarks/prompt_variants/pt_task18_r4merged_component_split.txt \
  --text $D/my_text_task18_check15.md \
  --sources $D/batch_dev_pilot100/sources \
  --references $D/batch_dev_pilot100/my_text.md.refs.txt \
  --output-dir "$OUT" \
  --model gemini/gemma-4-31b-it --no-arbiter --concurrency 2 --yes

echo "=== CONTAMINATION CHECK (must both be 0) ==="
grep -c '"no LLM response"' "$OUT/analysis.json" || true
grep -c 'SKIPPED without calling' "$OUT/analysis.json" || true

echo "=== MERGED vs ROUND 3, all 15 rows ==="
$PY - <<'EOF'
import json
def rows(d):
    claims = json.load(open(f"data/citation_integrity/{d}/analysis.json"))["text_claims"]
    out = {}
    for c in claims:
        for m in (c.get("markers") or []):
            out[m] = (c.get("verdict"), c.get("method"), bool(c.get("partial_support")),
                      c.get("proof_state", "-"))
    return out
r3 = rows("batch_dev_pilot100_run_task18_r3")
m4 = rows("task18_check15_r4merged")
bad = 0
for cid in sorted(m4):
    a, b = r3.get(cid), m4[cid]
    flag = "" if a == b else "   <-- DIFFERS"
    if a != b:
        bad += 1
    print(f"{cid}: r3 {a}  |  merged {b}{flag}")
print(f"\nrows compared: {len(m4)}   differing: {bad}")
print("RESULT:", "PASS — merge is behaviourally identical" if bad == 0 else
      "INVESTIGATE — the dedup rule interacts with the round-3 rules")
EOF
