#!/usr/bin/env bash
# Task #18 step A, final confirmation (free gemma, $0, ~5h unattended).
#
# Why this run exists. The 15-row check found the merged wording (round-3 wording +
# task #1's shipped de-duplication rule) differs from round 3 on exactly one verdict,
# and that row (cidev0020) turns out to be a four-citation row where the tool is
# handed only ONE of the four cited papers — an unfair question task #32 owns, and a
# row the loop record had already listed as the converter's fault. So the difference
# is not a real regression. BUT the diagnosed mechanism is general: the de-duplication
# rule makes the judge look harder for gaps at the moment it decides, which lowers
# leniency everywhere, not only on that row. The 15 rows cannot rule out that the same
# effect flips a FAIR row somewhere in the other 85. This run measures all 100 rows
# under the merged wording so the comparison against round 3 is complete.
#
# Pass mark, fixed before measuring: no row that the answer key calls accurate, and
# that asks a fair question (single-citation), may move from green to red versus
# batch_dev_pilot100_run_task18_r3. Verdict moves on multi-citation rows are reported
# separately and do not block, because those rows cannot be answered correctly until
# task #32 lands. Any new red card on a fair accurate row blocks promotion.
set -u
cd "$(dirname "$0")/.."
PY=venv/bin/python3
D=data/citation_integrity
OUT=$D/batch_dev_pilot100_run_task18_r4merged
R3=$D/batch_dev_pilot100_run_task18_r3

J=benchmarks/prompt_variants/pt_task18_r4merged_combined_judgment.txt
S=benchmarks/prompt_variants/pt_task18_r4merged_component_split.txt

mkdir -p "$OUT"
[ -d "$OUT/embeddings" ] || cp -r "$R3/embeddings" "$OUT/embeddings"

for pass in 1 2; do
  echo "=== pass $pass start $(date +%H:%M:%S) ==="
  $PY benchmarks/verify_with_prompts_task18.py "$J" "$S" \
    --text $D/batch_dev_pilot100/my_text.md \
    --sources $D/batch_dev_pilot100/sources \
    --references $D/batch_dev_pilot100/my_text.md.refs.txt \
    --output-dir "$OUT" \
    --model gemini/gemma-4-31b-it --no-arbiter --concurrency 2 --yes
  echo "=== pass $pass done $(date +%H:%M:%S) ==="
done

echo "=== CONTAMINATION CHECK (must be 0) ==="
grep -c '"no LLM response"' "$OUT/analysis.json" || true

echo
echo "=== MERGED vs ROUND 3 — every verdict move, split by fair/unfair question ==="
$PY - <<'EOF'
import json, sys
D = "data/citation_integrity"

sys.path.insert(0, "benchmarks"); sys.path.insert(0, ".")
from citation_integrity_bench import _row_co_citation

def rows(d):
    a = json.load(open(f"{D}/{d}/analysis.json"))
    out = {}
    for c in a["text_claims"]:
        for m in (c.get("markers") or []):
            out[m] = (c.get("verdict"), c.get("method"),
                      bool(c.get("partial_support")), c.get("proof_state", "-"))
    return out

r3 = rows("batch_dev_pilot100_run_task18_r3")
mg = rows("batch_dev_pilot100_run_task18_r4merged")

# Fairness and answer key come from the BENCHMARK's own records, which are
# authoritative. An earlier version of this block derived fairness from
# sibling_recovery.json instead; that file does not list every multi-cited row, so
# rows missing from it were printed as "unknown" with no answer-key label — and the
# blocker test below, which requires the label to be ACCURATE, could therefore never
# fire on them. A fair accurate row turning red would have been reported as a
# curiosity rather than as a failure. Fixed here; the 2026-08-11 result was
# re-verified against these records by hand and still passes (one verdict move in
# 100 rows, on a multi-cited row).
gt = json.load(open(f"{D}/batch_dev_pilot100/ci_ground_truth.json"))["claims"]

def fairness(k):
    g = gt.get(k)
    if g is None:
        return "NOT IN GROUND TRUTH", "?"
    single = _row_co_citation(g)[0]["is_single_cited"]
    return ("fair(1 citation)" if single else "UNFAIR(several citations, #32)"), g.get("label", "?")

blockers = []
print(f"{'row':<11} {'fairness':<26} {'key':<10} round3 -> merged")
for k in sorted(set(r3) & set(mg)):
    if r3[k] == mg[k]: continue
    fair, label = fairness(k)
    v_moved = r3[k][0] != mg[k][0]
    tag = ""
    if v_moved:
        tag = " <-- VERDICT MOVE"
        # Anything not positively known to be multi-cited counts as fair, so an
        # unclassifiable row fails the gate instead of slipping through it.
        if not fair.startswith("UNFAIR") and label != "MISSING" and mg[k][0] != "supported":
            tag += " *** BLOCKS PROMOTION ***"; blockers.append(k)
    print(f"{k:<11} {fair:<26} {label:<10} {r3[k]} -> {mg[k]}{tag}")

print()
print(f"rows compared: {len(set(r3) & set(mg))}")
print(f"BLOCKERS (fair + accurate + newly red): {len(blockers)} {blockers}")
print("RESULT:", "PASS — promote" if not blockers else "FAIL — fix the wording first")
EOF
