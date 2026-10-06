#!/usr/bin/env python3
"""Card #141 bookkeeping for run_gate_gemma.sh REUSE_FROM=<tag>.

  gate_reuse.py mark --out <dir>      remember where this test's lines start in
                                      <dir>/llm_calls.jsonl (<dir>/.reuse_start)
  gate_reuse.py summary --out <dir>   print "reused N of M calls" for the lines
                                      written since the mark

A folder's call log is appended to by every run into it, so the count starts
at the mark, never at the top of the file. No model call, no network.
"""
import argparse
import json
import os
import sys

MARK = ".reuse_start"


def _lines(path):
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [ln for ln in f if ln.strip()]


def mark(out):
    n = len(_lines(os.path.join(out, "llm_calls.jsonl")))
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, MARK), "w", encoding="utf-8") as f:
        f.write(str(n))
    return n


def summary(out):
    mp = os.path.join(out, MARK)
    if not os.path.isfile(mp):
        return "no count: this text was not run with REUSE_FROM in this test"
    with open(mp, encoding="utf-8") as f:
        start = int(f.read().strip() or 0)
    calls = reused = 0
    for ln in _lines(os.path.join(out, "llm_calls.jsonl"))[start:]:
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        calls += 1
        if rec.get("reused"):
            reused += 1
    return f"reused {reused} of {calls} calls"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("mark", "summary"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "mark":
        mark(a.out)
    else:
        print(summary(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
