#!/usr/bin/env python3
"""Aggregate the C0 blind grading votes and compare them with the Citation-Integrity labels.

Pure: no API calls, no network. Reads the blind vote files written by the graders
plus the batch's ground truth, and prints agreement under both mappings
(strict / grounding) with a per-fine-label breakdown and the full disagreement list.

    python3 benchmarks/ci_blind_compare.py \
        --batch data/citation_integrity/batch_dev_pilot100 \
        --packet data/citation_integrity/c0_packet \
        --out data/citation_integrity/c0_blind_sonnet.json

The graders never see the ground truth: the packet directory holds no labels.
This script is the only place the two are joined.
"""
import argparse, glob, json, os, re, sys
from collections import Counter, defaultdict

# a flag whose defect is only about provenance counts as "pass" under the
# grounding mapping, mirroring how the benchmark's INDIRECT labels are treated.
PROVENANCE_DEFECTS = {"content_present_but_secondhand"}


def load_votes(packet, votes_dir=None):
    """Votes from `<packet>/votes/` unless a per-reader directory is given.

    One packet is read by several readers (2026-08-03: Sonnet and Opus), so each
    reader gets its own `votes_<reader>_<date>/` directory and the packet's
    original `votes/` keeps the first reading. Files whose name starts with `_`
    are scratch (e.g. `_prompt_sample.txt`), never votes.
    """
    votes, dupes = {}, []
    pattern = os.path.join(votes_dir or os.path.join(packet, "votes"), "*.json")
    for path in sorted(glob.glob(pattern)):
        if os.path.basename(path).startswith("_"):
            continue
        rows = json.load(open(path))
        if isinstance(rows, dict):
            rows = rows.get("votes", [rows])
        for r in rows:
            cid = r["id"]
            if cid in votes:
                dupes.append(cid)
            r["_file"] = os.path.basename(path)
            votes[cid] = r
    return votes, dupes


def sides(vote):
    """(strict_side, grounding_side) for one blind vote."""
    if vote["vote"] == "pass":
        return "pass", "pass"
    if vote.get("defect") in PROVENANCE_DEFECTS:
        return "flag", "pass"
    return "flag", "flag"


_CURLY = {"‘": "'", "’": "'", "“": '"', "”": '"',
          "′": "'", "´": "'"}
# "^" too: extraction loses superscripts, so the paper's "LOD = 103" is the
# reader's faithful "LOD = 10^3".
_DROP = set(" \t\r\n-‐‑‒–—−^")
_ELLIPSIS = re.compile(r"\.\.\.|…")


def squash(s):
    """Comparison form for the verbatim gate: same characters, no typography.

    Extracted source text carries artefacts a reader silently repairs when it
    copies a sentence — a line break inside a word leaves "SARS- CoV-2", the PDF
    uses curly apostrophes. Judging a quote fabricated over that would be wrong,
    so the gate compares text with whitespace, hyphens and quote shapes removed:
    a real invention still fails, because every other character must still match
    in order.
    """
    s = str(s).lower()
    for k, v in _CURLY.items():
        s = s.replace(k, v)
    return "".join(ch for ch in s if ch not in _DROP)


