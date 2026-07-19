#!/usr/bin/env bash
# Reproduce the held-out WiCE evaluation end to end with your own API key.
# Pre-registered plan: docs/WICE_HELDOUT_PREREG_2026-07-19.md
# Our result:          docs/WICE_HELDOUT_RESULTS_2026-07-19.md
#
# Cost: roughly $1.40 with the default models (Gemini flash-lite judge +
# DeepSeek arbiter). Runtime: our run took ~1 hour with three batches in
# parallel; this script runs them sequentially, so expect ~2-3 hours (it
# skips finished batches, so you can also just launch it in 2-3 terminals
# at once and they will divide the work). The free claude-code backend is
# NOT recommended here: ~34 min per 8 claims with Haiku means ~1.5 days of
# continuous runtime for 512 claims (Sonnet a multiple of that), plan
# usage limits will likely interrupt, and the judge models differ from the
# published configuration so the numbers would not be comparable anyway.
#
# Prerequisites:
#   1. Setup from README.md (venv + requirements).
#   2. The WiCE dataset (not redistributed here). From
#      https://github.com/ryokamoi/wice copy
#      data/entailment_retrieval/claim/{train,dev,test}.jsonl
#      into this repository's data/wice/ directory.
#   3. A Gemini API key in config/google_api_key.txt.
#      Optional: a DeepSeek key in config/deepseek_api_key.txt (without it
#      the arbiter layer is skipped with a warning and only the strict
#      layer is comparable).
#
# To only RE-SCORE our stored runs (no API key, seconds), skip this script:
#   for d in benchmarks/wice_heldout/*_b*/; do
#     venv/bin/python3 benchmarks/wice_bench.py score \
#       --analysis "$d/analysis.json" --ground-truth "$d/wice_ground_truth.json"
#   done
set -euo pipefail
cd "$(dirname "$0")/.."
PY=venv/bin/python3
ROOT=data/wice_heldout_repro

[ -f data/wice/test.jsonl ] || { echo "Missing data/wice/test.jsonl — see prerequisites in this script's header."; exit 1; }

echo "== converting: full test split (batches of 26) =="
$PY benchmarks/wice_bench.py convert --split test --all --batch-size 26 \
    --output-root "$ROOT/test"

echo "== converting: unused refuted train rows =="
$PY benchmarks/wice_bench.py convert --split train --all --label not_supported \
    --exclude-used --batch-size 26 --output-root "$ROOT/refuted"

for B in "$ROOT"/test_b* "$ROOT"/refuted_b*; do
    [ -d "$B" ] || continue
    RUN="${B}_run"
    if [ -f "$RUN/analysis.json" ]; then
        echo "== $B already run, skipping =="
        continue
    fi
    echo "== running the tool on $B =="
    $PY verify_my_text.py \
        --text "$B/my_text.md" --sources "$B/sources" --output-dir "$RUN" \
        --model gemini/gemini-2.5-flash-lite --full --yes
    echo "== scoring $B =="
    $PY benchmarks/wice_bench.py score \
        --analysis "$RUN/analysis.json" --ground-truth "$B/wice_ground_truth.json" \
        | tee "${B}_score.txt" || true   # the refuted set MAY trip the false-support alarm (exit 1); keep going
done

echo "== totals =="
$PY - "$ROOT" << 'EOF'
import re, sys, glob
root = sys.argv[1]
tot = {}
for group in ('test', 'refuted'):
    s = a = n = fs_b = fs_a = 0
    for f in sorted(glob.glob(f'{root}/{group}_b*_score.txt')):
        t = open(f).read()
        m = re.search(r'verdict-level agreement: (\d+)/(\d+)', t)
        s += int(m.group(1)); n += int(m.group(2))
        a += int(re.search(r'arbiter-adjudicated agreement: (\d+)/', t).group(1))
        fm = re.search(r'false-supports on \d+ refuted rows: base=(\d+), adjudicated=(\d+)', t)
        if fm: fs_b += int(fm.group(1)); fs_a += int(fm.group(2))
    print(f'{group:8} strict {s}/{n} ({100*s/n:.0f}%)   adjudicated {a}/{n} ({100*a/n:.0f}%)   '
          f'false-supports base={fs_b} adjudicated={fs_a}')
EOF
echo "Compare with docs/WICE_HELDOUT_RESULTS_2026-07-19.md (exact per-batch table inside)."
