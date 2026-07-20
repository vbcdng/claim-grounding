#!/usr/bin/env python3
"""Opus grader pass over Fable gold labels (pilot harness, 2026-07-17).

Re-judges each gold-labeled claim with the owner-grader prompt
(config/prompts/pt_owner_grader_v2.txt) — the exact prompt shape the arbiter
and the gate_v2 grader exam use: {CLAIM} + {SHOWN} (everything the viewer
displays as evidence) + {CONTEXT} (full source text, or the best contiguous
~20k-word section of a long source). Prompt assembly is REUSED from
modules/papertrail/arbiter.py (_shown_block / _source_blocks) so the grader
sees exactly what a real arbiter/grader call sees; proof quotes go through
arbiter.verify_quotes (the mandatory verbatim gate).

Input : a gold-label JSONL (benchmarks/gold_labels/*.jsonl). Rows are looked
        up by claim_id in data/<run>/analysis.json (run field of the row).
Output: opus_pass/<batch>_graded.jsonl — one row per gold row with the
        grader's verdict/action/components/proofs plus the Fable + pipeline
        labels copied over for the agreement table.

Resume is deterministic: rows whose claim_id is already in the output file
are skipped (delete the output line to re-grade one claim).

Usage:
  venv/bin/python benchmarks/gold_labels/opus_pass/run_grader.py \
      benchmarks/gold_labels/paper1_hard_2026-07-17.jsonl \
      [--model claude-code/opus] [--limit 20] [--workers 3] [--data-root data]

$0 by default: claude-code/opus runs on the local claude CLI subscription.
NEVER point this at a Gemini model — the pass is budget-free by design.
"""
import argparse
import json
import os
import sys
import threading

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

from modules.papertrail import arbiter                      # noqa: E402
from modules.papertrail.llm_client import LLMClient, extract_json, parallel_map  # noqa: E402

PROMPT_PATH = os.path.join(PROJECT_ROOT, "config", "prompts", "pt_owner_grader_v2.txt")

# Provability axis used for the agreement table: the grader grades the DISPLAY
# (rule B: provable-nearby is never "supported"), while the Fable gold labels
# grade SUBSTANCE (is the claim provable from the cited source at all). The
# comparable signal is the grader's action:
#   supported / wrong_or_insufficient_evidence  -> every component provable  -> "provable"
#   add_citation_or_rewrite                     -> some component not provable -> "not_provable"
FABLE_PROVABLE = {"supported", "supported_minor_caveat"}
FABLE_NOT_PROVABLE = {"partial", "partial_weak", "unsupported",
                      "own_interpretation", "own_argument"}


def fable_axis(v):
    if v in FABLE_PROVABLE:
        return "provable"
    if v in FABLE_NOT_PROVABLE:
        return "not_provable"
    return "n/a"                          # not_rulable etc.


def grader_axis(action):
    if action in ("supported", "wrong_or_insufficient_evidence"):
        return "provable"
    if action == "add_citation_or_rewrite":
        return "not_provable"
    return "n/a"


def load_run(data_root, run_name, cache):
    """(claims_by_id, sources_by_pid) for a run dir, cached."""
    if run_name in cache:
        return cache[run_name]
    run_dir = os.path.join(data_root, run_name)
    analysis = json.load(open(os.path.join(run_dir, "analysis.json"), encoding="utf-8"))
    claims = {tc.get("id"): tc for tc in analysis.get("text_claims", [])}
    sources = {}
    sc_dir = os.path.join(run_dir, "source_claims")
    if os.path.isdir(sc_dir):
        for fn in os.listdir(sc_dir):
            if fn.endswith(".json"):
                try:
                    d = json.load(open(os.path.join(sc_dir, fn), encoding="utf-8"))
                    sources[d.get("paper_id")] = d
                except Exception:
                    continue
    cache[run_name] = (claims, sources)
    return cache[run_name]


