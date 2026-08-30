#!/usr/bin/env python3
"""Build a blind-grading packet for a Citation-Integrity batch: no labels anywhere in it.

A *blind reader* is a strong model that reads one benchmark row — the citing
sentence plus the whole cited paper — and gives its own verdict without seeing
the benchmark's label or the tool's verdict. Its purpose is to catch rows where
the benchmark's own answer key is wrong. This script writes the packet such a
reader works from: one task file per row, the cited papers, and an empty votes
directory. The packet deliberately contains NO labels and no tool output, which
is what makes the reading blind.

    python3 benchmarks/ci_blind_packet.py \
        --batch data/citation_integrity/batch_dev_fresh50 \
        --out data/citation_integrity/c0_packet_fresh50

Pure: no API calls, no network.

The rubric below is byte-identical to the one the first blind reading used on
2026-07-30 (it was hardcoded in scratchpad/build_c0_packet.py, which this script
replaces). Do NOT edit it without a new label era: the whole point of reusing it
is that a new reader's votes stay comparable with the votes already collected.
`tests/test_ci_blind_packet.py` regenerates the frozen pilot100 packet and fails
if a single task file changes.
"""
import argparse
import json
import os
import re
import shutil
import sys
import textwrap

RUBRIC = """## What to decide

Read the cited paper, then give ONE of two labels for the claim above:

- **pass** — the cited paper supports the claim as written. Every substantive
  part of the claim (the finding, the mechanism, the population, any number,
  the direction of the effect) is backed by the paper's own text.
- **flag** — anything else.

Rules:
- Judge ONLY the claim, not the rest of the paragraph. The paragraph is context
  so you can resolve pronouns and hedges, nothing more.
- The claim may be a clause carved out of a longer sentence. Judge it as the
  proposition it states.
- Be strict about substance, not wording. A faithful paraphrase passes; a
  changed number, a stronger effect than the paper reports, a different
  population, or a mechanism the paper did not show is a flag.
- If the claim is a general background statement that the paper does establish,
  that passes. If the paper is simply about something else, that is a flag.
- Base the decision on the cited paper's own text only. Do not use outside
  knowledge about whether the statement is true in the world.

## Output for this claim

One JSON object:

{
  "id": "<the id at the top of this file>",
  "vote": "pass" | "flag",
  "defect": null when vote is pass, otherwise exactly one of:
      "not_in_source"                  - the paper says nothing that bears on the claim
      "unrelated_source"               - the paper is about a different topic entirely
      "contradicted"                   - the paper says something incompatible with the claim
      "overstated"                     - the paper's finding is weaker/narrower/hedged
      "partially_supported"            - part of the claim is backed, another substantive part is not
      "content_present_but_secondhand" - the statement IS in the paper, but as something the
                                         paper cites, reviews or attributes to other work
                                         rather than reports as its own finding,
  "quote": "verbatim sentence from the cited paper your decision rests on (<=40 words, empty string if nothing relevant exists)",
  "reason": "one or two sentences",
  "confidence": "high" | "medium" | "low"
}

The quote must be copied character-for-character from the cited paper.
"""

# The line the reader is pointed at the source with. `ci_blind_reader.py` finds
# this paragraph and swaps it for the paper's actual text, because a one-turn
# headless call cannot open a file and answer in the same turn.
SOURCE_POINTER = ("Full text: `sources/{cid}.txt` (relative to the packet directory). Read it.\n"
                  "It is a real biomedical/scientific article, {chars} characters.")


def task_body(cid, row):
    """The grading task for one row — the exact text a blind reader is given."""
    para = row["citing_paragraph"].replace("<|multi_cit|>", "THE CITED PAPER")
    para = para.replace("<|cit|>", "THE CITED PAPER").replace("<|other_cit|>", "another reference")
    para = re.sub(r"\s+\n", "\n", para).strip()
    claim = row["claim_text"].replace(f"[[{cid}]]", "[THE CITED PAPER]")
    pointer = SOURCE_POINTER.format(cid=cid, chars=row["source_chars"])
    return f"""# Grading task {cid}

A sentence in a scientific paper cites another paper. Your job: decide whether
the cited paper actually supports what the citing sentence says.

## The claim under test

{claim}

## The paragraph it came from (context only)

{para}

## The cited paper

{pointer}

{RUBRIC}
"""


def build(batch, out):
    gt = json.load(open(os.path.join(batch, "ci_ground_truth.json")))
    rows = gt["claims"]
    for sub in ("tasks", "sources", "votes"):
        os.makedirs(os.path.join(out, sub), exist_ok=True)

    for cid, row in sorted(rows.items()):
        open(os.path.join(out, "tasks", f"{cid}.md"), "w").write(task_body(cid, row))
        shutil.copyfile(os.path.join(batch, "sources", f"{cid}.txt"),
                        os.path.join(out, "sources", f"{cid}.txt"))

    open(os.path.join(out, "README.md"), "w").write(textwrap.dedent(f"""\
        # Blind grading packet — {os.path.basename(os.path.normpath(batch))}

        {len(rows)} grading tasks in `tasks/`, the cited papers in `sources/`, votes go to
        `votes/` (or a per-reader `votes_<reader>_<date>/`). This packet deliberately
        contains NO benchmark labels and no tool output: it is the blind label check.
        Regenerate with:

            python3 benchmarks/ci_blind_packet.py --batch {batch} --out {out}
        """))
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True, help="a converted Citation-Integrity batch dir")
    ap.add_argument("--out", required=True, help="packet directory to write")
    a = ap.parse_args()
    n = build(a.batch, a.out)
    print(f"wrote {n} tasks + {n} sources to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
