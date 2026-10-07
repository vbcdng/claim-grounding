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

  4. (2026-09-28) A --pattern that the program writing the log can
     never print would wait until the timeout. Before waiting, a SELF-TEST
     finds that program (--producer, the log's own header line from
     gemma_queue.py or run_logged.sh, or the queue entry of a not-yet-started
     queue log), reads its source plus the local scripts and Python modules it
     uses, and REFUSES at once (exit 1) when a word of the pattern appears in
     none of them. Only letter words of 3+ characters are compared, so a
     variable part ("GATE EXIT=0" vs the script's "GATE EXIT=$rc") passes.
     When no producer can be found the test is skipped with a note on stderr;
     --skip-self-test "<reason>" turns it off deliberately. Known limits: the
     words are looked up one by one, so a phrase made of words the programs
     print in other places passes; and a word that only comes from data (a
     text name printed via a variable) is refused — skip the test for that.

Give MULTIPLE conditions and it finishes when ANY one is met (the reason is
named in the output). Exit codes: 0 = done, 2 = timed out, 1 = bad arguments
or a failed self-test.
Polls quietly (default every 30 s) and prints ONE final line — never pipe a
long run through tail (standing rule); point --log at the file instead.
stdlib only, no network.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


# ---------------------------------------------------------------- self-test

_QUEUE_HEADER = re.compile(r"^=== \S+ start: (.*) \(cwd (.*)\)$")
_RUN_LOGGED_HEADER = re.compile(r"^=== command: (.*)$")
_SCRIPT_EXT = (".py", ".sh", ".bash")
_MAX_FILES = 400


def pattern_branches(pattern: str) -> list[list[str]]:
    """The letter words (3+ chars) of each top-level alternative of a regex.
    An alternative with no such word cannot be checked (empty list)."""
    s = re.sub(r"\\[a-zA-Z]", " ", pattern)   # \d \s \b ... are not letters
    s = re.sub(r"\[[^\]]*\]", " ", s)          # character classes
    s = re.sub(r"\{[^}]*\}", " ", s)           # repeat counts
    s = re.sub(r"\(\?[a-zA-Z]+\)", " ", s)     # inline flags like (?i)
    s = s.replace("\\|", "\0")
    branches = []
    for part in s.split("|"):
        part = re.sub(r"\\(.)", r"\1", part.replace("\0", "|"))
        branches.append(re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", part))
    return branches


def _scripts_in(text: str, cwd: Path) -> list[Path]:
    """Existing script files named as words of a command or a shell script."""
    try:
        words = shlex.split(text, comments=True)
    except ValueError:
        words = text.split()
    out = []
    for w in words:
        for tok in re.split(r"[\s=;|&()<>\"'`{}$:,]+", w):
            tok = tok.lstrip("-")  # "${VAR:-default.py}" leaves "-default.py"
            if tok.endswith(_SCRIPT_EXT):
                p = Path(tok) if os.path.isabs(tok) else cwd / tok
                if p.is_file():
                    out.append(p.resolve())
    return out


def _py_imports(path: Path, root: Path) -> list[Path]:
    out = []
    try:
        src = path.read_text(errors="replace")
    except OSError:
        return out
    for m in re.finditer(r"^\s*(?:from\s+(\.*[\w.]*)\s+import\s+([\w, ]+)|import\s+([\w., ]+))",
                         src, re.MULTILINE):
        names = []
        if m.group(1) is not None:
            mod = m.group(1)
            base = path.parent if mod.startswith(".") else root
            mod = mod.lstrip(".")
            names.append((base, mod))
            for sub in m.group(2).split(","):  # "from pkg import module"
                if sub.strip():
                    names.append((base, f"{mod}.{sub.strip()}" if mod else sub.strip()))
        else:
            for mod in m.group(3).split(","):
                names.append((root, mod.strip().split(" ")[0]))
        for base, mod in names:
            if not mod:
                continue
            rel = Path(*mod.split("."))
            for cand in (base / f"{rel}.py", base / rel / "__init__.py"):
                if cand.is_file():
                    out.append(cand.resolve())
    return out


def producer_sources(start: list[Path], root: Path) -> list[Path]:
    """start + the local scripts they name + (recursively) the local Python
    modules the Python ones import, all inside root. Bounded."""
    seen: list[Path] = []
    todo = [p.resolve() for p in start if p.is_file()]
    while todo and len(seen) < _MAX_FILES:
        p = todo.pop()
        if p in seen:
            continue
        seen.append(p)
        if p.suffix == ".py":
            todo.extend(_py_imports(p, root))
        if p.suffix in (".sh", ".bash") or p in start:
            try:
                todo.extend(q for q in _scripts_in(p.read_text(errors="replace"), root)
                            if q not in seen)
            except OSError:
                pass
    return seen


def find_producers(log: str, explicit: list[str] | None) -> tuple[list[Path], Path]:
    """(the files that write this log, the folder their paths resolve in)."""
    logp = Path(log).resolve()
    if explicit:
        files = [Path(p).resolve() for p in explicit]
        return files, files[0].parent
    head = ""
    if logp.is_file():
        with open(logp, "rb") as f:
            head = f.read(65536).decode(errors="replace")
    for line in head.splitlines()[:20]:
        m = _QUEUE_HEADER.match(line)
        if m:
            cwd = Path(m.group(2))
            files = _scripts_in(m.group(1), cwd)
            q = logp.parent.parent.parent / "bin" / "gemma_queue.py"
            if q.is_file():
                files.append(q.resolve())
            return files, cwd
        m = _RUN_LOGGED_HEADER.match(line)
        if m:
            cwd = logp.parent.parent  # run_logged.sh writes <repo>/logs/
            files = _scripts_in(m.group(1), cwd)
            if (cwd / "run_logged.sh").is_file():
                files.append((cwd / "run_logged.sh").resolve())
            return files, cwd
    # A queue job that has not started yet: its log is <base>/logs/<id>.log and
    # the entry in <base>/queue.jsonl says what will write it (read only).
    if logp.parent.name == "logs" and (logp.parent.parent / "queue.jsonl").is_file():
        eid = logp.stem
        try:
            lines = (logp.parent.parent / "queue.jsonl").read_text().splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("id") == eid:
                cwd = Path(e.get("workdir") or ".")
                files = _scripts_in(e.get("command", ""), cwd)
                q = logp.parent.parent.parent / "bin" / "gemma_queue.py"
                if q.is_file():
                    files.append(q.resolve())
                return files, cwd
    return [], logp.parent


def self_test(log: str, pattern: str, explicit: list[str] | None = None) -> tuple[bool | None, str]:
    """(True = the pattern can appear, False = it cannot, None = cannot tell;
    one plain sentence)."""
    start, root = find_producers(log, explicit)
    if not start:
        return None, (f"self-test skipped: could not tell which program writes {log}; "
                      f"add --producer <script> to check the pattern")
    files = producer_sources(start, root)
    text = ""
    for p in files:
        try:
            text += p.read_text(errors="replace") + "\n"
        except OSError:
            pass
    fold = "(?i)" in pattern
    hay = text.lower() if fold else text
    missing_per_branch = []
    for words in pattern_branches(pattern):
        if not words:
            return None, "self-test skipped: the pattern has no plain word to look for"
        missing = [w for w in words if (w.lower() if fold else w) not in hay]
        if not missing:
            return True, f"self-test passed: the pattern's words appear in {len(files)} producer file(s)"
        missing_per_branch.append(missing)
    names = ", ".join(str(p) for p in start[:4])
    return False, (f"REFUSING: the end pattern {pattern!r} can never appear in {log}. "
                   f"The words {', '.join(repr(w) for w in missing_per_branch[0])} are "
                   f"printed by none of the {len(files)} file(s) that write this log "
                   f"(starting from {names}), so waiting would only end at the timeout. "
                   f"Look up the exact end line the program prints, or add "
                   f"--producer <script> if the log is written by something else. If "
                   f"the word comes from data rather than from the program (a text "
                   f"or file name the program prints), add --skip-self-test \"<reason>\".")


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
    ap.add_argument("--producer", action="append",
                    help="script that writes --log (repeatable); used by the pattern self-test")
    ap.add_argument("--skip-self-test", metavar="REASON",
                    help="do not check that --pattern can appear in --log; give the reason")
    a = ap.parse_args()

    if not (a.pid or (a.log and (a.pattern or a.quiet_secs)) or a.target):
        ap.error("give --pid, --log with --pattern/--quiet-secs, or --file")
    if a.pattern and not a.log:
        ap.error("--pattern needs --log")

    # Failure mode 4: a pattern the log's writer can never print.
    if a.pattern and not (a.skip_self_test or "").strip():
        ok, msg = self_test(a.log, a.pattern, a.producer)
        if ok is False:
            print(msg)
            return 1
        if ok is None:
            print(msg, file=sys.stderr)

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
