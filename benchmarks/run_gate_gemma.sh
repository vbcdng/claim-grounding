#!/usr/bin/env bash
# THE shared free-gemma ship-gate runner. Use this — do not write your own.
#
# Runs all SIX gate texts on the FREE Google-direct gemma judge (two-key
# rotation, $0), retries anything the free seat dropped, checks the runs for
# refused calls, then scores them. Born 2026-08-11 by folding task #18's three
# additions into task #1's run_gate_gemma.sh (author's instruction) so both
# tasks and every later one share one script.
#
#   bash benchmarks/run_gate_gemma.sh <tag> [concurrency]
#
#   <tag> = canonical   write into the tool's standard result folders
#                       (data/paper1_verification, data/bentonite_verification,
#                       data/chimp_verification, data/coverage_gate_run,
#                       data/gate_run_bohemia, data/gate_run_pots) and score
#                       with benchmarks/check_all.sh. Use this ONLY when you
#                       intend to replace those folders — back them up first,
#                       and never while another session's gate is running.
#   <tag> = anything    write into data/gate_<tag>_{paper1,bentonite,chimp,
#                       essay,bohemia,pots} and score those files directly.
#                       This is the safe default for a branch-level gate: it
#                       cannot overwrite another arm's results, and the two
#                       arms stay comparable side by side.
#   [concurrency]       default 2. Two is deliberate: at 4 the free seat
#                       dropped 4 of 100 claims (task #18 round 3) and a
#                       dropped claim is silently recorded as "unsupported"
#                       (task #37), which on a gate is a fake failure.
#
#   SCORE_ONLY=1        skip the runs, score whatever is already on disk.
#   RESUME=0            re-judge every text even if it already finished
#                       before. Default 1: re-running the SAME tag skips
#                       a text whose earlier run finished both passes under
#                       the same code commit, model, prompts, EXTRA_FLAGS,
#                       text and sources, with no refused calls — so a crash,
#                       restart or sleep costs only the unfinished texts. A
#                       fresh tag has nothing recorded and runs everything.
#                       The refused-call check and scoring still read every
#                       text. Logic: benchmarks/gate_resume.py.
#   REUSE_FROM=<tag>    (default off) serve every request that is
#                       byte-for-byte identical to one the earlier arm <tag>
#                       already asked (same model, prompt, temperature, output
#                       limit, provider settings) from that arm's recorded
#                       answer, and ask Gemma only the rest. Measured offline:
#                       93-100% of calls repeat between two arms that differ
#                       by one switch. Failed answers are never reused; every
#                       reused answer is marked in llm_calls.jsonl and the
#                       summary prints "reused N of M calls" per text. Refused
#                       together with STABILITY_RUN=1 or --verdict-vote in
#                       EXTRA_FLAGS (a stability measurement needs fresh
#                       answers), and refused when <tag> is this tag. Only arms
#                       recorded with request fingerprints can donate (every
#                       arm since this switch existed; RECORD_REQUESTS below).
#   RECORD_REQUESTS=1   default: write each request's fingerprint into
#                       llm_calls.jsonl so this arm can serve a later REUSE_FROM.
#                       Changes nothing sent to the model and no result file.
#                       0 = log exactly as before the answer-reuse switch existed.
#   STABILITY_RUN=1     mark this arm as a verdict-stability measurement, so
#                       REUSE_FROM is refused.
#   MODEL=<litellm id>  override the judge (default gemini/gemma-4-31b-it).
#   PROMPTS="name=path[,name=path]"
#                       gate a PROMPT VARIANT without editing config/prompts/.
#                       Routes the runs through benchmarks/verify_with_prompts.py,
#                       which sets matcher.PROMPT_OVERRIDES in-process, so the
#                       shared prompt files are never written and no other
#                       session's in-flight run is disturbed. Use this instead of
#                       copying a variant over config/prompts/ to gate it — that
#                       is the hazard CLAUDE.md warns about (matcher re-opens the
#                       file on every call, so an edit mid-run silently swaps the
#                       instructions and the output still looks normal).
#                       Example:
#                         PROMPTS="pt_combined_judgment_prompt.txt=benchmarks/prompt_variants/x.txt" \
#                           bash benchmarks/run_gate_gemma.sh mytag
#
# GATE RUNS ARE PINNED --no-arbiter (check_all.sh comment, 2026-07-14): the gate
# scores the frozen judge core; the arbiter tier has its own validation battery
# and its amber resolution could legitimately clear a must-flag row's amber,
# which the gate would misread as over-claiming.
#
# Never pass --api-key: that switches off the two-account key rotation in
# llm_client (config/google_api_key*.txt) and pins the run to one free seat.
set -u
cd "$(dirname "$0")/.."
PY=${PY:-venv/bin/python3}
MODEL=${MODEL:-gemini/gemma-4-31b-it}
SCORE_ONLY=${SCORE_ONLY:-0}
PROMPTS=${PROMPTS:-}
# EXTRA_FLAGS="--direction-check" (2026-09-09): extra verify_my_text.py
# flags appended to BOTH passes, so a default-off check can be measured on one
# arm without touching the script. Word-split on purpose; quote nothing inside.
EXTRA_FLAGS=${EXTRA_FLAGS:-}
RESUME=${RESUME:-1}
REUSE_FROM=${REUSE_FROM:-}
RECORD_REQUESTS=${RECORD_REQUESTS:-1}
STABILITY_RUN=${STABILITY_RUN:-0}
# Test seams (tests/test_gate_resume.py only): a stand-in checker, a list of
# texts, and a root for the non-canonical output folders. Unset = normal gate.
VERIFY_SCRIPT=${GATE_VERIFY_SCRIPT:-verify_my_text.py}
OUT_ROOT=${GATE_OUT_ROOT:-data}

