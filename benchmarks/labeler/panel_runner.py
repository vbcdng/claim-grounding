#!/usr/bin/env python3
"""Task #15 labeling panel runner — several models, one rubric, full sources.

Each model in the panel independently judges every row in a rows.jsonl file
(built by build_round1_rows.py) with the strict rubric v1 prompt, reading the
row's FULL source text. Output is one JSONL line per (row, model) pair.

Hard rules baked in (see docs/TASK15_LOOP.md round 0 and task #37):
 - A refused / failed call is recorded as {"answered": false} — NEVER as a
   verdict. The run summary reports the refusal count.
 - Every proof quote is checked verbatim against the source text
   (whitespace/quote-mark/case normalized); the result is recorded per part
   as quote_verified, and never silently dropped.
 - Resume-safe: pairs already present in the output file are skipped, so an
   interrupted run continues instead of re-billing.

Usage:
  python3 benchmarks/labeler/panel_runner.py \
      --rows benchmarks/labeler/rounds/round1/rows.jsonl \
      --models claude-code/sonnet,gemini/gemma-4-31b-it,deepseek/deepseek-v4-flash \
      --out benchmarks/labeler/rounds/round1/verdicts.jsonl

`--limit N` judges only the first N rows (smoke test). `--dry-run` builds the
prompts and prints their sizes without calling anything.
"""
import argparse
import json
import os
import re
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

PROMPT_PATH = os.path.join(REPO, "benchmarks", "labeler", "prompts",
                           "pt_labeler_rubric_v1.txt")
RUBRIC_VERSION = "v1"
VALID_LABELS = {"pass", "fail_contradicted", "fail_unproven", "invalid"}
VALID_CLASSES = {"proven", "contradicted", "unproven", "tolerated"}
MAX_ATTEMPTS = 2  # one retry on malformed JSON; a refusal (None) is final
# Rows whose prompt exceeds this are recorded as no-answer, NEVER truncated:
# a silently-cut source would judge the claim against part of the evidence.
DEFAULT_MAX_PROMPT_CHARS = 480_000  # ~120k tokens, fits a 128k context window


def normalize(text):
    """Whitespace-collapse, unify quote marks and dashes, casefold — the same
    leniency a human eyeballing 'is this quote really in the source' applies."""
    text = re.sub(r"[‘’‚‛']", "'", text)
    text = re.sub(r"[“”„‟\"]", '"', text)
    text = re.sub(r"[‐-―−-]", "-", text)
    text = re.sub(r"\s+", " ", text)
    return text.casefold().strip()


def quote_in_sources(quote, sources):
    """Return the name of the source containing the quote, else None.
    Falls back to an alphanumeric-only comparison so PDF hyphenation and
    line-break artifacts don't fail an honestly-copied sentence."""
    if not quote:
        return None
    nq = normalize(quote)
    for s in sources:
        if nq and nq in normalize(s["text"]):
            return s["name"]
    bare_q = re.sub(r"[^a-z0-9]", "", nq)
    if len(bare_q) >= 20:
        for s in sources:
            if bare_q in re.sub(r"[^a-z0-9]", "", normalize(s["text"])):
                return s["name"]
    return None


def build_prompt(template, row):
    src_blocks = []
    for s in row["sources"]:
        src_blocks.append(f"--- SOURCE: {s['name']} ---\n{s['text']}")
    return (template
            .replace("{CLAIM}", row["claim_text"])
            .replace("{CONTEXT}", row.get("context") or "(none provided)")
            .replace("{SOURCES}", "\n\n".join(src_blocks)))


def validate_verdict(obj):
    """Return (cleaned_dict, None) or (None, reason)."""
    if not isinstance(obj, dict):
        return None, "response is not a JSON object"
    label = obj.get("strict_label")
    if label not in VALID_LABELS:
        return None, f"strict_label {label!r} not one of {sorted(VALID_LABELS)}"
    parts = obj.get("parts")
    if not isinstance(parts, list) or not parts:
        return None, "parts missing or empty"
    cleaned_parts = []
    for p in parts:
        if not isinstance(p, dict) or p.get("classification") not in VALID_CLASSES:
            return None, f"bad part entry: {p!r}"
        cleaned_parts.append({
            "part": str(p.get("part") or ""),
            "classification": p["classification"],
            "quote": p.get("quote") if isinstance(p.get("quote"), str) else None,
            "source": p.get("source") if isinstance(p.get("source"), str) else None,
        })
    return {"strict_label": label, "parts": cleaned_parts,
            "hard_note": obj.get("hard_note") if isinstance(obj.get("hard_note"), str) else None}, None


