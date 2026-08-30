#!/usr/bin/env python3
"""Homegrown reflective prompt-optimization loop
(docs/PROMPT_OPTIMIZATION_PLAN_2026-07-30.md, option 2).

Each round: score the incumbent prompt on the TRAIN set, show its failures to
a reflector model (default claude-code/sonnet, $0), get a full revised prompt
back, and accept the revision only if it improves the metric on TRAIN and
then on DEV (score = hits - 5*false_positives; the FP weight is the plan's
non-negotiable kill-class asymmetry). Every candidate, accepted or rejected,
is written to the run dir with its scores — nothing is silent.

The author-ruled gold labels and the 11-item judge bench are NEVER inputs
here; they stay the held-out final gate (standing rule).

Usage:
  venv/bin/python3 benchmarks/prompt_opt/reflect_loop.py \
      --model openrouter/qwen/qwen3.5-flash-02-23 \
      --seed-prompt benchmarks/prompt_variants/pt_gemma_v2_restructure.txt \
      --train train.jsonl --dev dev.jsonl \
      --rounds 8 --outdir <run dir> [--reflector claude-code/sonnet]
"""
import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
from modules.papertrail.llm_client import LLMClient                   # noqa: E402
from benchmarks.prompt_opt.eval_prompt import (                       # noqa: E402
    eval_once, key_for, load_items)

BEGIN, END = "<<<PROMPT>>>", "<<<END>>>"

REFLECT_TEMPLATE = """You are optimizing a prompt for a small, cheap LLM \
("the judge") whose job is: given a CLAIM from a piece of cited writing and a \
PASSAGE assembled from the cited sources, answer whether the passage supports \
the claim. The judge model is weaker than you and takes instructions very \
literally.

Below is the CURRENT PROMPT and a sample of the judge's FAILURES on a \
labeled training set when run with it.

Failure kinds:
- FN (false negative): the passage does support the claim (label true) but \
the judge said false — usually over-strictness: demanding exact wording, \
treating paraphrase/entailment/rounding as a gap, or punishing the writer's \
own framing voice.
- FP (false positive): the passage does NOT support the claim (label false) \
but the judge said true. THIS IS 5x WORSE THAN A FALSE NEGATIVE. Typical \
traps: the claim strengthens the passage (reduced->eliminated, some->all, \
requested->refused), changes a number, negates a mechanism, or swaps \
cause and effect.

Your task: produce a REVISED prompt for the judge that fixes as many \
failures as possible WITHOUT introducing false positives. Rules:
1. Keep the placeholders {CLAIM} and {PASSAGE} exactly as written, each on \
its own line where the inputs get inserted.
2. Keep the output contract verbatim: the judge must return ONLY a JSON \
object {"supported": true or false, "reason": "<one short sentence>"}.
3. Full rewrite allowed — structure, ordering, wording, worked examples. \
Length: stay under ~700 words; the judge gets confused by walls of text.
4. Never weaken the false-positive guardrails to buy false-negative wins: \
strengthenings, changed numbers, absent events, and reversed causation must \
stay rejected.
5. Do not mention the training set, these instructions, or any failure IDs \
in the prompt itself.

REVISION HISTORY (what was already tried this run and how it scored — do \
not repeat a failed idea verbatim):
<<HISTORY>>

CURRENT PROMPT:
---
<<CURRENT_PROMPT>>
---

FAILURES (id | kind | claim | judge's stated reason):
<<FAILURES>>

Think briefly about the failure pattern, then output the revised prompt \
between <<<PROMPT>>> and <<<END>>> markers, nothing else after the end \
marker."""


def failure_block(rows, max_rows=24):
    fails = [r for r in rows if not r["correct"]]
    fps = [r for r in fails if r["got"]]
    fns = [r for r in fails if not r["got"]]
    # FPs all shown (kill class), FNs fill the rest
    shown = fps[:max_rows] + fns[: max(0, max_rows - len(fps))]
    lines = []
    for r in shown:
        kind = "FP" if r["got"] else "FN"
        lines.append(f"- {r['id']} | {kind} | CLAIM: {r['claim_text'][:300]} | "
                     f"judge said: {r['reason'][:160]}")
    if len(fails) > len(shown):
        lines.append(f"... plus {len(fails) - len(shown)} more failures not shown")
    return "\n".join(lines) if lines else "(no failures)"