# With PROMPTS set, the runs go through the in-process override runner instead of
# verify_my_text.py directly, so config/prompts/ is never written.
verify() {  # <verify_my_text.py args...>
  if [ -z "$PROMPTS" ]; then
    $PY "$VERIFY_SCRIPT" "$@"
  else
    local pairs=()
    IFS=',' read -ra pairs <<< "$PROMPTS"
    $PY benchmarks/verify_with_prompts.py "${pairs[@]}" -- "$@"
  fi
}

if [ $# -lt 1 ]; then
  # The header block only: skip the shebang, print comments, stop at the first
  # line of code so internal comments further down never leak into the usage text.
  awk 'NR==1 {next} /^#/ {sub(/^# ?/, ""); print; next} {exit}' "$0"
  echo "ERROR: a tag is required (use 'canonical' only deliberately)." >&2
  exit 2
fi
TAG=$1
CONC=${2:-2}

# name | text | sources | embeddings donor (content-hash keyed, saves ~30 min)
TEXTS=(
  "paper1|data/paper1_import/my_text.md|data/paper1_verification/sources|data/paper1_verification"
  "bentonite|examples/bentonite/my_text.md|examples/bentonite/sources|data/bentonite_verification"
  "chimp|examples/chimpanzee_validation/my_text.md|examples/chimpanzee_validation/sources|data/chimp_verification"
  "essay|data/loop_rounds/round_1/project/my_text.md|data/loop_rounds/round_1/project/sources|data/coverage_gate_run"
  "bohemia|data/loop_rounds/round_3/project/my_text.md|data/loop_rounds/round_3/project/sources|data/gate_run_bohemia"
  "pots|data/loop_rounds/round_4/project/my_text.md|data/loop_rounds/round_4/project/sources|data/gate_run_pots"
)
# Tests only: GATE_TEXTS="name|text|sources|donor;name|..." replaces the list.
if [ -n "${GATE_TEXTS:-}" ]; then
  IFS=';' read -ra TEXTS <<< "$GATE_TEXTS"
fi

outdir() {  # <name> -> the directory this run writes to
  outdir_for "$TAG" "$1"
}
outdir_for() {  # <tag> <name> -> the directory that tag's run of <name> writes to
  case "$1:$2" in
    canonical:paper1)    echo data/paper1_verification ;;
    canonical:bentonite) echo data/bentonite_verification ;;
    canonical:chimp)     echo data/chimp_verification ;;
    canonical:essay)     echo data/coverage_gate_run ;;
    canonical:bohemia)   echo data/gate_run_bohemia ;;
    canonical:pots)      echo data/gate_run_pots ;;
    *)                   echo "$OUT_ROOT/gate_${1}_$2" ;;
  esac
}

# Card #141: answer reuse from an earlier arm. Refuse the combinations that
# would make the arm measure nothing.
if [ -n "$REUSE_FROM" ]; then
  if [ "$REUSE_FROM" = "$TAG" ]; then
    echo "ERROR: REUSE_FROM=$REUSE_FROM is this arm's own tag. Reuse copies answers" >&2
    echo "  from a DIFFERENT, earlier arm; give that arm's tag." >&2
    exit 2
  fi
  if [ "$STABILITY_RUN" != "0" ] || [[ " $EXTRA_FLAGS " == *" --verdict-vote "* ]]; then
    echo "ERROR: REUSE_FROM is set, but this arm is a verdict-stability measurement" >&2
    echo "  (STABILITY_RUN=1 or --verdict-vote in EXTRA_FLAGS). A stability measurement" >&2
    echo "  asks the same questions again to see whether the answers change, so" >&2
    echo "  copying the earlier arm's answers would make it measure nothing." >&2
    echo "  Switch one of the two off." >&2
    exit 2
  fi
