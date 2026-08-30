#!/usr/bin/env python3
"""Wait for a long background run to finish — the ONE shared watcher.

Why this exists (working-style review lesson 2, approved 2026-08-09): three
sessions each hand-built a watcher and each shipped a different bug — one
matched a file that already existed when the run STARTED and declared victory
instantly, one notified on every poll instead of once, one never fired at all.
A false "it is done" is worse than no signal: the next step starts on
incomplete results. This script is written once, tested, and designed against
exactly those three failure modes:

  1. Only evidence created AFTER the watcher starts counts. --log seeks to the
     current end of file first and searches only appended bytes; --file
     REFUSES a target that already exists (unless you add --quiet-secs, which
     turns the check into "unmodified for N seconds", meaningful either way).
  2. It reports exactly once, then exits. Never a notification loop.
  3. It always ends: --timeout (default 12h) exits with code 2 and an honest
     "TIMED OUT — NOT confirmed finished" instead of hanging or lying.

Usage examples
    # done when the runner process exits:
    python3 wait_for_run.py --pid 12345
    # done when "ALL ARMS COMPLETE" is appended to the log:
    python3 wait_for_run.py --log data/run7/run.log --pattern "ALL ARMS COMPLETE"
    # done when results.json appears (must not exist yet):
    python3 wait_for_run.py --file data/run7/results.json
    # done when the log has been quiet for 10 minutes:
    python3 wait_for_run.py --log data/run7/run.log --quiet-secs 600
    # run one command once when done (e.g. a scoring step):
    python3 wait_for_run.py --pid 12345 --then "venv/bin/python score.py"

Give MULTIPLE conditions and it finishes when ANY one is met (the reason is
named in the output). Exit codes: 0 = done, 2 = timed out, 1 = bad arguments.
Polls quietly (default every 30 s) and prints ONE final line — never pipe a
long run through tail (standing rule); point --log at the file instead.
stdlib only, no network.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Wait for a background run; report once; never hang forever.")
    ap.add_argument("--pid", type=int, help="done when this process exits")
    ap.add_argument("--log", help="log file to watch (with --pattern and/or --quiet-secs)")
    ap.add_argument("--pattern", help="regex; done when it appears in bytes APPENDED after start")
    ap.add_argument("--file", dest="target", help="done when this file appears (must not exist yet)")
    ap.add_argument("--quiet-secs", type=int,
                    help="done when --log/--file has been unmodified this many seconds")
    ap.add_argument("--timeout", type=float, default=12 * 3600,
                    help="give up after this many seconds (default 12h; exit code 2)")
    ap.add_argument("--interval", type=float, default=30, help="poll every N seconds (default 30)")
    ap.add_argument("--then", help="shell command to run ONCE when done (not on timeout)")
    a = ap.parse_args()

    if not (a.pid or (a.log and (a.pattern or a.quiet_secs)) or a.target):
        ap.error("give --pid, --log with --pattern/--quiet-secs, or --file")
    if a.pattern and not a.log:
        ap.error("--pattern needs --log")

    # Failure mode 1: evidence that predates the watcher must not count.
    log_pos = 0
    if a.log and a.pattern:
        try:
            log_pos = os.path.getsize(a.log)
        except OSError:
            log_pos = 0  # log not written yet; everything in it will be new
    if a.target and os.path.exists(a.target) and not a.quiet_secs:
        print(f"REFUSING: {a.target} already exists, so its appearance cannot "
              f"signal completion. Delete it first, watch a different file, or "
              f"add --quiet-secs to wait for it to stop changing.")
        return 1

    rx = re.compile(a.pattern) if a.pattern else None
    start = time.time()
    reason = None
    while time.time() - start < a.timeout:
        if a.pid and not pid_alive(a.pid):
            reason = f"process {a.pid} exited"
        if reason is None and rx and a.log and os.path.exists(a.log):
            size = os.path.getsize(a.log)
            if size < log_pos:
                log_pos = 0  # log was truncated/rotated; the new content is new
            if size > log_pos:
                with open(a.log, "rb") as f:
                    f.seek(log_pos)
                    chunk = f.read().decode(errors="replace")
                log_pos = size
                if rx.search(chunk):
                    reason = f"pattern {a.pattern!r} appeared in {a.log}"
        if reason is None and a.quiet_secs:
            for path in (a.log, a.target):
                if path and os.path.exists(path):
                    if time.time() - os.path.getmtime(path) >= a.quiet_secs:
                        reason = f"{path} unchanged for {a.quiet_secs}s"
                        break
        if reason is None and a.target and not a.quiet_secs and os.path.exists(a.target):
            reason = f"{a.target} appeared"
        if reason:
            break
        time.sleep(a.interval)

    now = datetime.now().isoformat(timespec="seconds")
    if reason is None:
        # Failure mode 3: never hang, never lie. Timing out is a report, not a result.
        print(f"TIMED OUT at {now} after {a.timeout:.0f}s — the run is NOT "
              f"confirmed finished. Check it by hand before using its output.")
        return 2
    # Failure mode 2: one report, then exit.
    print(f"DONE at {now}: {reason} (waited {time.time() - start:.0f}s)")
    if a.then:
        subprocess.run(a.then, shell=True, check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
