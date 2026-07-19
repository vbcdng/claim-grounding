#!/usr/bin/env python3
"""Error-injection eval for a judge backend (queue #2 groundwork; recipe adapted
from sciwrite-lint's evals — reimplemented, no vendored code).

Idea: take a finished run's SUPPORTED claims, deterministically corrupt each one
so it is guaranteed false w.r.t. its sources (scale a number ~3x, flip a
direction word), and judge original + corrupted through the same rich-evidence
path. A trustworthy judge accepts the original and catches the injection. This
scales judge validation past the hand-audited papers — any finished run works —
and it needs NO human audit and (with --model claude-code/haiku) NO API spend.

The number that matters: MISSED INJECTIONS (corrupted claim judged supported) —
the false-positive analogue this project treats as unacceptable.

  venv/bin/python3 benchmarks/error_injection_eval.py \
      --run-dir data/paper1_verification --model claude-code/haiku
"""
import os
import re
import sys
import json
import glob
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher, embeddings  # noqa: E402
from modules.papertrail.llm_client import LLMClient, parallel_map  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOP_SENTS = 10

# Direction/polarity flips: applied to the FIRST match only, whole words,
# case-preserved where it matters. Flipping any one of these on a supported
# claim falsifies it against the same evidence.
FLIPS = [
    ("grew", "shrank"), ("grow", "shrink"), ("rose", "fell"), ("rises", "falls"),
    ("increase", "decrease"), ("increasing", "decreasing"), ("increased", "decreased"),
    ("more", "less"), ("higher", "lower"), ("faster", "slower"), ("largest", "smallest"),
    ("lowest", "highest"), ("exceeds", "falls below"), ("passed", "stayed below"),
    ("most", "fewest"), ("majority", "minority"), ("widening", "narrowing"),
    ("supports", "refutes"), ("falls", "rises"), ("concentrated", "dispersed"),
]
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def inject(text: str):
    """Return (corrupted_text, kind) or (None, None) when no safe injection exists."""
    m = _NUM_RE.search(text)
    if m:
        raw = m.group(0)
        try:
            val = float(raw.replace(",", ""))
            if val > 0:
                scaled = val * 3.7                      # far outside any rounding rule
                new = str(int(scaled)) if raw.isdigit() else f"{scaled:.1f}"
                return text[:m.start()] + new + text[m.end():], f"number {raw}->{new}"
        except ValueError:
            pass
    low = text.lower()
    for a, b in FLIPS:
        i = low.find(a)
        while i != -1:                                   # whole-word match only
            before_ok = i == 0 or not low[i - 1].isalpha()
            after_ok = i + len(a) >= len(low) or not low[i + len(a)].isalpha()
            if before_ok and after_ok:
                return text[:i] + b + text[i + len(a):], f"flip {a}->{b}"
            i = low.find(a, i + 1)
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default="data/paper1_verification")
    ap.add_argument("--model", default="claude-code/haiku")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--haiku-rubric", action="store_true", default=True,
                    help="use benchmarks/pt_combined_judgment_haiku_v1.txt (default)")
    ap.add_argument("--production-rubric", dest="haiku_rubric", action="store_false")
    args = ap.parse_args()

    with open(os.path.join(args.run_dir, "analysis.json"), encoding="utf-8") as f:
        analysis = json.load(f)
    sources = {}
    for p in glob.glob(os.path.join(args.run_dir, "source_claims", "*.json")):
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        sources[d["paper_id"]] = d

    if args.haiku_rubric:
        matcher.PROMPT_OVERRIDES["pt_combined_judgment_prompt.txt"] = os.path.join(
            REPO_ROOT, "benchmarks", "pt_combined_judgment_haiku_v1.txt")
    prompt = matcher._load_prompt("pt_combined_judgment_prompt.txt")
    llm = LLMClient(model=args.model)
    emb_dir = os.path.join(args.run_dir, "embeddings")

    def rich_passage(claim_text, pids):
        parts = []
        for pid in pids:
            src = sources.get(pid)
            if not src:
                continue
            sents = [s.get("text", "") for s in src.get("sentences", [])]
            cache = os.path.join(emb_dir, f"{pid}.sents.npz")
            if sents and os.path.exists(cache):
                row = embeddings.cosine_matrix([claim_text], sents, b_cache_file=cache)[0]
                top = sorted(range(len(sents)), key=lambda j: -row[j])[:TOP_SENTS]
                body = " ".join(sents[j] for j in sorted(top))
            else:
                body = " ".join(sents[:TOP_SENTS])
            parts.append(f"From {src.get('title') or pid}: {body}")
        return "\n\n".join(parts)

    pairs = []
    for c in analysis["text_claims"]:
        if c.get("verdict") != "supported" or not c.get("paper_ids"):
            continue
        bad, kind = inject(c["text"])
        if bad:
            pairs.append((c, bad, kind))
    if args.limit:
        pairs = pairs[:args.limit]
    print(f"{len(pairs)} supported claims with a safe injection "
          f"(model {args.model}, rubric {'haiku_v1' if args.haiku_rubric else 'production'})")

    def judge_pair(item):
        c, bad, kind = item
        # rank evidence against the ORIGINAL for both judgments — the corrupted
        # claim must be caught on the same evidence the original is judged on
        passage = rich_passage(c["text"], c["paper_ids"])
        if not passage.strip():
            return c["id"], None, None, kind
        def verdict(t):
            ok, _, tally = matcher._vote_support(
                llm, prompt.replace("{CLAIM}", t).replace("{PASSAGE}", passage))
            return ok, tally
        return c["id"], verdict(c["text"]), verdict(bad), kind

    results = parallel_map(judge_pair, pairs, workers=args.workers)
    kept = caught = missed = dropped_orig = skipped = 0
    for cid, orig, bad, kind in results:
        if orig is None:
            skipped += 1
            continue
        o_ok, o_t = orig
        b_ok, b_t = bad
        kept += 1 if o_ok else 0
        dropped_orig += 0 if o_ok else 1
        caught += 0 if b_ok else 1
        missed += 1 if b_ok else 0
        flagbits = []
        if not o_ok:
            flagbits.append("over-refused original")
        if b_ok:
            flagbits.append("MISSED INJECTION")
        print(f"{cid}: orig={'sup' if o_ok else 'unsup'}({o_t}) "
              f"injected={'sup' if b_ok else 'unsup'}({b_t}) [{kind}]"
              + (f"  <-- {', '.join(flagbits)}" if flagbits else ""))
    n = kept + dropped_orig
    print(f"\n{n} pairs: originals accepted {kept}/{n}, "
          f"injections caught {caught}/{n}, MISSED {missed}, skipped {skipped}")
    print("(missed injection = corrupted claim judged supported — the unacceptable direction)")
    sys.exit(1 if missed else 0)


if __name__ == "__main__":
    main()
