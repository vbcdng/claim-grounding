#!/usr/bin/env bash
# Compare two ship-gate arms row by row, using the REAL scorers (no reimplemented
# scoring logic — it runs regression_check.py / coverage_check.py once per arm and
# diffs their output). Born 2026-08-11: the gemma gate FAILS on the currently
# shipped prompts (task #1's baseline: paper1 t17 + essay t6/t8/t10), so "the gate
# passes" is not an available pass mark for a branch change. The honest mark is
# "breaks no row that passes in the baseline", which is what this prints — and
# since the author's 2026-08-11 rulings (vault page "Problem rows to rule on
# (gate 2026-08-11)", question A-2) that IS the official pass definition on
# gemma: known-red rows on the unchanged tool are excused for every branch
# until their fix tasks land; any NEW red row still fails.
#
#   bash benchmarks/compare_gate_arms.sh <baseline-tag-or-canonical> <arm-tag>
#
# e.g. bash benchmarks/compare_gate_arms.sh canonical task18
#      (canonical = the six standard folders, where task #1's gemma baseline sits;
#       task18 = data/gate_task18_* written by run_gate_gemma.sh task18)
#
# Makes ZERO API calls. Writes nothing except a scratch scoring transcript.
set -u
cd "$(dirname "$0")/.."
PY=${PY:-venv/bin/python3}

if [ $# -lt 2 ]; then
  grep '^#' "$0" | sed 's/^# \{0,1\}//'
  echo "ERROR: need <baseline-tag> <arm-tag>" >&2
  exit 2
fi
BASE=$1
ARM=$2
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

dir() {  # <tag> <name>
  case "$1:$2" in
    canonical:paper1)    echo data/paper1_verification ;;
    canonical:bentonite) echo data/bentonite_verification ;;
    canonical:chimp)     echo data/chimp_verification ;;
    canonical:essay)     echo data/coverage_gate_run ;;
    canonical:bohemia)   echo data/gate_run_bohemia ;;
    canonical:pots)      echo data/gate_run_pots ;;
    *)                   echo "data/gate_$1_$2" ;;
  esac
}

score_arm() {  # <tag> <outfile>
  local tag=$1 out=$2
  : > "$out"
  for pair in "paper1:regression_check.py:paper1_ground_truth.json" \
              "bentonite:regression_check.py:bentonite_ground_truth.json" \
              "chimp:regression_check.py:chimpanzee_ground_truth.json" \
              "essay:coverage_check.py:coverage_ground_truth_essay.json" \
              "bohemia:coverage_check.py:coverage_ground_truth_bohemia.json" \
              "pots:coverage_check.py:coverage_ground_truth_pots.json"; do
    IFS=':' read -r name scorer gt <<< "$pair"
    d=$(dir "$tag" "$name")
    echo "##### $name" >> "$out"
    if [ -f "$d/analysis.json" ]; then
      $PY "benchmarks/$scorer" --analysis "$d/analysis.json" \
          --ground-truth "benchmarks/$gt" >> "$out" 2>&1 || true
    else
      echo "MISSING $d/analysis.json" >> "$out"
    fi
  done
}

echo "=== scoring baseline arm '$BASE' ==="; score_arm "$BASE" "$TMP/base.txt"
echo "=== scoring change arm  '$ARM'  ==="; score_arm "$ARM"  "$TMP/arm.txt"

echo
echo "=== ROWS THAT CHANGED VERDICT OR PASS/FAIL (baseline '<' vs arm '>') ==="
diff "$TMP/base.txt" "$TMP/arm.txt" | grep -E "^[<>]" | grep -vE "^[<>] *$" || echo "  (no differences at all)"

echo
echo "=== THE PASS MARK: rows passing in the baseline that FAIL in the arm ==="
# A failing row is printed by either scorer as "FAIL <id>" or "✗ <id>". Row ids
# REPEAT across the six texts (paper1 t6 and essay t6 are different rows), so each
# id must be qualified with its text or a regression in one text can be cancelled
# out by a pass in another — the same collision class as the benchmark row-id bug.
fails() {
  awk '
    /^##### /      { section = $2; next }
    /FAIL +t[0-9]+/ { match($0, /t[0-9]+/); print section "/" substr($0, RSTART, RLENGTH); next }
    /✗ +t[0-9]+/    { match($0, /t[0-9]+/); print section "/" substr($0, RSTART, RLENGTH); next }
  ' "$1" | sort -u
}
comm -13 <(fails "$TMP/base.txt") <(fails "$TMP/arm.txt") > "$TMP/new_fails.txt"
if [ -s "$TMP/new_fails.txt" ]; then
  echo "  REGRESSION — the arm breaks rows the baseline passes:"
  sed 's/^/    /' "$TMP/new_fails.txt"
  rc=1
else
  echo "  none — the arm breaks nothing the baseline passes"
  rc=0
fi

echo
echo "=== rows failing in the baseline that the arm FIXES (bonus, not required) ==="
comm -23 <(fails "$TMP/base.txt") <(fails "$TMP/arm.txt") | sed 's/^/    /' || true

echo
echo "=== EVERY CLAIM WHOSE VERDICT DIFFERS BETWEEN THE ARMS (scorer-independent) ==="
# The sections above only see rows the answer keys cover. This reads the runs
# themselves, so a moved verdict on an unscored row (e.g. the paper1 watch rows)
# cannot hide. Ids are qualified by text, because they repeat across the six texts.
$PY - "$BASE" "$ARM" <<'PYEOF'
import json, os, sys
base_tag, arm_tag = sys.argv[1], sys.argv[2]
CANON = {"paper1": "data/paper1_verification", "bentonite": "data/bentonite_verification",
         "chimp": "data/chimp_verification", "essay": "data/coverage_gate_run",
         "bohemia": "data/gate_run_bohemia", "pots": "data/gate_run_pots"}

def d(tag, name):
    return CANON[name] if tag == "canonical" else f"data/gate_{tag}_{name}"

def rows(tag, name):
    p = f"{d(tag, name)}/analysis.json"
    if not os.path.exists(p):
        return None
    out = {}
    for c in json.load(open(p)).get("text_claims", []):
        if c.get("id"):
            out[c["id"]] = (c.get("verdict"), bool(c.get("partial_support")),
                            c.get("proof_state", "-"))
    return out

total = 0
for name in CANON:
    b, a = rows(base_tag, name), rows(arm_tag, name)
    if b is None or a is None:
        print(f"  {name}: MISSING a run ({'baseline' if b is None else 'arm'}) — cannot compare")
        continue
    moved = [k for k in sorted(set(b) & set(a), key=lambda x: (len(x), x)) if b[k][0] != a[k][0]]
    disp = [k for k in sorted(set(b) & set(a), key=lambda x: (len(x), x))
            if b[k][0] == a[k][0] and b[k] != a[k]]
    only_b, only_a = sorted(set(b) - set(a)), sorted(set(a) - set(b))
    print(f"  {name}: {len(b)} vs {len(a)} claims | verdict moves {len(moved)} | display-only {len(disp)}"
          + (f" | only in baseline {only_b}" if only_b else "")
          + (f" | only in arm {only_a}" if only_a else ""))
    for k in moved:
        print(f"      VERDICT  {name}/{k}: {b[k][0]} -> {a[k][0]}")
    for k in disp:
        print(f"      display  {name}/{k}: {b[k]} -> {a[k]}")
    total += len(moved)
print(f"\n  total verdict moves across all six texts: {total}")
PYEOF

echo
echo "=== COMPARE EXIT=$rc  (0 = no regression vs baseline) ==="
exit $rc
