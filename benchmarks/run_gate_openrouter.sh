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
#                  AUTHOR_GO="EXAMPLE ONLY - replace with the real approval
#                  text and date" (an invented placeholder). It is
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
#   EXTRA_FLAGS="--aida-grounder --aida-field-tools"
#                  (2026-10-01) extra verify_my_text.py
#                  flags appended to BOTH passes, exactly as in the free script.
#                  Word-split on purpose; quote nothing inside. Recorded in every
#                  output folder as .extra_flags: one tag = one set of flags, a
#                  re-run of the tag with different flags is refused (exit 2).
#   REUSE_FROM=<tag> (the free script's answer-reuse switch)
#                  serve every request that is byte-for-byte identical to one
#                  the earlier PAID arm <tag> already asked from that arm's
#                  recorded answer; only the rest is sent and paid for. Reused
#                  answers are marked in llm_calls.jsonl, cost nothing, and are
#                  left out of the spending check; the summary prints "reused N
#                  of M calls" per text. Refused (exit 2, before anything is
#                  sent): <tag> is this tag; STABILITY_RUN=1 or --verdict-vote in
#                  EXTRA_FLAGS; and, unlike the free script, any text whose donor
#                  folder is missing, was not judged on this paid host, or holds
#                  no request fingerprints (recorded before this switch existed) — on this
#                  script a donor that cannot donate means paying for the whole
#                  arm, which is not what the go was priced for.
#   RECORD_REQUESTS=1 default: write each request's fingerprint into
#                  llm_calls.jsonl so this arm can serve a later REUSE_FROM.
#                  Changes nothing sent and no result file. 0 = log as before.
#   STABILITY_RUN=1 mark this arm as a verdict-stability measurement, so
#                  REUSE_FROM is refused.
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
EXTRA_FLAGS=${EXTRA_FLAGS:-}
REUSE_FROM=${REUSE_FROM:-}
RECORD_REQUESTS=${RECORD_REQUESTS:-1}
STABILITY_RUN=${STABILITY_RUN:-0}
# Test seams (tests/test_paid_gate_reuse.py only), the same three as the free
# script: a stand-in checker, a list of texts, a root for the output folders.
VERIFY_SCRIPT=${GATE_VERIFY_SCRIPT:-verify_my_text.py}
OUT_ROOT=${GATE_OUT_ROOT:-data}
HOST_NOW="paid-openrouter-bf16"

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

# Answer reuse from an earlier arm: refuse the combinations that would make the
# arm measure nothing (same rules as the free script).
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
    $PY "$VERIFY_SCRIPT" "$@"
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
# Tests only: GATE_TEXTS="name|text|sources|donor;name|..." replaces the list.
if [ -n "${GATE_TEXTS:-}" ]; then
  IFS=';' read -ra TEXTS <<< "$GATE_TEXTS"
fi

outdir() { outdir_for "$TAG" "$1"; }
outdir_for() { echo "$OUT_ROOT/gate_${1}_$2"; }   # <tag> <name>

# Can the REUSE_FROM arm's folder for <name> donate? Prints a reason and
# returns 1 when it cannot: no call log, not judged on this paid host, or no
# request fingerprints (recorded before answer reuse existed, or RECORD_REQUESTS=0).
donor_problem() {  # <name>
  local d log
  d=$(outdir_for "$REUSE_FROM" "$1")
  log="$d/llm_calls.jsonl"
  if [ ! -f "$log" ]; then
    echo "no call log at $log"; return 1
  fi
  if [ ! -f "$d/.judge_host" ] || [ "$(cat "$d/.judge_host")" != "$HOST_NOW" ]; then
    echo "$d was not judged on the paid full-precision host"; return 1
  fi
  if ! grep -q '"request_sha256"' "$log"; then
    echo "$log holds no request fingerprints (recorded before answer reuse existed)"; return 1
  fi
  return 0
}

# Price everything this arm has sent so far, from the per-request logs. A line
# marked "reused" was served from the REUSE_FROM arm's recorded answer, was
# never sent, and costs nothing, so it is left out.
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
        if r.get("reused"):
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
echo "extra flags: ${EXTRA_FLAGS:-(none)}"
echo "answer reuse: ${REUSE_FROM:-(off)}"