def grade_one(row, tpl, llm, data_root, run_cache, temperature=0.0,
              max_output_tokens=3000):
    claims, sources = load_run(data_root, row["run"], run_cache)
    tc = claims.get(row["claim_id"])
    if tc is None:
        return {"claim_id": row["claim_id"], "run": row["run"],
                "error": "claim_id not found in analysis.json"}
    prompt = (tpl.replace("{CLAIM}", tc.get("text", ""))
                 .replace("{SHOWN}", arbiter._shown_block(tc))
                 .replace("{CONTEXT}", arbiter._source_blocks(tc, sources)))
    raw = llm.call(prompt, temperature=temperature,
                   max_output_tokens=max_output_tokens)
    j = extract_json(raw)
    if not isinstance(j, dict) or j.get("action") not in (
            "supported", "add_citation_or_rewrite", "wrong_or_insufficient_evidence"):
        return {"claim_id": row["claim_id"], "run": row["run"],
                "error": "unparseable grader reply", "raw": (raw or "")[:400]}
    src_norm = arbiter._norm(" ".join(
        s.get("text", "") for pid in arbiter._claim_pids(tc)
        for s in (sources.get(pid) or {}).get("sentences", []) or []))
    proofs, dropped = arbiter.verify_quotes(j.get("proof_sentences") or [], src_norm)
    out = {
        "claim_id": row["claim_id"],
        "run": row["run"],
        "grader_model": llm.model,
        "grader_verdict": j.get("verdict"),
        "grader_action": j["action"],
        "grader_components": j.get("components") or [],
        "grader_missing_subclaim": (j.get("missing_subclaim") or "").strip(),
        "grader_rewrite_suggestion": (j.get("rewrite_suggestion") or "").strip(),
        "grader_proofs_verified": proofs,
        "grader_quotes_dropped": dropped,
        "grader_why": (j.get("why") or "").strip(),
        # copied through for the agreement table:
        "fable_verdict": row.get("fable_verdict"),
        "fable_reasoning": row.get("reasoning"),
        "pipeline_verdict": row.get("pipeline_verdict"),
        "fable_axis": fable_axis(row.get("fable_verdict")),
        "grader_axis": grader_axis(j["action"]),
    }
    fa, ga = out["fable_axis"], out["grader_axis"]
    out["axis_agreement"] = "n/a" if "n/a" in (fa, ga) else \
        ("agree" if fa == ga else "DISAGREE")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gold_jsonl", help="gold-label JSONL (read-only input)")
    ap.add_argument("--model", default="claude-code/opus",
                    help="grader model (default claude-code/opus, $0 on the CLI)")
    ap.add_argument("--limit", type=int, default=0, help="grade only the first N rows")
    ap.add_argument("--workers", type=int, default=3,
                    help="parallel calls (claude-code ceiling is ~6; keep low)")
    ap.add_argument("--data-root", default=os.path.join(PROJECT_ROOT, "data"))
    ap.add_argument("--temperature", type=float, default=0.0,
                    help="sampling temperature (kimi-k3 requires exactly 1)")
    ap.add_argument("--max-output-tokens", type=int, default=3000,
                    help="per-call output budget (reasoning models may need "
                         "16000+ on large-context rows)")
    ap.add_argument("--api-key", default=None,
                    help="API key value or path to a key file (LLMClient resolves)")
    ap.add_argument("--api-base", default=None,
                    help="OpenAI-compatible endpoint, e.g. https://api.moonshot.ai/v1")
    ap.add_argument("--prompt", default=PROMPT_PATH,
                    help="grader prompt file (default pt_owner_grader_v2.txt; "
                         "a non-default prompt also suffixes the output file "
                         "so v2/v3 passes never share a resume file)")
    ap.add_argument("--out", default=None,
                    help="output JSONL (default opus_pass/<batch>_graded.jsonl)")
    a = ap.parse_args()

    if a.model.startswith("gemini"):
        sys.exit("Refusing to run the gold-label grader pass on Gemini API money. "
                 "Use claude-code/opus (default) or another $0/off-budget backend.")

    rows = [json.loads(l) for l in open(a.gold_jsonl, encoding="utf-8") if l.strip()]
    if a.limit:
        rows = rows[:a.limit]

    base = os.path.splitext(os.path.basename(a.gold_jsonl))[0]
    prompt_tag = os.path.splitext(os.path.basename(a.prompt))[0]
    suffix = "" if os.path.abspath(a.prompt) == os.path.abspath(PROMPT_PATH) \
        else f"_{prompt_tag}"
    out_path = a.out or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     f"{base}_graded{suffix}.jsonl")
    # Resume: only rows that graded CLEANLY count as done — error rows are
    # retried on the next invocation (the aggregator keeps the last row per
    # claim_id, so a retried row supersedes its earlier error line).
    done = set()
    if os.path.exists(out_path):
        for l in open(out_path, encoding="utf-8"):
            if l.strip():
                try:
                    r = json.loads(l)
                    if not r.get("error"):
                        done.add((r.get("run"), r["claim_id"]))
                except Exception:
                    pass
    todo = [r for r in rows if (r.get("run"), r["claim_id"]) not in done]
    print(f"{len(rows)} gold rows, {len(done)} already graded, {len(todo)} to grade "
          f"with {a.model} -> {out_path}")
    if not todo:
        return

    tpl = open(a.prompt, encoding="utf-8").read()
    llm = LLMClient(model=a.model, api_key=a.api_key, api_base=a.api_base)
    run_cache, lock = {}, threading.Lock()
    consec_errors = [0]          # hard-fail guard: throttle is retried inside the
    MAX_CONSEC_ERRORS = 5        # backend; N calls in a row ending in error = stop

    def work(row):
        res = grade_one(row, tpl, llm, a.data_root, run_cache,
                        temperature=a.temperature,
                        max_output_tokens=a.max_output_tokens)
        res["grader_prompt"] = prompt_tag
        with lock:
            if res.get("error"):
                consec_errors[0] += 1
                if consec_errors[0] >= MAX_CONSEC_ERRORS:
                    print(f"ABORT: {consec_errors[0]} consecutive grader errors — "
                          f"the CLI looks hard-down, stopping instead of hammering",
                          flush=True)
                    os._exit(3)
            else:
                consec_errors[0] = 0
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(res, ensure_ascii=False) + "\n")
        tag = res.get("error") or (f"{res['grader_action']} "
                                   f"[{res['axis_agreement']} vs fable "
                                   f"{res['fable_verdict']}]")
        print(f"  {res['claim_id']}: {tag}", flush=True)
        return res

    # Pre-load run data serially (load_run isn't thread-safe on first touch).
    for r in todo:
        load_run(a.data_root, r["run"], run_cache)
    results = parallel_map(work, todo, workers=a.workers)
    errs = [r for r in results if r.get("error")]
    ok = [r for r in results if not r.get("error")]
    agree = sum(1 for r in ok if r["axis_agreement"] == "agree")
    dis = [r["claim_id"] for r in ok if r["axis_agreement"] == "DISAGREE"]
    print(f"done: {len(ok)} graded ({agree} agree, {len(dis)} disagree: {dis}), "
          f"{len(errs)} errors {[e['claim_id'] for e in errs]}")


if __name__ == "__main__":
    main()