fi
[ "$STABILITY_RUN" != "0" ] && export PAPERTRAIL_STABILITY_RUN=1
[ "$RECORD_REQUESTS" != "0" ] && export PAPERTRAIL_RECORD_REQUESTS=1

echo "=== GATE RUN tag=$TAG model=$MODEL concurrency=$CONC score_only=$SCORE_ONLY $(date +%F' '%H:%M:%S) ==="
[ -z "$PROMPTS" ] && echo "prompts: config/prompts/ as committed (no override)" \
                  || echo "prompts: OVERRIDDEN in-process -> $PROMPTS"

if [ "$SCORE_ONLY" != "1" ]; then
  for row in "${TEXTS[@]}"; do
    IFS='|' read -r name text sources donor <<< "$row"
    out=$(outdir "$name")
    echo "=== $name -> $out  start $(date +%F' '%H:%M:%S) ==="
    mkdir -p "$out"
    # A directory belongs to exactly ONE prompt arm. The second pass reuses previous
    # verdicts keyed on model + text + source hashes, and the PROMPT is not part of
    # that key (task #44), so re-running a tag under a different PROMPTS value would
    # silently keep verdicts produced by the old wording and the output would look
    # perfectly normal. Refuse instead: a new prompt arm gets a new tag.
    arm_file="$out/.prompt_arm"
    arm_now="${PROMPTS:-none}"
    if [ -f "$arm_file" ] && [ "$(cat "$arm_file")" != "$arm_now" ]; then
      echo "ERROR: $out was produced under a different prompt arm." >&2
      echo "  on disk: $(cat "$arm_file")" >&2
      echo "  now:     $arm_now" >&2
      echo "  Verdict reuse is not keyed on the prompt (task #44), so this run would" >&2
      echo "  silently mix wordings. Use a new <tag>, or delete $out first." >&2
      exit 2
    fi
    printf '%s' "$arm_now" > "$arm_file"
    # Which code produced this folder: "<sha>" or "<sha>+dirty".
    $PY benchmarks/gate_resume.py commit > "$out/.code_commit"
    resume_args=(--out "$out" --text "$text" --sources "$sources" --model "$MODEL"
                 --arm "$arm_now" --extra "$EXTRA_FLAGS")
    # Skip a text this tag already finished under identical inputs.
    # Anything different, missing or doubtful prints "RUN: <reason>" and the text
    # is judged from zero exactly as before.
    if [ "$RESUME" != "0" ]; then
      if $PY benchmarks/gate_resume.py check "${resume_args[@]}"; then
        echo "=== $name skipped: already finished in this test $(date +%F' '%H:%M:%S) ==="
        continue
      fi
    fi
    $PY benchmarks/gate_resume.py forget --out "$out"
    if [ ! -d "$out/embeddings" ] && [ -d "$donor/embeddings" ] && [ "$donor" != "$out" ]; then
      cp -r "$donor/embeddings" "$out/embeddings"
    fi
    # Card #141: point the model client at the earlier arm's answers for THIS
    # text, and remember where this run's call-log lines start for the count.
    if [ -n "$REUSE_FROM" ]; then
      unset PAPERTRAIL_REUSE_FROM
      donor_log="$(outdir_for "$REUSE_FROM" "$name")/llm_calls.jsonl"
      if [ -f "$donor_log" ]; then
        export PAPERTRAIL_REUSE_FROM="$donor_log"
        echo "=== $name reuses identical answers from $donor_log ==="
      else
        echo "=== $name: no call log at $donor_log; every call is asked live ==="
      fi
      $PY benchmarks/gate_reuse.py mark --out "$out"
    fi
    # First pass: --full so nothing is reused from an earlier run.
    verify --text "$text" --sources "$sources" --output-dir "$out" \
        --model "$MODEL" --yes --full --no-arbiter --concurrency "$CONC" $EXTRA_FLAGS
    first_rc=$?
    echo "=== $name first pass exit=$first_rc $(date +%F' '%H:%M:%S) ==="
    # Second identical pass WITHOUT --full: re-asks only what the free seat
    # dropped; everything else is reused, so this is cheap.
    verify --text "$text" --sources "$sources" --output-dir "$out" \
        --model "$MODEL" --yes --no-arbiter --concurrency "$CONC" $EXTRA_FLAGS
    retry_rc=$?
    echo "=== $name retry exit=$retry_rc $(date +%F' '%H:%M:%S) ==="
    [ -n "$REUSE_FROM" ] && unset PAPERTRAIL_REUSE_FROM
    # Record the text as finished only when both passes exited cleanly.
    if [ "$first_rc" = "0" ] && [ "$retry_rc" = "0" ]; then
      $PY benchmarks/gate_resume.py stamp "${resume_args[@]}"
    fi
  done