if [ "$ESTIMATE_ONLY" = "1" ]; then
  echo
  echo "ESTIMATE ONLY — no request was made and nothing was spent."
  echo "  Six texts, about 3,450 requests, roughly 6.4 million word-pieces sent"
  echo "  and 0.2 million returned, measured on earlier arms."
  echo "  At the cheapest pinned seller (\$0.08 in / \$0.35 out per million): about \$0.58"
  echo "  At the dearest pinned seller (\$0.14 in / \$0.40 out per million): about \$0.98"
  if [ -n "$EXTRA_FLAGS" ]; then
    echo "  EXTRA_FLAGS adds the calls of those switches on top of that. No earlier"
    echo "  paid arm ran with them, so their number is NOT in this estimate; the"
    echo "  spending ceiling (\$$MAX_USD) stops the arm between texts if it would pass."
  fi
  if [ -n "$REUSE_FROM" ]; then
    echo "  REUSE_FROM=$REUSE_FROM: every request identical to one that arm asked is"
    echo "  served from its recorded answer for \$0, so only the requests that differ"
    echo "  are paid (93-100% repeat between arms one switch apart)."
    for row in "${TEXTS[@]}"; do
      IFS='|' read -r name _ _ _ <<< "$row"
      if why=$(donor_problem "$name"); then
        printf "    %-10s donor ready: %s\n" "$name" "$(outdir_for "$REUSE_FROM" "$name")"
      else
        printf "    %-10s donor NOT ready (%s) — a real run would be refused\n" "$name" "$why"
      fi
    done
  fi
  echo "  Free alternative, same six texts, same scoring: benchmarks/run_gate_gemma.sh"
  exit 0
fi

# A paid arm that reuses answers must have a usable donor for EVERY text before
# anything is sent: otherwise that text is asked in full and paid in full.
if [ "$SCORE_ONLY" != "1" ] && [ -n "$REUSE_FROM" ]; then
  bad=0
  for row in "${TEXTS[@]}"; do
    IFS='|' read -r name _ _ _ <<< "$row"
    if ! why=$(donor_problem "$name"); then
      echo "ERROR: REUSE_FROM=$REUSE_FROM cannot donate for $name: $why." >&2
      bad=1
    fi
  done
  if [ "$bad" = "1" ]; then
    echo "  Nothing was sent. Run the donor arm first (with RECORD_REQUESTS left on)," >&2
    echo "  or drop REUSE_FROM deliberately, with the author's go for the full price." >&2
    exit 2
  fi
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
    host_now="$HOST_NOW"
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

    # One tag = one set of EXTRA_FLAGS. A folder from
    # before this stamp existed was run with no extra flags, so a missing file
    # reads as "none".
    flags_file="$out/.extra_flags"
    flags_now="${EXTRA_FLAGS:-none}"
    flags_disk="none"
    [ -f "$flags_file" ] && flags_disk=$(cat "$flags_file")
    if [ -f "$out/analysis.json" ] || [ -f "$flags_file" ]; then
      if [ "$flags_disk" != "$flags_now" ]; then
        echo "ERROR: $out was produced with different EXTRA_FLAGS." >&2
        echo "  on disk: $flags_disk" >&2
        echo "  now:     $flags_now" >&2
        echo "  Use a new <tag>: one tag holds one set of flags." >&2
        exit 2
      fi
    fi
    printf '%s' "$flags_now" > "$flags_file"

    printf '%s\n' "$AUTHOR_GO" > "$out/.paid_run_authorization"

    if [ ! -d "$out/embeddings" ] && [ -d "$donor/embeddings" ] && [ "$donor" != "$out" ]; then
      cp -r "$donor/embeddings" "$out/embeddings"
    fi

    # Point the model client at the donor arm's answers for THIS text, and
    # remember where this run's call-log lines start for the reuse count.
    if [ -n "$REUSE_FROM" ]; then
      donor_log="$(outdir_for "$REUSE_FROM" "$name")/llm_calls.jsonl"
      export PAPERTRAIL_REUSE_FROM="$donor_log"
      echo "=== $name reuses identical answers from $donor_log ==="
      $PY benchmarks/gate_reuse.py mark --out "$out"
    fi

    verify --text "$text" --sources "$sources" --output-dir "$out" \
        --model "$MODEL" --yes --full --no-arbiter --concurrency "$CONC" $EXTRA_FLAGS
    echo "=== $name first pass exit=$? $(date +%F' '%H:%M:%S) ==="
    # Second identical pass WITHOUT --full: re-asks only what failed. Paid
    # requests fail far less often than the free seat drops claims, but an
    # outage still turns a failed request into a red card (task #37).
    verify --text "$text" --sources "$sources" --output-dir "$out" \
        --model "$MODEL" --yes --no-arbiter --concurrency "$CONC" $EXTRA_FLAGS
    echo "=== $name retry exit=$? $(date +%F' '%H:%M:%S) ==="
    [ -n "$REUSE_FROM" ] && unset PAPERTRAIL_REUSE_FROM
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

if [ -n "$REUSE_FROM" ]; then
  echo
  echo "=== ANSWERS REUSED FROM ARM $REUSE_FROM (counts this test's calls only; reused answers cost \$0) ==="
  for row in "${TEXTS[@]}"; do
    IFS='|' read -r name _ _ _ <<< "$row"
    printf "  %-10s %s\n" "$name" "$($PY benchmarks/gate_reuse.py summary --out "$(outdir "$name")")"
  done
fi

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
