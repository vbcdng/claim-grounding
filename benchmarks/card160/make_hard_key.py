#!/usr/bin/env python3
"""Stability study (internal label CARD160): write benchmarks/card160/hard_rows_ground_truth.json from the hard
test text (benchmarks/card160/hard_rows/my_text.md) and the expected verdicts
below. No model call: the claim ids and texts come from the checker's own
splitter, so the key matches what a run produces.

  ./venv/bin/python3 benchmarks/card160/make_hard_key.py [--check]

--check exits 1 when the file on disk differs from what would be written.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)
from modules.papertrail import text_decomposer     # noqa: E402

TEXT = os.path.join(HERE, "hard_rows", "my_text.md")
OUT = os.path.join(HERE, "hard_rows_ground_truth.json")

# In text order: (expected verdict, why). The sources are constructed test
# documents (hard_rows/sources/), written so each expectation is unambiguous.
EXPECT = [
    ("supported", "the raccoon-dog sentence: an argument FOR a natural origin that "
                  "mentions a lab leak; the review states exactly this argument"),
    ("unsupported", "the raccoon-dog sentence read the wrong way round: the review argues "
                    "the opposite"),
    ("supported", "a claim about a claim: the review reports that Rootclaim argued this"),
    ("unsupported", "the same claim with the attribution removed: the review only reports "
                    "it, and rejects it"),
    ("supported", "a reported ruling: the review states that both judges found a natural "
                  "origin more likely"),
    ("unsupported", "a reported argument credited to the wrong people: commentators argued "
                    "it, the review's authors reject it"),
    ("supported", "the same reported argument credited correctly, with the review's "
                  "rejection"),
    ("supported", "a reported finding credited correctly (Neumark and Wascher argued it)"),
    ("unsupported", "the reported finding stated as fact: the paper reports a dispute and "
                    "sides against it"),
    ("unsupported", "the paper's own judgement turned into its opposite"),
]


def build():
    with open(TEXT, encoding="utf-8") as f:
        raw = f.read()
    _, body = text_decomposer.parse_references(raw, text_path=TEXT)
    claims = [c for c in text_decomposer.extract_claims(body) if c.get("markers")]
    if len(claims) != len(EXPECT):
        raise SystemExit(f"the splitter found {len(claims)} cited claims, the key "
                         f"lists {len(EXPECT)}")
    rows = [{"id": c["id"], "expect": exp, "note": why, "text": c["text"]}
            for c, (exp, why) in zip(claims, EXPECT)]
    return {"paper": "stability_hard_rows",
            "text_file": "benchmarks/card160/hard_rows/my_text.md",
            "created": "2026-10-02",
            "basis": ("Hard test sentences for the stability study: the raccoon-dog "
                      "sentence, which argues for a natural origin while mentioning a "
                      "laboratory leak, arguments a "
                      "writer reports from someone else, and claims about claims, each "
                      "with a twin that must be rejected. The two sources are "
                      "constructed test documents, not real publications."),
            "claims": rows}


def main(argv):
    key = build()
    text = json.dumps(key, ensure_ascii=False, indent=1) + "\n"
    if "--check" in argv:
        with open(OUT, encoding="utf-8") as f:
            return 0 if f.read() == text else 1
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"wrote {OUT}: {len(key['claims'])} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
