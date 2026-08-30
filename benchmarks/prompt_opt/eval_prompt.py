#!/usr/bin/env python3
"""Score a judge prompt on a train/dev item set — the prompt-optimization
loop's evaluator (docs/PROMPT_OPTIMIZATION_PLAN_2026-07-30.md).

Items: JSONL rows {"id": str, "expected": bool, "claim": str, "passage": str}
(the judge_bench item shape). The prompt file must contain {CLAIM} and
{PASSAGE} placeholders and demand the {"supported": ..., "reason": ...} JSON
contract that matcher._parse_support reads.

Metric (plan, non-negotiable): score = hits - 5*false_positives. A false
positive is expected=False judged True — the kill class. Empty/unparseable
responses count as False (production behavior) AND are tallied separately:
they are a stability smell, not a win.

Usage:
  venv/bin/python3 benchmarks/prompt_opt/eval_prompt.py \
      openrouter/qwen/qwen3.5-flash-02-23 <prompt.txt> <items.jsonl> \
      [--repeats 1] [--concurrency 6] [--out report.json] [--quiet]

Exit code 0 always (scores are data, not gates). LIVE API — pennies per eval
at cheap-tier prices.
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
from modules.papertrail.llm_client import LLMClient          # noqa: E402
from modules.papertrail.matcher import _parse_support        # noqa: E402


def key_for(model):
    """config/<provider>_api_key.txt if present, else None (env-var fallback).
    Inlined copy of judge_bench.key_for — judge_bench.py reads sys.argv at
    module level and is not importable."""
    provider = model.split("/")[0]
    fname = "google_api_key.txt" if provider == "gemini" else f"{provider}_api_key.txt"
    path = os.path.join(REPO, "config", fname)
    return path if os.path.exists(path) else None


def load_items(path):
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    for it in items:
        for field in ("id", "expected", "claim", "passage"):
            if field not in it:
                sys.exit(f"item missing '{field}': {json.dumps(it)[:120]}")
    return items


def eval_once(model, template, items, concurrency, repeat_tag=""):
    llm = LLMClient(model=model, api_key=key_for(model))

    def one(it):
        raw = llm.call(template.replace("{CLAIM}", it["claim"])
                               .replace("{PASSAGE}", it["passage"]),
                       temperature=0.0, max_output_tokens=2048)
        got, reason, _ = _parse_support(raw)
        return {"id": it["id"], "expected": it["expected"], "got": got,
                "correct": got == it["expected"], "reason": reason,
                "empty": raw is None}

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        rows = list(ex.map(one, items))

    hits = sum(r["correct"] for r in rows)
    fps = sum(1 for r in rows if not r["expected"] and r["got"])
    fns = sum(1 for r in rows if r["expected"] and not r["got"])
    empties = sum(r["empty"] for r in rows)
    return {"repeat": repeat_tag, "n": len(rows), "hits": hits,
            "false_positives": fps, "false_negatives": fns,
            "empties": empties, "score": hits - 5 * fps, "rows": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("prompt_path")
    ap.add_argument("items_path")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--out", help="write full JSON report here")
    ap.add_argument("--quiet", action="store_true",
                    help="summary lines only, no per-item failure listing")
    a = ap.parse_args()

    with open(a.prompt_path, encoding="utf-8") as f:
        template = f.read()
    if "{CLAIM}" not in template or "{PASSAGE}" not in template:
        sys.exit("prompt file lacks {CLAIM}/{PASSAGE} placeholders")
    items = load_items(a.items_path)

    runs = []
    for rep in range(a.repeats):
        t0 = time.time()
        res = eval_once(a.model, template, items, a.concurrency,
                        repeat_tag=f"r{rep + 1}")
        runs.append(res)
        print(f"[{res['repeat']}] {a.model} on {os.path.basename(a.items_path)} "
              f"({res['n']} items, {time.time() - t0:.0f}s): "
              f"hits={res['hits']} FP={res['false_positives']} "
              f"FN={res['false_negatives']} empties={res['empties']} "
              f"score={res['score']}")
        if not a.quiet:
            for r in res["rows"]:
                if not r["correct"]:
                    kind = "FP" if r["got"] else "FN"
                    print(f"    {kind} {r['id']}: {r['reason'][:90]}")

    scores = [r["score"] for r in runs]
    summary = {
        "model": a.model,
        "prompt": os.path.abspath(a.prompt_path),
        "items": os.path.abspath(a.items_path),
        "repeats": a.repeats,
        "scores": scores,
        "spread": max(scores) - min(scores),
        "min_score": min(scores),
        "any_fp": any(r["false_positives"] for r in runs),
        "runs": runs,
    }
    if a.repeats > 1:
        print(f"scores across repeats: {scores}  spread={summary['spread']}")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=1)
        print(f"report -> {a.out}")


if __name__ == "__main__":
    main()
