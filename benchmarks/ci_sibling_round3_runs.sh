#!/usr/bin/env bash
# Round 3 of task #32: judge the original and repaired Citation-Integrity sets
# and leave four runs on disk for `ci_sibling_compare.py` to score.
#
# FREE. Judge = gemini/gemma-4-31b-it on Google's free tier; --no-arbiter
# because the arbiter is a PAID model. Nothing here spends money.
#
# WHY A SEPARATE WORKTREE
#   The shared working folder is usually sitting on another session's branch
#   with in-flight changes to the judging code. Both arms of a comparison must
#   run on the same code, and that code should not be someone's unfinished work,
#   so every run happens in a clean checkout of origin/master. `config/` is
#   copied in because it is gitignored, and the project venv is used explicitly
#   because a fresh worktree has no installed packages of its own.
#
# WHY THE REPAIRED ARMS ARE SEEDED
#   A repaired arm starts as a copy of its own baseline run. The tool then
#   reuses the verdict of every claim whose text, markers and source files are
#   unchanged, so only the rows that gained sibling papers are judged again
#   (45 of 107 on pilot100). It is much faster AND cleaner: untouched rows come
#   out identical by construction, so nothing in the comparison is model noise.
#
# WHAT TO EXPECT
#   Google's free tier refuses roughly one request in three and the tool waits
#   and retries, giving 7-11 model calls a minute. A full pass is 8-11 hours.
#   Both config/google_api_key*.txt keys rotate automatically; that is already
#   the ceiling, not a misconfiguration.
#
# INTERRUPTING IS SAFE
#   Re-running this script resumes: finished arms are reused, an interrupted
#   arm is judged again. `ci_sibling_compare.py` refuses to score an arm that
#   never wrote its own results, so a half-finished run cannot reach a total.
#
# Usage:  bash benchmarks/ci_sibling_round3_runs.sh [workdir]
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${1:-${TMPDIR:-/tmp}/ci_round3_worktree}"
D="$REPO/data/citation_integrity"
PY="$REPO/venv/bin/python"
LOGS="$D/round3_logs"
mkdir -p "$LOGS"

if [ ! -d "$WORK/.git" ] && [ ! -f "$WORK/.git" ]; then
  echo "creating a clean checkout of origin/master at $WORK"
  git -C "$REPO" fetch --quiet origin master || true
  git -C "$REPO" worktree add -f "$WORK" origin/master
fi
cp -r "$REPO/config" "$WORK/" 2>/dev/null || true
cd "$WORK"

run () {   # run <arm name> <batch directory>
  if [ -f "$D/run32_$1/analysis.json" ] && \
     grep -q "\"text_file\":[^,]*$2/my_text.md" "$D/run32_$1/analysis.json"; then
    echo "=== $1 already finished, skipping ==="
    return
  fi
  echo "=== $1 started $(date '+%F %H:%M:%S') ==="
  "$PY" verify_my_text.py \
      --text "$D/$2/my_text.md" --sources "$D/$2/sources" \
      --references "$D/$2/my_text.md.refs.txt" --output-dir "$D/run32_$1" \
      --model gemini/gemma-4-31b-it --no-arbiter --yes --concurrency 4 \
      > "$LOGS/$1.log" 2>&1
  echo "=== $1 finished $(date '+%F %H:%M:%S') exit $? ==="
}

seed () {  # seed <repaired arm> <baseline arm> — reuse the untouched verdicts
  [ -f "$D/run32_$2/analysis.json" ] || { echo "no baseline for $1"; return 1; }
  rm -rf "$D/run32_$1"
  cp -r "$D/run32_$2" "$D/run32_$1"
  mv "$D/run32_$1/llm_calls.jsonl" "$D/run32_$1/llm_calls_baseline.jsonl" 2>/dev/null || true
}

run pilot100_original batch_dev_pilot100
seed pilot100_repaired pilot100_original && run pilot100_repaired batch_dev_pilot100_repaired
run fresh50_original batch_dev_fresh50
seed fresh50_repaired fresh50_original && run fresh50_repaired batch_dev_fresh50_repaired

echo "=== all four arms done $(date '+%F %H:%M:%S') ==="
echo "now score them:  $PY benchmarks/ci_sibling_compare.py"
