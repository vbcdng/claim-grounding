#!/usr/bin/env python3
"""Card #105 — where exactly did the piece-by-piece reading lose a row?

Makes no model calls. It reads the answers already on disk and, for every
control row where the author has ruled and the piece-by-piece reading gave a
different answer, prints the claim, the ruling, and the list of checkable parts
the reading worked from.

It also runs one mechanical check that needs no judgement at all: if a part was
marked "the source never says this", but that part's own words appear inside a
quote the same reading already checked word for word against the source, then
the reading contradicted itself. Such a part was provable from a sentence the
reading had in its hands.

Usage:
  python3 benchmarks/labeler/card105_diagnose_control_misses.py \
      --control benchmarks/labeler/rounds/round3/control_gemma_piece_read.jsonl \
      --whole benchmarks/labeler/rounds/round1/verdicts_gemma.jsonl \
      --rulings benchmarks/labeler/strict_labels.jsonl
"""
import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from benchmarks.labeler.compare_piece_read import load_rulings  # noqa: E402
from benchmarks.labeler.compare_read_modes import LABEL_WORDS, load  # noqa: E402


def _word(label):
    return LABEL_WORDS.get(label, label or "no answer")


def _norm(text):
    """Lower-case and squeeze runs of whitespace, so a line break inside a
    quoted sentence does not hide a match."""
    return " ".join((text or "").lower().split())


def self_contradicting_parts(parts):
    """Parts marked unproven whose own words sit inside a verified quote.

    A part counts only when some OTHER part of the same claim was proven by a
    quote that was checked word for word against the source and that contains
    this part's text. Returns one entry per such part."""
    verified = [
        p for p in parts
        if p.get("quote") and p.get("quote_verified")
    ]
    found = []
    for part in parts:
        if part.get("classification") != "unproven":
            continue
        text = _norm(part.get("part"))
        if not text:
            continue
        for other in verified:
            if other is part:
                continue
            if text in _norm(other.get("quote")):
                found.append({
                    "part": part.get("part"),
                    "already_quoted_for": other.get("part"),
                    "quote": other.get("quote"),
                })
                break
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--control", required=True)
    ap.add_argument("--whole", required=True)
    ap.add_argument("--rulings", required=True)
    a = ap.parse_args()

    rulings = load_rulings(a.rulings)
    control = load(a.control)
    whole = load(a.whole)

    total = 0
    wrong = 0
    contradictions = 0
    single_piece = 0
    for (row_id, model), rec in sorted(control.items()):
        ruling = rulings.get(row_id)
        if ruling is None:
            continue
        total += 1
        if (rec.get("pieces") or 0) <= 1:
            single_piece += 1
        piece_label = rec.get("strict_label") if rec.get("answered") else None
        if piece_label == ruling["label"]:
            continue
        wrong += 1
        whole_rec = whole.get((row_id, model)) or {}
        whole_label = (whole_rec.get("strict_label")
                       if whole_rec.get("answered") else None)
        print("=" * 72)
        print(f"{row_id}  ({rec.get('pieces')} piece(s) of the source)")
        print(f"  the author ruled : {_word(ruling['label'])}  ({ruling['date']})")
        print(f"  read piece by piece: {_word(piece_label)}   <-- wrong")
        print(f"  read whole        : {_word(whole_label)}")
        if ruling.get("reason"):
            print(f"  the author's reason: {ruling['reason']}")
        print("  the parts the reading worked from:")
        for part in rec.get("parts") or []:
            print(f"    [{part.get('classification')}] {part.get('part')}")
        bad = self_contradicting_parts(rec.get("parts") or [])
        contradictions += len(bad)
        for entry in bad:
            print(f"  !! the part \"{entry['part']}\" was called unproven, but its "
                  f"own words are inside a quote this reading already checked "
                  f"against the source, used for the part "
                  f"\"{entry['already_quoted_for']}\":")
            print(f"     {entry['quote'][:300]}")

    print("=" * 72)
    print(json.dumps({
        "control_rows_the_author_ruled_on": total,
        "rows_the_piece_reading_got_wrong": wrong,
        "rows_whose_source_fitted_one_piece": single_piece,
        "parts_called_unproven_that_a_verified_quote_already_contained":
            contradictions,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
