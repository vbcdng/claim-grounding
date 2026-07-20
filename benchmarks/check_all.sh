#!/usr/bin/env bash
# Ship-gate: score every hand-audited paper against its ground truth.
# No prompt/matcher/config change ships unless this passes on FRESH runs of all
# three papers (owner rule 2026-07-04, extended to 3 papers 2026-07-04 after the
# bentonite+chimpanzee generalization check — see docs/GENERALIZATION_CHECK.md).
# The scorer itself makes no API calls; it reads existing analysis.json files.
# Run the three verifications first (paper1 needs --full for a true fresh score).
set -u
cd "$(dirname "$0")/.."
PY=${PY:-venv/bin/python3}
rc=0

# Staleness check (2026-07-17): the scorer reads whatever analysis.json is on
# disk, so a "gate clean" after a matcher/prompt change is only meaningful if
# the runs POSTDATE the change (the 07-12 subject guard "passed" against
# pre-guard outputs and its t27 regression surfaced 5 days later —
# docs/SUBJECT_GUARD.md addendum 2). Warn, never fail: a stale run may be
# legitimately reusable when the change can't affect it.
LAST_CHANGE=$(git log -1 --format=%ct -- modules/papertrail/matcher.py config/prompts 2>/dev/null || echo 0)
stale_warn() {  # <analysis>
  if [ -f "$1" ] && [ "$LAST_CHANGE" -gt 0 ] \
     && [ "$(stat -c %Y "$1")" -lt "$LAST_CHANGE" ]; then
    echo "WARNING: $1 predates the last matcher/prompt change — re-run for a meaningful gate."
  fi
}

score() {  # <name> <analysis> <ground-truth>
  echo "===== $1 ====="
  if [ ! -f "$2" ]; then
    echo "MISSING analysis: $2 — run the verifier for $1 first (see docs/GENERALIZATION_CHECK.md)"
    rc=1; echo; return
  fi
  stale_warn "$2"
  "$PY" benchmarks/regression_check.py --analysis "$2" --ground-truth "$3" || rc=1
  echo
}

score paper1     data/paper1_verification/analysis.json  benchmarks/paper1_ground_truth.json
score bentonite  data/bentonite_verification/analysis.json benchmarks/bentonite_ground_truth.json
score chimp      data/chimp_verification/analysis.json   benchmarks/chimpanzee_ground_truth.json

# Coverage gate v2 (owner evidence standard; triple-confirmed 2026-07-11): the
# 3 papers above only prove verdicts didn't move; these score the covering-set
# output against ground truths where grader + Fable + owner all agree
# (docs/GATE_V2_CANDIDATES.md) — must-cover rows must show the quoted proof,
# must-flag rows must stay amber/unsupported (over-claiming = fail).
# Each runs in its OWN scratch dir (owner rule 2026-07-11: round dirs are
# immutable snapshots once their table exists — never re-run into them).
# GATE RUNS ARE PINNED --no-arbiter (2026-07-14, arbiter became default-on):
# the gate scores the frozen judge core; the arbiter tier is additive, has its
# own validation battery (MODEL_SWAP_PROTOCOL §6a + arbval_* runs), and its
# amber resolution could legitimately clear a must-flag row's amber, which the
# gate would misread as over-claiming.
# Fresh runs first (all --full --no-arbiter):
# venv/bin/python3 verify_my_text.py --text data/loop_rounds/round_1/project/my_text.md \
#   --sources data/loop_rounds/round_1/project/sources --output-dir data/coverage_gate_run --yes --full --no-arbiter
# venv/bin/python3 verify_my_text.py --text data/loop_rounds/round_3/project/my_text.md \
#   --sources data/loop_rounds/round_3/project/sources --output-dir data/gate_run_bohemia --yes --full --no-arbiter
# venv/bin/python3 verify_my_text.py --text data/loop_rounds/round_4/project/my_text.md \
#   --sources data/loop_rounds/round_4/project/sources --output-dir data/gate_run_pots --yes --full --no-arbiter
cover() {  # <name> <analysis> <ground-truth>
  echo "===== coverage: $1 ====="
  if [ ! -f "$2" ]; then
    echo "MISSING analysis: $2 — run verify_my_text.py for $1 first (commands in the comment above)"
    rc=1; echo; return
  fi
  stale_warn "$2"
  "$PY" benchmarks/coverage_check.py --analysis "$2" --ground-truth "$3" || rc=1
  echo
}

cover essay   data/coverage_gate_run/analysis.json  benchmarks/coverage_ground_truth_essay.json
cover bohemia data/gate_run_bohemia/analysis.json   benchmarks/coverage_ground_truth_bohemia.json
cover pots    data/gate_run_pots/analysis.json      benchmarks/coverage_ground_truth_pots.json

if [ $rc -eq 0 ]; then echo "ALL PAPERS OK — no regressions."; else echo "REGRESSION or missing run — do not ship."; fi
exit $rc
