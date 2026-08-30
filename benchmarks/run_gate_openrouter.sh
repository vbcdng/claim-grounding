#!/usr/bin/env bash
# THE shared PAID ship-gate runner (OpenRouter, full precision). Use this — do
# not write your own — but read the rule below before you use it at all.
#
# ============================ THE RULE ============================
# THIS SCRIPT SPENDS THE AUTHOR'S MONEY. A CLAUDE SESSION MUST NEVER DECIDE
# ON ITS OWN TO RUN IT. It may be run ONLY when the author has said so, in
# that session, in their own words ("run the gate on the paid host", "use
# OpenRouter for this arm", or an equally explicit go). Author instruction,
# 2026-08-28. Things that are NOT a go: the free run being slow, the queue
# being busy, a deadline, an earlier go for a DIFFERENT paid run, the author
# asking a question about paid hosting, or this script being convenient.
# When in doubt, run the free script and say why you did not run this one.
#
# The free alternative is benchmarks/run_gate_gemma.sh — same six texts, same
# scoring, $0, exactly reproducible, about 2.5 hours per text on the free seat.
# That script stays the DEFAULT for every gate arm that will be compared with
# an arm already recorded, because the host itself moves about five rows in a
# hundred (task #31), so a free arm and a paid arm are not comparable.
# ==================================================================
#
#   AUTHOR_GO="<the author's own words, with the date>" \
#     bash benchmarks/run_gate_openrouter.sh <tag> [concurrency]
#
#   AUTHOR_GO      REQUIRED. Quote the go you are acting on, e.g.
#                  AUTHOR_GO="author 2026-08-28: 'run the r7 arm on the paid
#                  host, I do not want to wait for the free seat'". It is
#                  printed at the top of the log and written into every output
#                  folder as .paid_run_authorization, so a paid run can always
#                  be traced back to the permission it rested on. The script
#                  refuses to start without it. Do not write it yourself from
#                  an inferred or assumed go.
#   <tag>          writes into data/gate_<tag>_{paper1,bentonite,chimp,essay,
#                  bohemia,pots}. The tag 'canonical' is REFUSED here: those
#                  folders hold the shipped reference results produced on the
#                  free judge, and overwriting them with paid-host verdicts
#                  would quietly make every later free-vs-free comparison wrong.
#   [concurrency]  default 4. The free script uses 2 because the free seat drops
#                  claims above that; the paid route does not, and 4 was measured
#                  clean (2,539 requests, zero refused, task #31's fifth run).
#
#   MAX_USD=<n>    spending ceiling, default 2.00. After each text the script
#                  prices what has been spent so far at the DEAREST pinned
#                  seller's rate and stops before the next text if the ceiling
#                  would be passed. A six-text arm costs about $0.60.
#   SCORE_ONLY=1   skip the runs, score whatever is already on disk. Free.
#   PROMPTS="name=path[,name=path]"
#                  gate a prompt variant in-process, exactly as in the free
#                  script — config/prompts/ is never written.
#   ESTIMATE_ONLY=1 print the plan, the pinning and the cost estimate, then stop
#                  without making a single request. Free. Use this to show the
#                  author what a paid arm would cost before asking for a go.
#
# WHY THE PRECISION IS PINNED. Left alone the marketplace routes each request to
# the cheapest company, which serves a four-bit copy of the model — a copy that
# is more lenient AND that answered the same question two opposite ways within
# minutes (task #31, 2026-08-20). Pinned to full precision the same model scored
# 67 of 100 against the free service's 62, was better on BOTH kinds of mistake,
# and gave the same answer thirty times out of thirty. So this script names the
# four full-precision sellers, cheapest first, and refuses every compressed copy
# and every other seller. Naming only ONE seller does not work: that seller's
# shared capacity turns away about one request in twelve, and with substitutes
# refused there is nowhere for those requests to go.
#
# GATE RUNS ARE PINNED --no-arbiter, exactly as in the free script.
set -u
cd "$(dirname "$0")/.."
PY=${PY:-venv/bin/python3}
MODEL=${MODEL:-openrouter/google/gemma-4-31b-it}
SCORE_ONLY=${SCORE_ONLY:-0}
ESTIMATE_ONLY=${ESTIMATE_ONLY:-0}
PROMPTS=${PROMPTS:-}
MAX_USD=${MAX_USD:-2.00}
AUTHOR_GO=${AUTHOR_GO:-}