fi

echo
echo "=== REFUSED CALLS PER RUN (every number must be 0, or no score below means anything) ==="
contaminated=0
for row in "${TEXTS[@]}"; do
  IFS='|' read -r name text sources donor <<< "$row"
  out=$(outdir "$name")
  if [ ! -f "$out/analysis.json" ]; then
    printf "  %-10s NO RESULT FILE at %s — this text was not verified\n" "$name" "$out"
    missing=1; continue
  fi
  # task #37: count every failure marker, not just the legacy reason string —
  # judge_error (verdict minted during failures) and checks_failed (an extra
  # check dropped during failures) contaminate a score the same way. The
  # scorers below also refuse on their own; this line is the early signal.
  n=$(grep -c -E '"no LLM response"|"judge_error": true|"checks_failed"' "$out/analysis.json" || true)
  printf "  %-10s %s\n" "$name" "$n"
  [ "$n" = "0" ] || contaminated=1
done
[ "${missing:-0}" = "0" ] || echo "  WARNING: a text has no result file — run without SCORE_ONLY, or check the log for a crash."
[ "$contaminated" = "0" ] || echo "  WARNING: refused calls present — those claims read as red cards (task #37). Re-run to retry them before trusting any score."

if [ -n "$REUSE_FROM" ]; then
  echo
  echo "=== ANSWERS REUSED FROM ARM $REUSE_FROM (counts this test's calls only) ==="
  for row in "${TEXTS[@]}"; do
    IFS='|' read -r name text sources donor <<< "$row"
    printf "  %-10s %s\n" "$name" "$($PY benchmarks/gate_reuse.py summary --out "$(outdir "$name")")"
  done
fi

echo
rc=0
if [ "$TAG" = "canonical" ]; then
  echo "=== SCORING via check_all.sh (canonical folders) $(date +%F' '%H:%M:%S) ==="
  bash benchmarks/check_all.sh || rc=1
else
  echo "=== LAYER 1: the three hand-audited papers ==="
  $PY benchmarks/regression_check.py --analysis "$(outdir paper1)/analysis.json"    --ground-truth benchmarks/paper1_ground_truth.json      || rc=1
  $PY benchmarks/regression_check.py --analysis "$(outdir bentonite)/analysis.json" --ground-truth benchmarks/bentonite_ground_truth.json   || rc=1
  $PY benchmarks/regression_check.py --analysis "$(outdir chimp)/analysis.json"     --ground-truth benchmarks/chimpanzee_ground_truth.json  || rc=1
  echo
  echo "=== LAYER 2: coverage gate v2 (the proof sentences shown on the card) ==="
  $PY benchmarks/coverage_check.py --analysis "$(outdir essay)/analysis.json"   --ground-truth benchmarks/coverage_ground_truth_essay.json   || rc=1
  $PY benchmarks/coverage_check.py --analysis "$(outdir bohemia)/analysis.json" --ground-truth benchmarks/coverage_ground_truth_bohemia.json || rc=1
  $PY benchmarks/coverage_check.py --analysis "$(outdir pots)/analysis.json"    --ground-truth benchmarks/coverage_ground_truth_pots.json    || rc=1
fi

[ "$contaminated" = "0" ] && [ "${missing:-0}" = "0" ] || rc=1
echo
if [ "$rc" = "0" ]; then
  echo "=== GATE RESULT: no new red row. Rows the author has ruled accepted_red (paper1 t49, t56; essay t8, t10 as of 2026-09-06) may still print RED above — they are standing reminders, not failures. ==="
else
  echo "=== GATE RESULT: a NEW red row, a refused call or a missing result file — read the FAIL lines above. ==="
fi
echo "=== GATE EXIT=$rc  (tag=$TAG) $(date +%F' '%H:%M:%S) ==="
exit $rc
