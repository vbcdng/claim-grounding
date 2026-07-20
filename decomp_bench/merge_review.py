#!/usr/bin/env python3
"""Merge an edited review file (ALL or SAMPLE) back into its JSONL.

Reads only id, H, L and note from each line (the claim text is an echo).
The JSONL is rewritten in place; a .bak of the previous version is kept.

Usage: merge_review.py <review_..._SAMPLE.txt> [--jsonl path]  (--jsonl is
inferred from the review filename when omitted)
"""
import argparse
import json
import os
import re
import shutil
import sys

LINE_RE = re.compile(r"^(?P<id>\S+) \| H:\[(?P<h>[^\]]*)\] \| L:\[(?P<l>[^\]]*)\]")
# short form returned by the external LLM rater (see prompt_v1.txt):
#   <id> | L:[y] | reason: <=10 words
SHORT_RE = re.compile(r"^(?P<id>\S+)\s*\|\s*L:\[(?P<l>[^\]]*)\]"
                      r"(?:\s*\|\s*reason:\s*(?P<reason>.*))?$")


def norm(v: str):
    v = v.strip().lower()
    if v in ("y", "x", "ok", "yes"):
        return "y"
    if v in ("n", "no", "bad"):
        return "n"
    return None


def infer_jsonl(review_path: str):
    name = os.path.basename(review_path)
    m = re.match(r"review_(.+)_(ALL|SAMPLE)\.txt$", name)
    if not m:
        return None
    return os.path.join(os.path.dirname(os.path.abspath(review_path)),
                        m.group(1) + ".jsonl")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("review")
    ap.add_argument("--jsonl")
    a = ap.parse_args()

    jsonl = a.jsonl or infer_jsonl(a.review)
    if not jsonl or not os.path.exists(jsonl):
        sys.exit(f"cannot find the JSONL for {a.review}; pass --jsonl")

    edits = {}       # id -> (h, l, note, llm_reason)
    bad = 0
    for line in open(a.review, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line.strip():
            continue
        m = LINE_RE.match(line)
        if m:
            note = (line.rsplit("| note:", 1)[1].strip()
                    if "| note:" in line else "")
            edits[m.group("id")] = (norm(m.group("h")), norm(m.group("l")),
                                    note, None)
            continue
        s = SHORT_RE.match(line)
        if s:
            edits[s.group("id")] = (None, norm(s.group("l")), "",
                                    (s.group("reason") or "").strip())
            continue
        bad += 1
    if bad:
        print(f"warning: {bad} unparseable line(s) skipped")

    rows = [json.loads(l) for l in open(jsonl, encoding="utf-8") if l.strip()]
    changed = 0
    for r in rows:
        e = edits.get(r["claim_id"])
        if not e:
            continue
        h, l, note, reason = e
        new = (h if h is not None else r["human_ok"],
               l if l is not None else r["llm_ok"],
               note or r.get("note", ""),
               reason if reason is not None else r.get("llm_reason", ""))
        old = (r["human_ok"], r["llm_ok"], r.get("note", ""),
               r.get("llm_reason", ""))
        if new != old:
            (r["human_ok"], r["llm_ok"], r["note"], r["llm_reason"]) = new
            changed += 1

    shutil.copy2(jsonl, jsonl + ".bak")
    with open(jsonl, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"merged {changed} updated row(s) into {jsonl} "
          f"({len(edits)} review lines read; backup: {jsonl}.bak)")


if __name__ == "__main__":
    main()