# The four sellers that hold the full-precision copy, cheapest first, with
# compressed copies and every other seller refused outright.
PIN='{"openrouter/google/gemma-4-31b-it": {"provider": {"order": ["OpenInference", "CoreWeave", "Venice", "Novita"], "quantizations": ["bf16"], "allow_fallbacks": false}}}'
# Dearest of the four, used to price the ceiling check conservatively.
PRICE_IN_PER_M=0.14
PRICE_OUT_PER_M=0.40

usage() {
  awk 'NR==1 {next} /^#/ {sub(/^# ?/, ""); print; next} {exit}' "$0"
}

if [ $# -lt 1 ]; then
  usage
  echo "ERROR: a tag is required." >&2
  exit 2
fi
TAG=$1
CONC=${2:-4}

if [ "$TAG" = "canonical" ]; then
  echo "ERROR: 'canonical' is refused by the paid runner." >&2
  echo "  Those folders hold the shipped reference results, produced on the free" >&2
  echo "  judge. Paid-host verdicts differ on about 5 rows in 100 (task #31), so" >&2
  echo "  writing them there would silently break every later comparison. Use a" >&2
  echo "  named tag; promoting a paid arm to canonical is the author's decision." >&2
  exit 2
fi

# ---- the permission check, before anything else can spend --------------------
if [ "$SCORE_ONLY" != "1" ] && [ "$ESTIMATE_ONLY" != "1" ]; then
  if [ ${#AUTHOR_GO} -lt 20 ]; then
    echo "REFUSED: this runner spends money and has no author go recorded." >&2
    echo >&2
    echo "  Set AUTHOR_GO to the author's own words, with the date, e.g." >&2
    echo "    AUTHOR_GO=\"author 2026-08-28: 'run this arm on the paid host'\" \\" >&2
    echo "      bash benchmarks/run_gate_openrouter.sh <tag>" >&2
    echo >&2
    echo "  A Claude session must NEVER write this itself from an assumed or" >&2
    echo "  inferred go, and must never run this script on its own initiative." >&2
    echo "  If you have no explicit go: run benchmarks/run_gate_gemma.sh (free)," >&2
    echo "  or ESTIMATE_ONLY=1 here to show the author the cost first." >&2
    exit 3
  fi
fi

# ---- the key ----------------------------------------------------------------
if [ "$SCORE_ONLY" != "1" ] && [ "$ESTIMATE_ONLY" != "1" ]; then
  if [ -z "${OPENROUTER_API_KEY:-}" ]; then
    if [ -f config/openrouter_api_key.txt ]; then
      OPENROUTER_API_KEY=$(tr -d '\r\n' < config/openrouter_api_key.txt)
      export OPENROUTER_API_KEY
    else
      echo "ERROR: no OPENROUTER_API_KEY in the environment and no" >&2
      echo "  config/openrouter_api_key.txt to read it from. Nothing was spent." >&2
      exit 2
    fi
  fi
  # Never --api-key on the command line: on the free route it switches off the
  # two-account rotation, and here it would put the key in every process list.
  export PAPERTRAIL_LLM_EXTRA_BODY="$PIN"
fi

verify() {  # <verify_my_text.py args...>
  if [ -z "$PROMPTS" ]; then
    $PY verify_my_text.py "$@"
  else
    local pairs=()
    IFS=',' read -ra pairs <<< "$PROMPTS"
    $PY benchmarks/verify_with_prompts.py "${pairs[@]}" -- "$@"
  fi
}

# name | text | sources | embeddings donor (content-hash keyed, saves ~30 min)
TEXTS=(
  "paper1|data/paper1_import/my_text.md|data/paper1_verification/sources|data/paper1_verification"
  "bentonite|examples/bentonite/my_text.md|examples/bentonite/sources|data/bentonite_verification"
  "chimp|examples/chimpanzee_validation/my_text.md|examples/chimpanzee_validation/sources|data/chimp_verification"
  "essay|data/loop_rounds/round_1/project/my_text.md|data/loop_rounds/round_1/project/sources|data/coverage_gate_run"
  "bohemia|data/loop_rounds/round_3/project/my_text.md|data/loop_rounds/round_3/project/sources|data/gate_run_bohemia"
  "pots|data/loop_rounds/round_4/project/my_text.md|data/loop_rounds/round_4/project/sources|data/gate_run_pots"
)

outdir() { echo "data/gate_${TAG}_$1"; }

# Price everything this arm has sent so far, from the per-request logs.
spent_so_far() {
  $PY - "$PRICE_IN_PER_M" "$PRICE_OUT_PER_M" "$@" <<'PYEOF'
import json, sys, os
pin, pout = float(sys.argv[1]), float(sys.argv[2])
tin = tout = 0
for d in sys.argv[3:]:
    p = os.path.join(d, "llm_calls.jsonl")
    if not os.path.exists(p):
        continue
    for line in open(p, encoding="utf-8"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        tin += r.get("prompt_tokens") or 0
        tout += r.get("completion_tokens") or 0
print("%.4f" % (tin / 1e6 * pin + tout / 1e6 * pout))
PYEOF
}

# Which companies actually answered — proof, not assumption, that the run was
# served at full precision (the per-request log records the seller since 8/20).
served_by() {
  $PY - "$1" <<'PYEOF'
import json, sys, os, collections
p = os.path.join(sys.argv[1], "llm_calls.jsonl")
if not os.path.exists(p):
    sys.exit(0)
c = collections.Counter()
for line in open(p, encoding="utf-8"):
    try:
        r = json.loads(line)
    except ValueError:
        continue
    s = r.get("served_by")
    c[s if isinstance(s, str) else "not recorded"] += 1
print("    answered by: " + ", ".join(f"{k} {v}" for k, v in c.most_common()))
PYEOF
}

echo "=== PAID GATE RUN tag=$TAG model=$MODEL concurrency=$CONC $(date +%F' '%H:%M:%S) ==="
echo "authorization: ${AUTHOR_GO:-(none — score/estimate only)}"
echo "precision pin: $PIN"
echo "spending ceiling: \$$MAX_USD (a six-text arm costs about \$0.60)"
[ -z "$PROMPTS" ] && echo "prompts: config/prompts/ as committed (no override)" \
                  || echo "prompts: OVERRIDDEN in-process -> $PROMPTS"

if [ "$ESTIMATE_ONLY" = "1" ]; then
  echo
  echo "ESTIMATE ONLY — no request was made and nothing was spent."
  echo "  Six texts, about 3,450 requests, roughly 6.4 million word-pieces sent"
  echo "  and 0.2 million returned, measured on earlier arms."
  echo "  At the cheapest pinned seller (\$0.08 in / \$0.35 out per million): about \$0.58"
  echo "  At the dearest pinned seller (\$0.14 in / \$0.40 out per million): about \$0.98"
  echo "  Free alternative, same six texts, same scoring: benchmarks/run_gate_gemma.sh"
  exit 0
fi

if [ "$SCORE_ONLY" != "1" ]; then
  dirs=()
  for row in "${TEXTS[@]}"; do
    IFS='|' read -r name text sources donor <<< "$row"
    out=$(outdir "$name")
    dirs+=("$out")

    spent=$(spent_so_far "${dirs[@]}")
    if [ "$(echo "$spent > $MAX_USD" | bc -l)" = "1" ]; then
      echo "STOPPED before $name: \$$spent already spent, ceiling is \$$MAX_USD." >&2
      echo "  Raise MAX_USD deliberately, with the author's go, to continue." >&2
      break
    fi

    echo "=== $name -> $out  start $(date +%F' '%H:%M:%S)  (spent so far \$$spent) ==="
    mkdir -p "$out"

    # A directory belongs to exactly one judge host. Verdict reuse is keyed on the
    # model string, so a host change already forces a re-judge, but a mixed folder
    # is still unreadable afterwards — refuse it outright.
    host_file="$out/.judge_host"
    host_now="paid-openrouter-bf16"
    if [ -f "$host_file" ] && [ "$(cat "$host_file")" != "$host_now" ]; then
      echo "ERROR: $out holds results from a different judge host: $(cat "$host_file")" >&2
      echo "  Use a new <tag>. Mixing hosts in one folder makes the arm meaningless." >&2
      exit 2
    fi
    printf '%s' "$host_now" > "$host_file"

    # Same one-tag-one-prompt-arm guard as the free script (task #44).
    arm_file="$out/.prompt_arm"
    arm_now="${PROMPTS:-none}"
    if [ -f "$arm_file" ] && [ "$(cat "$arm_file")" != "$arm_now" ]; then
      echo "ERROR: $out was produced under a different prompt arm." >&2
      echo "  on disk: $(cat "$arm_file")" >&2
      echo "  now:     $arm_now" >&2
      exit 2
    fi
    printf '%s' "$arm_now" > "$arm_file"

    printf '%s\n' "$AUTHOR_GO" > "$out/.paid_run_authorization"

    if [ ! -d "$out/embeddings" ] && [ -d "$donor/embeddings" ] && [ "$donor" != "$out" ]; then
      cp -r "$donor/embeddings" "$out/embeddings"
    fi

    verify --text "$text" --sources "$sources" --output-dir "$out" \
        --model "$MODEL" --yes --full --no-arbiter --concurrency "$CONC"
    echo "=== $name first pass exit=$? $(date +%F' '%H:%M:%S) ==="
    # Second identical pass WITHOUT --full: re-asks only what failed. Paid
    # requests fail far less often than the free seat drops claims, but an
    # outage still turns a failed request into a red card (task #37).
    verify --text "$text" --sources "$sources" --output-dir "$out" \
        --model "$MODEL" --yes --no-arbiter --concurrency "$CONC"
    echo "=== $name retry exit=$? $(date +%F' '%H:%M:%S) ==="
    served_by "$out"
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
  n=$(grep -c -E '"no LLM response"|"judge_error": true|"checks_failed"' "$out/analysis.json" || true)
  printf "  %-10s %s\n" "$name" "$n"
  [ "$n" = "0" ] || contaminated=1
done
[ "${missing:-0}" = "0" ] || echo "  WARNING: a text has no result file — check the log for a crash or the spending ceiling."
[ "$contaminated" = "0" ] || echo "  WARNING: refused calls present — those claims read as red cards (task #37). Re-run to retry them before trusting any score."

echo
alldirs=()
paid_arm=0
for row in "${TEXTS[@]}"; do
  IFS='|' read -r name _ _ _ <<< "$row"
  d=$(outdir "$name"); alldirs+=("$d")
  [ -f "$d/.judge_host" ] && [ "$(cat "$d/.judge_host")" = "paid-openrouter-bf16" ] && paid_arm=1
done
# SCORE_ONLY can be pointed at an arm this script never ran (a free one, say), so
# the money line and the not-comparable warning are printed only for a paid arm.
if [ "$paid_arm" = "1" ]; then
  echo "=== MONEY: about \$$(spent_so_far "${alldirs[@]}") at the dearest pinned seller's rate (upper bound; the invoice is lower) ==="
else
  echo "=== MONEY: nothing was spent by this invocation (no folder here carries the paid-host stamp) ==="
fi

echo
rc=0
echo "=== LAYER 1: the three hand-audited papers ==="
$PY benchmarks/regression_check.py --analysis "$(outdir paper1)/analysis.json"    --ground-truth benchmarks/paper1_ground_truth.json      || rc=1
$PY benchmarks/regression_check.py --analysis "$(outdir bentonite)/analysis.json" --ground-truth benchmarks/bentonite_ground_truth.json   || rc=1
$PY benchmarks/regression_check.py --analysis "$(outdir chimp)/analysis.json"     --ground-truth benchmarks/chimpanzee_ground_truth.json  || rc=1
echo
echo "=== LAYER 2: coverage gate v2 (the proof sentences shown on the card) ==="
$PY benchmarks/coverage_check.py --analysis "$(outdir essay)/analysis.json"   --ground-truth benchmarks/coverage_ground_truth_essay.json   || rc=1
$PY benchmarks/coverage_check.py --analysis "$(outdir bohemia)/analysis.json" --ground-truth benchmarks/coverage_ground_truth_bohemia.json || rc=1
$PY benchmarks/coverage_check.py --analysis "$(outdir pots)/analysis.json"    --ground-truth benchmarks/coverage_ground_truth_pots.json    || rc=1

[ "$contaminated" = "0" ] && [ "${missing:-0}" = "0" ] || rc=1
echo
if [ "$paid_arm" = "1" ]; then
  echo "=== PAID GATE EXIT=$rc  (tag=$TAG)  READ THIS BEFORE COMPARING: this arm was"
  echo "    judged on the paid full-precision host, so it is NOT comparable with an"
  echo "    arm run on the free Google service — the host alone moves about five"
  echo "    rows in a hundred. Compare paid with paid. $(date +%F' '%H:%M:%S) ==="
else
  echo "=== GATE EXIT=$rc  (tag=$TAG, scored only — these folders were not produced"
  echo "    by this paid runner) $(date +%F' '%H:%M:%S) ==="
fi
exit $rc
