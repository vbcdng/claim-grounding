#!/usr/bin/env python3
"""Stability study (CARD160): check from the files that no verdict of a finished
run came from a refused or failed model call. No model calls.

Two independent sources, per run and text:
  1. analysis.json: every claim tested with stability_table.failed_claim (the
     counting program's own rule: judge_error, or a "no LLM response" reason),
     plus the count of claims with an extra check failed (checks_failed).
  2. the gate logs: the last "REFUSED CALLS PER RUN" block of every log whose
     name starts with the run's tag, and the run's retry report
     (.card160_retry_report.json) if one exists.

Usage: ./venv/bin/python3 benchmarks/card160/check_runs_clean.py [--root data]
       [--logs logs] [--json out.json]
Prints one line per run; exit 1 if any run has a failed-call verdict.
"""

import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import interim_table as it        # noqa: E402
import stability_table as st      # noqa: E402

RUNS = ["card160paidr1", "card160paidr2", "card160paidr3", "card160paidprompt",
        "card160paidshuffle", "card160paidbias", "card160paiddoubt", "card160paidreworded",
        "card160paidnegated", "card160freer1", "card160freer2", "card160freer3",
        "card160sonnetr1", "card160sonnetr2", "card160sonnetr3",
        "card160haikur1", "card160haikur2", "card160haikur3"]

# The three free runs were jobs of our queue of free runs; their logs are named by job id.
QUEUE_DIR = os.path.expanduser("~/.config/claim-grounding/gemma_queue/logs")
QUEUE_LOGS = {"card160freer1": "q157", "card160freer2": "q156", "card160freer3": "q155"}


def refused_block(log_path):
    """The lines of the last REFUSED CALLS PER RUN block in a log, or None."""
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    starts = [i for i, l in enumerate(lines) if "REFUSED CALLS PER RUN" in l]
    if not starts:
        return None
    out = []
    for l in lines[starts[-1] + 1:]:
        if not l.strip() or l.startswith("==="):
            if out:
                break
            continue
        out.append(l.strip())
    return out


def refused_numbers(block):
    """Every integer that follows 'refused' or a colon in the block's lines."""
    nums = []
    for l in block or []:
        m = re.search(r"(\d+)\s*(refused|failed)", l) or re.search(r":\s*(\d+)", l)
        if m:
            nums.append(int(m.group(1)))
    return nums


def check(root, logs_dir):
    rows = []
    for tag in RUNS:
        texts = it.tag_texts(tag)
        failed, checks, claims, retry = [], 0, 0, []
        for text in texts:
            a = st.load(root, tag, text)
            if a is None:
                failed.append(f"{text}: no analysis.json")
                continue
            for c in a.get("text_claims", []):
                if c.get("verdict") in ("own", "omitted"):
                    continue
                claims += 1
                if st.failed_claim(c):
                    failed.append(f"{text}/{c.get('id')}")
                if c.get("checks_failed"):
                    checks += 1
            rep = os.path.join(st.run_dir(root, tag, text), ".card160_retry_report.json")
            if os.path.isfile(rep):
                with open(rep, encoding="utf-8") as f:
                    retry.append(f"{text}: {json.load(f).get('reason', '?')}")
        logs = sorted(glob.glob(os.path.join(logs_dir, tag + "_*.log")))
        if tag in QUEUE_LOGS:     # free runs ran through the Gemma queue
            logs.append(os.path.join(QUEUE_DIR, QUEUE_LOGS[tag] + ".log"))
        blocks = {os.path.basename(p): refused_block(p) for p in logs}
        rows.append({"run": tag, "texts": len(texts), "judged_claims": claims,
                     "failed_call_verdicts": failed, "claims_with_extra_check_failed": checks,
                     "retry_reports": retry, "logs": blocks})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.path.join(REPO, "data"))
    ap.add_argument("--logs", default=os.path.join(REPO, "logs"))
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    rows = check(args.root, args.logs)
    bad = 0
    for r in rows:
        bad += bool(r["failed_call_verdicts"])
        last = [b for b in r["logs"].values() if b]
        print(f"{r['run']}: {r['texts']} texts, {r['judged_claims']} judged claims, "
              f"failed-call verdicts {len(r['failed_call_verdicts'])}"
              + (f" {r['failed_call_verdicts']}" if r["failed_call_verdicts"] else "")
              + f", extra check failed on {r['claims_with_extra_check_failed']}"
              + f", retry reports {r['retry_reports'] or 'none'}"
              + f", logs {len(r['logs'])} ({sum(1 for b in r['logs'].values() if b)} with a refused-calls block)")
        for name, b in r["logs"].items():
            if b:
                print(f"    {name}: " + " | ".join(b))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
