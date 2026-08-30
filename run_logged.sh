#!/usr/bin/env bash
# run_logged.sh — run any command with its output written to a disk file CONTINUOUSLY.
#
# Why this exists: on 2026-08-10 the notebook died with an empty battery and every
# running shell lost its output (it lived only in memory buffers). This wrapper makes
# the log the primary copy: line-buffered append to logs/<name>_<timestamp>.log, plus
# a background loop that flushes the file to physical disk every 15 seconds, so a
# sudden power loss costs at most ~15 seconds of output.
#
# Usage:
#   ./run_logged.sh <name> -- <command> [args...]
#
# The first line printed is "LOG: <path>" so the caller can hand that path straight
# to wait_for_run.py --log <path> --pattern <regex>.
# Exit status of the wrapped command is preserved.
set -u

if [ "$#" -lt 3 ] || [ "$2" != "--" ]; then
    echo "usage: $0 <name> -- <command> [args...]" >&2
    exit 64
fi

name="$1"
shift 2

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
log_dir="$repo_dir/logs"
mkdir -p "$log_dir"
log="$log_dir/${name}_$(date +%Y%m%d_%H%M%S)_$$.log"
: > "$log"
echo "LOG: $log"

# Python (and most tools) block-buffer when writing to a file; force line buffering
# so every finished line reaches the file immediately.
export PYTHONUNBUFFERED=1

# Flush the log's pages to disk every 15s (kernel default can hold dirty pages ~30s).
(
    while sleep 15; do
        sync -d "$log" 2>/dev/null || sync
    done
) &
syncer=$!
trap 'kill "$syncer" 2>/dev/null' EXIT

{
    echo "=== run_logged.sh start $(date -Is) ==="
    echo "=== command: $*"
} >> "$log"

stdbuf -oL -eL "$@" >> "$log" 2>&1
status=$?

echo "=== run_logged.sh exit $status $(date -Is) ===" >> "$log"
sync -d "$log" 2>/dev/null || sync
echo "EXIT: $status (log: $log)"
exit "$status"
