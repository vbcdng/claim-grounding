#!/usr/bin/env python3
"""Check the repaired Citation-Integrity batches — task #32, round 2.

Independent of `ci_sibling_repair.py`: it re-reads what was written to disk and
re-derives everything through the production reader (`text_decomposer`), so a
bug in the builder cannot hide itself. No network, no AI model, no cost.

The 2026-08-01 attempt at this repair broke rows in ways nobody noticed until
the benchmark was scored — 5 rows lost the citation under test, others shrank to
fragments like "and the US". Every check below exists to catch one of those.

  1. wording      the claim's text is byte-identical to the original
  2. citation     the row's own marker survives and is on that claim
  3. no split     the paragraph yields the same number of claims as before
  4. markers      every marker resolves to a source file that exists and is real
  5. distinct     no sibling file is a copy of the paper under test
  6. flags        `fair_question` agrees with what is actually on disk

Usage:
    python3 benchmarks/ci_sibling_repair_check.py [--suffix _repaired]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "modules"))

from papertrail.text_decomposer import (extract_claims,      # noqa: E402
                                        parse_references)

CI_DIR = os.path.join(ROOT, "data", "citation_integrity")
BATCHES = ("pilot100", "fresh50")


def claims_of(path):
    with open(path, encoding="utf-8") as f:
        body = f.read()
    body = re.sub(r"^#.*\n", "", body, count=1)
    return extract_claims(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suffix", default="_repaired")
    args = ap.parse_args()

    failures, checked = [], 0
    for batch in BATCHES:
        src = os.path.join(CI_DIR, f"batch_dev_{batch}")
        out = os.path.join(CI_DIR, f"batch_dev_{batch}{args.suffix}")
        if not os.path.isdir(out):
            failures.append(f"{batch}: repaired batch directory is missing")
            continue

        with open(os.path.join(src, "ci_ground_truth.json"), encoding="utf-8") as f:
            before = json.load(f)["claims"]
        with open(os.path.join(out, "ci_ground_truth.json"), encoding="utf-8") as f:
            after = json.load(f)["claims"]

        old_claims = {tuple(c.get("markers") or []): c for c in
                      claims_of(os.path.join(src, "my_text.md"))}
        old_by_key = {}
        for markers, c in old_claims.items():
            for m in markers:
                old_by_key.setdefault(m, c)
        new_claims = claims_of(os.path.join(out, "my_text.md"))
        new_by_key = {}
        for c in new_claims:
            for m in (c.get("markers") or []):
                new_by_key.setdefault(m, c)

        n_old = len(claims_of(os.path.join(src, "my_text.md")))
        if len(new_claims) != n_old:
            failures.append(f"{batch}: the repaired text yields {len(new_claims)} "
                            f"claims, the original {n_old}")

        refs, _ = parse_references("", os.path.join(out, "my_text.md.refs.txt"),
                                   os.path.join(out, "my_text.md"))

        own_texts = {}
        for key, row in after.items():
            checked += 1
            rep = row.get("repair", {})
            old, new = old_by_key.get(key), new_by_key.get(key)

            if new is None:
                failures.append(f"{batch}:{key} check 2 — the citation under test "
                                f"is not on any claim in the repaired text")
                continue
            if old is not None and (old.get("text") or "") != (new.get("text") or ""):
                failures.append(f"{batch}:{key} check 1 — the claim's wording changed")
            expected = {key} | set(rep.get("sibling_keys") or [])
            actual = set(new.get("markers") or [])
            if actual != expected:
                failures.append(f"{batch}:{key} check 2 — markers on the claim are "
                                f"{sorted(actual)}, expected {sorted(expected)}")

            for marker in actual:
                path = refs.get(marker)
                if not path:
                    failures.append(f"{batch}:{key} check 4 — marker [[{marker}]] "
                                    f"is not in the references file")
                    continue
                full = path if os.path.isabs(path) else os.path.join(out, "sources",
                                                                     os.path.basename(path))
                if not os.path.exists(full) or os.path.getsize(full) < 1000:
                    failures.append(f"{batch}:{key} check 4 — source file for "
                                    f"[[{marker}]] is missing or too small")
                else:
                    with open(full, encoding="utf-8") as f:
                        own_texts.setdefault(marker, f.read())

            under_test = own_texts.get(key, "")
            for sk in (rep.get("sibling_keys") or []):
                if own_texts.get(sk, "!") == under_test:
                    failures.append(f"{batch}:{key} check 5 — sibling {sk} is a copy "
                                    f"of the paper under test")

            span = row.get("annotated_span") or ""
            multi = "<|multi_cit|>" in span or "<|other_cit|>" in span
            fair_should = (not multi) or (rep.get("status") == "repaired"
                                          and not rep.get("unfetchable"))
            if bool(rep.get("fair_question")) != bool(fair_should):
                failures.append(f"{batch}:{key} check 6 — fair_question is "
                                f"{rep.get('fair_question')}, should be {fair_should}")

    print(f"claims checked: {checked}")
    if failures:
        print(f"\nFAILED — {len(failures)} problem(s):")
        for f in failures[:60]:
            print("   " + f)
        if len(failures) > 60:
            print(f"   ... and {len(failures) - 60} more")
        return 1
    print("\nAll six checks passed on every claim in both repaired batches:")
    print("  1 wording unchanged   2 citation under test kept   3 no extra split")
    print("  4 every marker has a real source file   5 siblings are different papers")
    print("  6 the fairness flag matches what is on disk")
    return 0


if __name__ == "__main__":
    sys.exit(main())
