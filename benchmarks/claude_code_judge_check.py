#!/usr/bin/env python3
"""Stream E plumbing check: re-judge a run's hard-ground-truth claims through the
REAL claude-code backend path — LLMClient("claude-code/haiku") subprocess dispatch,
matcher._vote_support majority voting, and the Haiku-tuned combined rubric via
matcher.PROMPT_OVERRIDES — against rich evidence (cosine top-K sentences per cited
source, the study's round-2/3 shape).

This reproduces docs/HAIKU_VS_GEMINI_JUDGE.md round 3 through the production code
path. It is NOT independent validation of the tuned prompt (train-on-test — see the
study's caveats; the clean held-out check is post-7/7 queue #2). $0 API spend.

  venv/bin/python3 benchmarks/claude_code_judge_check.py \
      --run-dir data/paper1_verification --ground-truth benchmarks/paper1_ground_truth.json
"""
import os
import sys
import json
import glob
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher, embeddings  # noqa: E402
from modules.papertrail.llm_client import LLMClient, parallel_map  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOP_SENTS = 10   # the study's "rich evidence" shape


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default="data/paper1_verification")
    ap.add_argument("--ground-truth", default="benchmarks/paper1_ground_truth.json")
    ap.add_argument("--model", default="claude-code/haiku")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="only the first N hard-GT claims")
    args = ap.parse_args()

    with open(os.path.join(args.run_dir, "analysis.json"), encoding="utf-8") as f:
        analysis = json.load(f)
    with open(args.ground_truth, encoding="utf-8") as f:
        gt = {c["id"]: c for c in json.load(f)["claims"]
              if c.get("expect") in ("supported", "unsupported")}
    sources = {}
    for p in glob.glob(os.path.join(args.run_dir, "source_claims", "*.json")):
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        sources[d["paper_id"]] = d

    claims = [c for c in analysis["text_claims"] if c["id"] in gt and c.get("paper_ids")]
    if args.limit:
        claims = claims[:args.limit]

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

    def judge(c):
        passage = rich_passage(c["text"], c["paper_ids"])
        if not passage.strip():
            return c["id"], None, "no evidence assembled"
        ok, reason, tally = matcher._vote_support(
            llm, prompt.replace("{CLAIM}", c["text"]).replace("{PASSAGE}", passage))
        return c["id"], ok, f"{tally}: {reason[:100]}"

    results = parallel_map(judge, claims, workers=args.workers)
    agree = fp = fn = skipped = 0
    for cid, ok, note in results:
        if ok is None:
            skipped += 1
            print(f"{cid}: SKIPPED ({note})")
            continue
        want = gt[cid]["expect"] == "supported"
        mark = "OK " if ok == want else ("FP!" if ok and not want else "FN ")
        if ok == want:
            agree += 1
        elif ok:
            fp += 1
        else:
            fn += 1
        print(f"{cid}: {mark} judged={'sup' if ok else 'unsup'} expect={gt[cid]['expect']} ({note})")
    n = agree + fp + fn
    print(f"\n{n} judged: {agree} agree ({100*agree/max(n,1):.0f}%), "
          f"{fp} FALSE POSITIVES, {fn} false negatives, {skipped} skipped")
    print("(FP = judged supported where the hand audit says unsupported — "
          "the failure the project treats as unacceptable; the study saw 0.)")
    sys.exit(1 if fp else 0)


if __name__ == "__main__":
    main()