def check_quotes(votes, batch):
    """Verbatim gate: a quote must appear in the cited paper it claims to come from.

    Three outcomes, because they mean different things and only the first is a
    reason to distrust a vote:

    - `bad` — text that is not in the paper at all. The vote rests on something
      the reader did not read there.
    - `typography_only` — matches once hyphenation, quote shapes and lost
      superscripts are normalised: a faithful copy of badly extracted text.
    - `stitched` — the reader joined two real passages with "...". Every piece is
      in the paper, but the quote is not one continuous sentence, so it cannot be
      shown to a human as one.
    """
    bad, typo, stitched = [], [], []
    for cid, v in sorted(votes.items()):
        q = (v.get("quote") or "").strip()
        if not q:
            continue
        src = open(os.path.join(batch, "sources", f"{cid}.txt")).read()
        norm = lambda s: " ".join(s.split())
        if q in src or norm(q) in norm(src):
            continue
        sq_src = squash(src)
        if squash(q) in sq_src:
            typo.append(cid)
            continue
        pieces = [p for p in _ELLIPSIS.split(q) if len(squash(p)) > 10]
        if len(pieces) > 1 and all(squash(p) in sq_src for p in pieces):
            stitched.append(cid)
        else:
            bad.append(cid)
    return bad, typo, stitched


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True)
    ap.add_argument("--packet", required=True)
    ap.add_argument("--votes-dir", help="per-reader votes dir (default <packet>/votes)")
    ap.add_argument("--grader", default="claude-sonnet (blind)",
                    help="what to record as the reader's name in --out")
    ap.add_argument("--out")
    a = ap.parse_args()

    gt = json.load(open(os.path.join(a.batch, "ci_ground_truth.json")))
    rows = gt["claims"]
    votes, dupes = load_votes(a.packet, a.votes_dir)

    missing = sorted(set(rows) - set(votes))
    extra = sorted(set(votes) - set(rows))
    print(f"rows in batch: {len(rows)}   blind votes found: {len(votes)}")
    if dupes:
        print(f"  DUPLICATE votes (last wins): {sorted(set(dupes))}")
    if missing:
        print(f"  MISSING votes ({len(missing)}): {', '.join(missing)}")
    if extra:
        print(f"  votes for unknown ids: {', '.join(extra)}")

    bad_quotes, typo_quotes, stitched_quotes = check_quotes(votes, a.batch)
    print(f"  quotes not found in the cited paper at all: {len(bad_quotes)}"
          + (f" -> {', '.join(bad_quotes)}" if bad_quotes else ""))
    if typo_quotes:
        print(f"  quotes that match apart from typography (hyphenation, curly "
              f"quotes, lost superscripts) — faithful copies of badly extracted "
              f"text: {len(typo_quotes)} -> {', '.join(typo_quotes)}")
    if stitched_quotes:
        print(f"  quotes that join two real passages with '...' — every piece is "
              f"in the paper, but it is not one continuous sentence: "
              f"{len(stitched_quotes)} -> {', '.join(stitched_quotes)}")

    graded = [c for c in sorted(rows) if c in votes]
    per_mapping = {}
    for mapping in ("strict", "grounding"):
        agree = disagree_over = disagree_under = 0
        by_label = defaultdict(Counter)
        rowsout = []
        for cid in graded:
            r, v = rows[cid], votes[cid]
            gt_side = r[f"{mapping}_side"]
            blind_side = sides(v)[0 if mapping == "strict" else 1]
            if gt_side == blind_side:
                agree += 1
                kind = "agree"
            elif gt_side == "flag":
                # benchmark says defective, blind grader saw no problem
                disagree_over += 1
                kind = "blind_passed_a_flagged_row"
            else:
                disagree_under += 1
                kind = "blind_flagged_an_accurate_row"
            by_label[r["label"]][blind_side] += 1
            rowsout.append({"id": cid, "label": r["label"], "gt_side": gt_side,
                            "blind_side": blind_side, "kind": kind,
                            "defect": v.get("defect"), "confidence": v.get("confidence")})
        n = len(graded)
        per_mapping[mapping] = {"n": n, "agree": agree,
                                "blind_passed_a_flagged_row": disagree_over,
                                "blind_flagged_an_accurate_row": disagree_under,
                                "agreement_pct": round(100 * agree / n, 1) if n else None,
                                "by_label": {k: dict(v) for k, v in by_label.items()},
                                "rows": rowsout}

    for mapping, d in per_mapping.items():
        print(f"\n=== {mapping} mapping ===")
        print(f"blind grader agrees with the benchmark label on {d['agree']}/{d['n']}"
              f"  ({d['agreement_pct']}%)")
        print(f"  benchmark flagged, blind grader passed : {d['blind_passed_a_flagged_row']}")
        print(f"  benchmark ACCURATE, blind grader flagged: {d['blind_flagged_an_accurate_row']}")
        print("  per benchmark label (blind pass / blind flag):")
        for label, c in sorted(d["by_label"].items(), key=lambda kv: -sum(kv[1].values())):
            print(f"    {label:<22} pass {c.get('pass',0):>3}   flag {c.get('flag',0):>3}")

    print("\n=== disagreements (strict mapping) ===")
    for r in per_mapping["strict"]["rows"]:
        if r["kind"] != "agree":
            print(f"  {r['id']}  label={r['label']:<20} blind={r['blind_side']:<4}"
                  f" conf={r['confidence']:<6} defect={r['defect']}")

    if a.out:
        json.dump({"batch": a.batch, "packet": a.packet, "grader": a.grader,
                   "votes_dir": a.votes_dir or os.path.join(a.packet, "votes"),
                   "n_rows": len(rows), "n_votes": len(votes), "missing": missing,
                   "bad_quotes": bad_quotes, "typography_only_quotes": typo_quotes,
                   "stitched_quotes": stitched_quotes,
                   "mappings": per_mapping,
                   "votes": {c: votes[c] for c in graded}},
                  open(a.out, "w"), indent=1)
        print(f"\nwrote {a.out}")
    return 1 if (missing or bad_quotes) else 0


if __name__ == "__main__":
    sys.exit(main())