def attach_claims(rows, items_by_id):
    for r in rows:
        r["claim_text"] = items_by_id[r["id"]]["claim"]
    return rows


def extract_prompt(text):
    if not text or BEGIN not in text:
        return None
    body = text.split(BEGIN, 1)[1]
    if END in body:
        body = body.split(END, 1)[0]
    body = body.strip()
    if "{CLAIM}" not in body or "{PASSAGE}" not in body:
        return None
    if '"supported"' not in body:
        return None
    return body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--seed-prompt", required=True)
    ap.add_argument("--train", required=True)
    ap.add_argument("--dev", required=True)
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--reflector", default="claude-code/sonnet")
    ap.add_argument("--concurrency", type=int, default=6)
    a = ap.parse_args()

    os.makedirs(a.outdir, exist_ok=True)
    train = load_items(a.train)
    dev = load_items(a.dev)
    train_by_id = {it["id"]: it for it in train}
    with open(a.seed_prompt, encoding="utf-8") as f:
        best_prompt = f.read()

    log_path = os.path.join(a.outdir, "loop_log.jsonl")

    def log(rec):
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def score(prompt_text, items, tag):
        return eval_once(a.model, prompt_text, items, a.concurrency,
                         repeat_tag=tag)

    print(f"scoring seed on train ({len(train)}) + dev ({len(dev)})...")
    best_train = score(best_prompt, train, "seed-train")
    best_dev = score(best_prompt, dev, "seed-dev")
    if best_train["empties"] or best_dev["empties"]:
        # an exhausted-retries call parses as "unsupported" — fake-correct on
        # negatives, fake-FN on positives; a poisoned seed poisons every
        # acceptance decision downstream, so abort instead of optimizing noise
        print(f"SEED EVAL POISONED by empty responses "
              f"(train={best_train['empties']} dev={best_dev['empties']}) — "
              f"aborting; re-run when the provider pool frees")
        log({"round": 0, "event": "seed_poisoned",
             "train_empties": best_train["empties"],
             "dev_empties": best_dev["empties"]})
        sys.exit(3)
    print(f"seed: train score={best_train['score']} "
          f"(hits={best_train['hits']} FP={best_train['false_positives']}) | "
          f"dev score={best_dev['score']} "
          f"(hits={best_dev['hits']} FP={best_dev['false_positives']})")
    with open(os.path.join(a.outdir, "prompt_best.txt"), "w", encoding="utf-8") as f:
        f.write(best_prompt)
    log({"round": 0, "event": "seed", "train_score": best_train["score"],
         "dev_score": best_dev["score"],
         "train": {k: best_train[k] for k in ("hits", "false_positives",
                                              "false_negatives", "empties")},
         "dev": {k: best_dev[k] for k in ("hits", "false_positives",
                                          "false_negatives", "empties")}})

    reflector = LLMClient(model=a.reflector)
    history = []

    for rnd in range(1, a.rounds + 1):
        print(f"\n=== round {rnd}/{a.rounds} ===")
        fails = failure_block(attach_claims(best_train["rows"], train_by_id))
        hist_text = "\n".join(history[-6:]) or "(first round)"
        # .replace, not .format — the template legitimately contains literal
        # braces ({CLAIM}, {PASSAGE}, the JSON contract)
        ask = (REFLECT_TEMPLATE.replace("<<HISTORY>>", hist_text)
                               .replace("<<CURRENT_PROMPT>>", best_prompt)
                               .replace("<<FAILURES>>", fails))
        raw = reflector.call(ask, temperature=0.7, max_output_tokens=8000)
        candidate = extract_prompt(raw)
        cand_path = os.path.join(a.outdir, f"prompt_r{rnd:02d}.txt")
        if candidate is None:
            print("  reflector output invalid (markers/placeholders/contract) — skipping round")
            log({"round": rnd, "event": "invalid_candidate"})
            history.append(f"round {rnd}: reflector produced an invalid prompt (rejected)")
            continue
        with open(cand_path, "w", encoding="utf-8") as f:
            f.write(candidate)

        cand_train = score(candidate, train, f"r{rnd}-train")
        print(f"  candidate train score={cand_train['score']} "
              f"(hits={cand_train['hits']} FP={cand_train['false_positives']}) "
              f"vs best {best_train['score']}")
        if cand_train["empties"]:
            print(f"  eval poisoned ({cand_train['empties']} empty responses) "
                  f"— round discarded, candidate NOT judged")
            log({"round": rnd, "event": "poisoned_eval",
                 "empties": cand_train["empties"], "prompt_file": cand_path})
            history.append(f"round {rnd}: eval hit provider rate limits "
                           f"(not the prompt's fault) — discarded, retry the idea")
            continue
        if cand_train["score"] <= best_train["score"]:
            log({"round": rnd, "event": "rejected_train",
                 "train_score": cand_train["score"], "prompt_file": cand_path})
            history.append(f"round {rnd}: train score {cand_train['score']} "
                           f"(<= best {best_train['score']}) — rejected on train; "
                           f"FP={cand_train['false_positives']}")
            continue

        cand_dev = score(candidate, dev, f"r{rnd}-dev")
        print(f"  candidate dev score={cand_dev['score']} "
              f"(hits={cand_dev['hits']} FP={cand_dev['false_positives']}) "
              f"vs best {best_dev['score']}")
        if cand_dev["empties"]:
            print(f"  dev eval poisoned ({cand_dev['empties']} empty responses) "
                  f"— round discarded")
            log({"round": rnd, "event": "poisoned_eval_dev",
                 "empties": cand_dev["empties"], "prompt_file": cand_path})
            history.append(f"round {rnd}: dev eval hit provider rate limits "
                           f"(not the prompt's fault) — discarded, retry the idea")
            continue
        # non-regression on dev (not strict improvement): dev can saturate
        # near-perfect at seed, and a train win with an equal dev score is
        # still a win; only a dev DROP signals overfitting to train
        if cand_dev["score"] < best_dev["score"]:
            log({"round": rnd, "event": "rejected_dev",
                 "train_score": cand_train["score"],
                 "dev_score": cand_dev["score"], "prompt_file": cand_path})
            history.append(f"round {rnd}: improved train "
                           f"({cand_train['score']}) but dev dropped to "
                           f"{cand_dev['score']} (best {best_dev['score']}) "
                           f"— rejected (overfit to train)")
            continue

        best_prompt, best_train, best_dev = candidate, cand_train, cand_dev
        with open(os.path.join(a.outdir, "prompt_best.txt"), "w",
                  encoding="utf-8") as f:
            f.write(best_prompt)
        log({"round": rnd, "event": "accepted",
             "train_score": cand_train["score"], "dev_score": cand_dev["score"],
             "train": {k: cand_train[k] for k in ("hits", "false_positives",
                                                  "false_negatives", "empties")},
             "dev": {k: cand_dev[k] for k in ("hits", "false_positives",
                                              "false_negatives", "empties")},
             "prompt_file": cand_path})
        history.append(f"round {rnd}: ACCEPTED — train {cand_train['score']}, "
                       f"dev {cand_dev['score']}")
        print("  ACCEPTED as new best")

    # stability check on the final best (plan rule: spread >=2 disqualifies)
    print("\nfinal stability check: 3 identical dev runs of the best prompt")
    stab = [score(best_prompt, dev, f"stab-{i+1}") for i in range(3)]
    scores = [s["score"] for s in stab]
    spread = max(scores) - min(scores)
    verdict = "STABLE" if spread < 2 else "UNSTABLE (spread >= 2 — disqualifying)"
    print(f"stability scores={scores} spread={spread} -> {verdict}")
    log({"event": "stability", "scores": scores, "spread": spread,
         "verdict": verdict,
         "fps": [s["false_positives"] for s in stab],
         "empties": [s["empties"] for s in stab]})
    print(f"\nbest prompt -> {os.path.join(a.outdir, 'prompt_best.txt')}"
          f"\nlog -> {log_path}")


if __name__ == "__main__":
    main()