def judge_one(client, template, row, model,
              max_prompt_chars=DEFAULT_MAX_PROMPT_CHARS):
    """One (row, model) judgment. Returns the output record (always — a
    refusal becomes answered=False, never a verdict)."""
    prompt = build_prompt(template, row)
    base = {"row_id": row["row_id"], "model": model,
            "rubric_version": RUBRIC_VERSION, "prompt_chars": len(prompt),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if max_prompt_chars and len(prompt) > max_prompt_chars:
        return {**base, "answered": False,
                "reason": (f"source too long: prompt is {len(prompt):,} characters, "
                           f"over the {max_prompt_chars:,} cap — never truncated, "
                           "needs a longer-context model or a human plan")}
    last_reason = "no response from the model"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        raw = client.call(prompt, temperature=0.1, max_output_tokens=8000,
                          purpose="benchmark_labeler_panel", claim_id=row["row_id"])
        if raw is None:
            # A refused or failed call is "no answer" — final, no retry here:
            # llm_client already retried transport errors internally.
            return {**base, "answered": False,
                    "reason": "call refused or failed (client returned nothing)"}
        from modules.papertrail.llm_client import extract_json
        verdict, err = validate_verdict(extract_json(raw))
        if verdict is not None:
            for p in verdict["parts"]:
                p["quote_verified_in"] = quote_in_sources(p["quote"], row["sources"])
                p["quote_verified"] = p["quote_verified_in"] is not None
            return {**base, "answered": True, **verdict,
                    "attempts": attempt}
        last_reason = f"malformed answer: {err}"
    return {**base, "answered": False, "reason": last_reason}


def load_rows(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def load_done(out_path):
    done = set()
    if os.path.exists(out_path):
        with open(out_path) as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    done.add((rec["row_id"], rec["model"]))
    return done


def run_panel(rows, models, out_path, client_factory=None, template=None,
              progress=print, max_prompt_chars=DEFAULT_MAX_PROMPT_CHARS):
    """Core loop, injectable for offline tests: client_factory(model) must
    return an object with .call(prompt, ..., purpose=, claim_id=) -> str|None."""
    if template is None:
        with open(PROMPT_PATH) as f:
            template = f.read()
    if client_factory is None:
        from modules.papertrail.llm_client import LLMClient

        def client_factory(m):
            # Provider key-file fallback (LLMClient's own fallback is
            # gemini-only; mirrors arbiter.resolve_key, plus openrouter):
            key_path = None
            for prefix, env, path in (
                    ("deepseek/", "DEEPSEEK_API_KEY",
                     os.path.join(REPO, "config", "deepseek_api_key.txt")),
                    ("openrouter/", "OPENROUTER_API_KEY",
                     os.path.join(REPO, "config", "openrouter_api_key.txt"))):
                if m.startswith(prefix) and not os.environ.get(env) \
                        and os.path.exists(path):
                    key_path = path
            return LLMClient(model=m, api_key=key_path)
    done = load_done(out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    counts = {"judged": 0, "refused": 0, "skipped": 0}
    with open(out_path, "a") as out:
        for model in models:
            client = client_factory(model)
            for row in rows:
                if (row["row_id"], model) in done:
                    counts["skipped"] += 1
                    continue
                rec = judge_one(client, template, row, model,
                                max_prompt_chars=max_prompt_chars)
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                if rec["answered"]:
                    counts["judged"] += 1
                    progress(f"{model} {row['row_id']}: {rec['strict_label']}")
                else:
                    counts["refused"] += 1
                    progress(f"{model} {row['row_id']}: NO ANSWER ({rec['reason']})")
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True)
    ap.add_argument("--models", required=True,
                    help="comma-separated model names, one per panel seat")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-prompt-chars", type=int,
                    default=DEFAULT_MAX_PROMPT_CHARS,
                    help="rows over this become no-answer, never truncated; 0 = no cap")
    ap.add_argument("--dry-run", action="store_true",
                    help="build prompts, print sizes, call nothing")
    a = ap.parse_args()

    rows = load_rows(a.rows)
    if a.limit:
        rows = rows[:a.limit]
    models = [m.strip() for m in a.models.split(",") if m.strip()]

    if a.dry_run:
        with open(PROMPT_PATH) as f:
            template = f.read()
        total = 0
        for row in rows:
            n = len(build_prompt(template, row))
            total += n
            print(f"{row['row_id']}: prompt {n:,} chars (~{n // 4:,} tokens)")
        print(f"TOTAL per model: {total:,} chars (~{total // 4:,} tokens); "
              f"x {len(models)} models = ~{total * len(models) // 4:,} input tokens")
        return 0

    counts = run_panel(rows, models, a.out,
                       max_prompt_chars=a.max_prompt_chars)
    print(f"done: {counts['judged']} judged, {counts['refused']} NO-ANSWER, "
          f"{counts['skipped']} already present (resumed)")
    if counts["refused"]:
        print("WARNING: refusals above are recorded as no-answer, not as verdicts; "
              "check them before quoting any number from this run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
