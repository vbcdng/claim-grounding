#!/usr/bin/env python3
"""Arbiter replay harness — score arbiter CANDIDATE models on recorded runs.

Every finished run under data/ whose claims carry an `arbiter` payload is
replayable: the exact sentence lists the original arbiter saw are cached in
`<run>/source_claims/*.json`, titles live in `analysis["sources"]`, and the
prompt assembly in modules/papertrail/arbiter.py is a pure function of
(claim dict, sources dict) — so a candidate model can be asked the very same
questions the incumbent answered, and the two compared per claim.

Feasibility + sampling design: docs/OPENROUTER_RESCAN_2026-08-01.md
("Arbiter replay harness" section). Frozen run dirs are READ-ONLY here —
all output goes to a separate workspace dir; the script refuses to write
into anything that looks like a run dir.

Modes (subcommands):
  inventory  — scan data/ for arbiter-touched claims, classify strata, $0
  sample     — build the stratified sample (labeled + flips + N survivors), $0
  estimate   — assemble the real prompts for the sample, report token counts, $0
  replay     — LIVE: run a candidate model on the sample, diff vs recorded,
               log every raw response (the transcripts the runs never stored),
               write results.jsonl + report.md. With --rescue-judge MODEL the
               candidate's rulings also go through the REAL production
               machinery (task #24 extension, 2026-08-02): a "provable" ruling
               on an unsupported claim runs arbiter.rescue() with that primary
               judge (unanimity bar unchanged) → would-flip yes/no per claim;
               supported/NOT-PROVEN claims always get arbiter.resolve_ambers()
               ($0, pure) → would the badge clear. Frozen runs stay untouched —
               everything happens on the restored deep copies.
  report     — regenerate report.md from an existing results.jsonl, $0

Typical flow:
  python benchmarks/arbiter_replay.py sample  --out <workspace>
  python benchmarks/arbiter_replay.py estimate --out <workspace>
  python benchmarks/arbiter_replay.py replay  --out <workspace> --model deepseek/deepseek-v4-flash-0731

Scoring levels (see the rescan doc):
  1. action agreement vs the recorded arbiter — free, but "different" != "wrong"
  2. the verbatim quote gate — label-free: quotes_dropped counts proof quotes
     the model OFFERED that are not verbatim in the source (the hallucinated-
     grounding class that disqualified ling as a judge)
  3. human ground truth — only the labeled stratum (paper1 + pots gate files)

Replay fidelity caveats (recorded honestly per claim):
  * method=="arbiter_rescue" claims had their verdict/evidences rewritten by
    rescue() AFTER the arbiter ran; we restore verdict="unsupported" and strip
    the rescue-added evidences, but originals rescue dropped are gone →
    `restored_approx: true` on those rows.
  * cached source_claims titles can predate the manifest title override, so we
    take titles from analysis["sources"] (the final values the original
    prompt used).
"""
import argparse
import copy as copymod
import hashlib
import json
import os
import random
import sys
import threading
import time
from collections import Counter
from typing import Any, Dict, List, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from modules.papertrail import arbiter, llm_client  # noqa: E402
from modules.papertrail.llm_client import LLMClient, parallel_map  # noqa: E402

DEFAULT_DATA_DIR = os.path.join(REPO_ROOT, "data")
DEFAULT_BENCH_DIR = os.path.join(REPO_ROOT, "benchmarks")
DEFAULT_SURVIVORS = 40
DEFAULT_SEED = 46
EST_OUT_TOKENS_PER_CALL = 2700   # measured mean on 358 real arbiter calls (8/1)

# text_file basename hint -> hand-audited ground-truth file (id-keyed "claims")
GT_FILES = {
    "paper1": "paper1_ground_truth.json",
    "bentonite": "bentonite_ground_truth.json",
    "chimpanzee": "chimpanzee_ground_truth.json",
    "pots": "coverage_ground_truth_pots.json",
    "bohemia": "coverage_ground_truth_bohemia.json",
    "essay": "coverage_ground_truth_essay.json",
}


# ---------------------------------------------------------------- inventory

def _find_runs(data_dir: str) -> List[str]:
    """Every dir under data_dir holding an analysis.json (any depth)."""
    runs = []
    skip = {"sources", "source_claims", "embeddings", "inbox"}
    for root, dirs, files in os.walk(data_dir):
        dirs[:] = [d for d in dirs if d not in skip]
        if "analysis.json" in files:
            runs.append(root)
    return sorted(runs)


def _gt_lookup(text_file: str, run_name: str,
               bench_dir: str) -> (Optional[str], Dict[str, Dict]):
    """(gt filename, {claim id -> gt entry}) for this run's text, or (None, {}).

    The paper GT files carry their own relative `text_file` — match it as a
    path suffix of the run's text_file (basenames are all `my_text.md`, so a
    bare filename hint never fires). The coverage GT files carry none, so fall
    back to the text-name hint in the run's text_file path, its basename, or
    the run dir's name (e.g. arbval_pots → pots)."""
    tf = str(text_file or "").replace(os.sep, "/").lower()
    for hint, fname in GT_FILES.items():
        path = os.path.join(bench_dir, fname)
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            gt = json.load(f)
        gt_tf = (gt.get("text_file") or "") if isinstance(gt, dict) else ""
        if gt_tf:
            hit = tf.endswith(str(gt_tf).replace(os.sep, "/").lower())
        else:
            hit = hint in tf or hint in os.path.basename(tf) or hint in run_name.lower()
        if hit:
            entries = gt.get("claims") if isinstance(gt, dict) else gt
            return fname, {e["id"]: e for e in (entries or [])
                           if isinstance(e, dict) and e.get("id")}
    return None, {}


def build_inventory(data_dir: str = DEFAULT_DATA_DIR,
                    bench_dir: str = DEFAULT_BENCH_DIR) -> Dict[str, Any]:
    """Scan every run for arbiter-touched claims; classify strata; dedup
    identical rerun pairs (same text_file + same arbiter claim-id set —
    keep the latest timestamp, mark the rest duplicate_of)."""
    runs_out, claims_out = [], []
    by_signature: Dict[Any, List[Dict]] = {}
    for run_dir in _find_runs(data_dir):
        try:
            with open(os.path.join(run_dir, "analysis.json"), "r", encoding="utf-8") as f:
                analysis = json.load(f)
        except Exception as e:
            runs_out.append({"run": os.path.relpath(run_dir, data_dir),
                             "error": f"unreadable analysis.json: {e}"})
            continue
        meta = analysis.get("metadata") or {}
        arb_claims = [c for c in (analysis.get("text_claims") or []) if c.get("arbiter")]
        if not arb_claims:
            continue
        rel = os.path.relpath(run_dir, data_dir)
        sc_dir = os.path.join(run_dir, "source_claims")
        gt_file, gt = _gt_lookup(meta.get("text_file"), rel, bench_dir)
        rec = {"run": rel,
               "text_file": meta.get("text_file"),
               "timestamp": meta.get("timestamp"),
               "judge_model": meta.get("model"),
               "arbiter_model_seen": (arb_claims[0].get("arbiter") or {}).get("model"),
               "n_arbiter_claims": len(arb_claims),
               "has_source_claims": os.path.isdir(sc_dir),
               "gt_file": gt_file}
        sig = (str(meta.get("text_file")), tuple(sorted(c["id"] for c in arb_claims)))
        by_signature.setdefault(sig, []).append(rec)
        runs_out.append(rec)

        for c in arb_claims:
            ab = c["arbiter"]
            strata = []
            if c.get("method") == "arbiter_rescue" or c.get("proof_state") == "arbiter_resolved":
                strata.append("flip")
            if c["id"] in gt:
                strata.append("labeled")
            if "flip" not in strata:
                strata.append("amber_survivor")
            claims_out.append({
                "run": rel, "claim_id": c["id"], "verdict": c.get("verdict"),
                "method": c.get("method"), "proof_state": c.get("proof_state"),
                "trigger": ab.get("trigger"), "action": ab.get("action"),
                "n_proofs": len(ab.get("proofs") or []),
                "quotes_dropped": ab.get("quotes_dropped", 0),
                "strata": strata,
                "gt": ({k: gt[c["id"]].get(k) for k in ("expect", "kind", "note")}
                       if c["id"] in gt else None),
            })

    # dedup: within a signature group keep the latest timestamp
    dup_runs = set()
    for sig, group in by_signature.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda r: str(r.get("timestamp") or ""))
        keeper = group[-1]
        for r in group[:-1]:
            r["duplicate_of"] = keeper["run"]
            dup_runs.add(r["run"])
    claims_out = [c for c in claims_out if c["run"] not in dup_runs]

    totals = Counter()
    for c in claims_out:
        for s in c["strata"]:
            totals[s] += 1
    return {"data_dir": data_dir,
            "runs": runs_out,
            "claims": claims_out,
            "n_duplicate_runs_skipped": len(dup_runs),
            "totals": {"claims": len(claims_out), **dict(totals)}}


# ------------------------------------------------------------------ sample

def make_sample(inventory: Dict[str, Any], n_survivors: int = DEFAULT_SURVIVORS,
                seed: int = DEFAULT_SEED) -> Dict[str, Any]:
    """Stratified sample: ALL labeled + ALL flips + n seeded-random survivors.
    The dollars are trivial either way; the sample bounds the human
    adjudication reading, so it is chosen, not random."""
    labeled = [c for c in inventory["claims"] if "labeled" in c["strata"]]
    flips = [c for c in inventory["claims"] if "flip" in c["strata"]]
    core_keys = {(c["run"], c["claim_id"]) for c in labeled + flips}
    pool = [c for c in inventory["claims"]
            if "amber_survivor" in c["strata"]
            and (c["run"], c["claim_id"]) not in core_keys]
    pool.sort(key=lambda c: (c["run"], c["claim_id"]))   # order-independent of scan
    rng = random.Random(seed)
    survivors = rng.sample(pool, min(n_survivors, len(pool)))
    rows, seen = [], set()
    for c in labeled + flips + survivors:
        k = (c["run"], c["claim_id"])
        if k not in seen:
            seen.add(k)
            rows.append(c)
    return {"seed": seed, "n_survivors_requested": n_survivors,
            "counts": {"labeled": len(labeled), "flips": len(flips),
                       "survivors_sampled": len(survivors),
                       "survivor_pool": len(pool), "total": len(rows)},
            "rows": rows}


# ----------------------------------------------------------------- replay

def load_run_sources(run_dir: str) -> Dict[str, Dict[str, Any]]:
    """Rebuild the exact `sources` dict the original run passed to
    arbiter.run: sentences byte-identical from source_claims/*.json, titles
    from analysis["sources"] (the final manifest-overridden values)."""
    with open(os.path.join(run_dir, "analysis.json"), "r", encoding="utf-8") as f:
        analysis = json.load(f)
    titles = {s.get("paper_id"): s.get("title")
              for s in (analysis.get("sources") or []) if s.get("paper_id")}
    sources = {}
    sc_dir = os.path.join(run_dir, "source_claims")
    if os.path.isdir(sc_dir):
        for fname in sorted(os.listdir(sc_dir)):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(sc_dir, fname), "r", encoding="utf-8") as f:
                    cached = json.load(f)
            except Exception:
                continue
            pid = cached.get("paper_id")
            if not pid:
                continue
            sources[pid] = {"sentences": cached.get("sentences") or [],
                            "title": titles.get(pid) or cached.get("title") or pid}
    return sources


def restore_pre_arbiter(claim: Dict[str, Any]) -> (Dict[str, Any], Dict[str, Any], bool):
    """A deep copy of the claim as the arbiter originally saw it, the recorded
    arbiter payload, and whether the restoration is approximate."""
    c = copymod.deepcopy(claim)
    old = c.pop("arbiter")
    approx = False
    if c.get("method") == "arbiter_rescue":
        # rescue() flipped the verdict and REPLACED evidences; originals for
        # the covered sources are unrecoverable — restore what we can.
        c["verdict"] = "unsupported"
        c["evidences"] = [e for e in (c.get("evidences") or [])
                          if (e or {}).get("via") != "arbiter_rescue"]
        approx = True
    if c.get("proof_state") == "arbiter_resolved":
        # resolve_ambers() is display-only; nothing the prompt reads changed.
        c["proof_state"] = "partial"
        (c.get("covering") or {}).pop("arbiter_resolution", None)
    c.pop("owner_flag", None)   # an author ruling added later must not block replay
    return c, old, approx


class _ResponseLog:
    """Locked JSONL sink for raw responses — the transcripts runs never stored."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def write(self, rec: Dict[str, Any]) -> None:
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")


class _LoggingLLM:
    """Per-claim wrapper: arbiter.run/rescue only touch .model and .call().
    `stage` marks whose calls these are in raw_responses.jsonl ("arbiter" =
    the candidate, "rescue_judge" = the primary judge's re-check votes)."""

    def __init__(self, inner, sink: _ResponseLog, run: str, claim_id: str,
                 log_prompts: bool = False, stage: str = "arbiter",
                 force_temperature: Optional[float] = None):
        self._inner, self._sink = inner, sink
        self._run, self._claim_id, self._log_prompts = run, claim_id, log_prompts
        self._stage = stage
        # Some hosts accept exactly one temperature and reject the arbiter's
        # 0.1 outright (the Kimi platform's kimi-k2.6: "only 1 is allowed for
        # this model"). Overriding here keeps the candidate's requirement out
        # of the production arbiter code, and never touches the rescue judge.
        self._force_temperature = force_temperature
        self.calls = 0

    @property
    def model(self):
        return self._inner.model

    def call(self, prompt, **kw):
        if self._force_temperature is not None:
            kw["temperature"] = self._force_temperature
        self.calls += 1
        t0 = time.time()
        raw = self._inner.call(prompt, **kw)
        rec = {"run": self._run, "claim_id": self._claim_id, "model": self.model,
               "stage": self._stage,
               "elapsed_s": round(time.time() - t0, 1),
               "prompt_chars": len(prompt),
               "prompt_sha1": hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:12],
               "response_chars": len(raw or ""), "response": raw}
        if self._log_prompts:
            rec["prompt"] = prompt
        self._sink.write(rec)
        return raw


def _rejudge(restored: Dict[str, Any], sources: Dict[str, Any], run_rel: str,
             judge_llm, sink: _ResponseLog,
             log_prompts: bool = False) -> (Optional[Dict], Optional[Dict]):
    """Run the candidate's fresh ruling through the REAL production machinery
    (task #24: compare what each arbiter would DO, not just what it says).

    Rescue: an unsupported claim the candidate ruled provable (action
    wrong_or_insufficient_evidence + gate-verified proofs) goes through
    arbiter.rescue() with the primary judge — the same window location,
    subject guard, combined prompt and unanimity bar as production.
    Amber: a supported/NOT-PROVEN claim goes through arbiter.resolve_ambers()
    — $0, pure post-processing, needs no judge.

    Mutates only `restored` (a throwaway deep copy). Returns
    (rescue_record, amber_record), either may be None."""
    cid = restored.get("id")
    ab = restored.get("arbiter") or {}
    rescue_rec = amber_rec = None

    if restored.get("verdict") == "unsupported":
        proposed = (ab.get("action") == "wrong_or_insufficient_evidence"
                    and bool(ab.get("proofs")))
        rescue_rec = {"proposed": proposed}
        if proposed and judge_llm is not None:
            jw = _LoggingLLM(judge_llm, sink, run_rel, cid,
                             log_prompts=log_prompts, stage="rescue_judge")
            res = arbiter.rescue([restored], sources, jw, workers=1)
            rescue_rec.update({
                "would_flip": cid in res["flipped"],
                "judge_calls": jw.calls,
                # rescue() bails before any judge call when no proof quote
                # pins to a locatable window in a cited source
                "no_window": jw.calls == 0 and cid in res["held"],
            })
            if cid in res["flipped"]:
                rescue_rec["flip_reason"] = (restored.get("reason") or "")[:300]
        elif proposed:
            rescue_rec["would_flip"] = None   # no judge configured — unknown

    elif restored.get("verdict") == "supported" \
            and restored.get("proof_state") == "partial" and ab.get("action"):
        res = arbiter.resolve_ambers([restored])
        amber_rec = {"eligible": True, "would_resolve": cid in res["resolved"]}

    return rescue_rec, amber_rec


def _compact(ab: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not ab:
        return None
    return {"model": ab.get("model"), "action": ab.get("action"),
            "trigger": ab.get("trigger"),
            "n_proofs": len(ab.get("proofs") or []),
            "quotes_dropped": ab.get("quotes_dropped", 0),
            "has_conflict": bool(ab.get("conflict")),
            "missing_subclaim": (ab.get("missing_subclaim") or "")[:300]}


def replay(rows: List[Dict[str, Any]], out_dir: str, data_dir: str = DEFAULT_DATA_DIR,
           model: Optional[str] = None, api_key: Optional[str] = None,
           workers: int = 4, log_prompts: bool = False, llm=None,
           estimate_only: bool = False,
           rescue_judge_model: Optional[str] = None,
           rescue_judge_api_key: Optional[str] = None,
           judge_llm=None, api_base: Optional[str] = None,
           temperature: Optional[float] = None) -> Dict[str, Any]:
    """Replay `rows` (inventory/sample rows) against a candidate arbiter.
    Pass `llm` to inject a fake client (tests); otherwise `model` is required.
    `api_base` targets an OpenAI-compatible endpoint the installed litellm has
    no native provider for (e.g. the Kimi/Moonshot platform: model
    `openai/kimi-k2.6`, api_base `https://api.moonshot.ai/v1`).
    estimate_only assembles the real prompts and returns token counts, no calls
    (the estimate does NOT include rescue-judge calls — rescues are rare,
    ~a handful per arm). `rescue_judge_model` / `judge_llm` (injectable for
    tests) turn on the real rescue re-judge — see _rejudge()."""
    tpl = arbiter._load_prompt()
    by_run: Dict[str, List[Dict]] = {}
    for r in rows:
        by_run.setdefault(r["run"], []).append(r)

    # Assemble every job first (also powers the estimate).
    jobs, est_prompt_chars, problems = [], 0, []
    for run_rel, run_rows in sorted(by_run.items()):
        run_dir = os.path.join(data_dir, run_rel)
        with open(os.path.join(run_dir, "analysis.json"), "r", encoding="utf-8") as f:
            analysis = json.load(f)
        claims_by_id = {c.get("id"): c for c in analysis.get("text_claims") or []}
        sources = load_run_sources(run_dir)
        for r in run_rows:
            orig = claims_by_id.get(r["claim_id"])
            if not orig or not orig.get("arbiter"):
                problems.append(f"{run_rel}/{r['claim_id']}: claim or arbiter payload missing")
                continue
            restored, old, approx = restore_pre_arbiter(orig)
            computed_trigger = arbiter.trigger(restored)
            if computed_trigger is None:
                problems.append(f"{run_rel}/{r['claim_id']}: trigger lost after restoration — skipped")
                continue
            prompt = (tpl.replace("{TRIGGER}", computed_trigger)
                      .replace("{CLAIM}", restored.get("text", ""))
                      .replace("{SHOWN}", arbiter._shown_block(restored))
                      .replace("{CONTEXT}", arbiter._source_blocks(restored, sources)))
            est_prompt_chars += len(prompt)
            jobs.append({"run": run_rel, "row": r, "restored": restored, "old": old,
                         "approx": approx, "computed_trigger": computed_trigger,
                         "sources": sources})

    est = {"claims": len(jobs), "est_input_tokens": est_prompt_chars // 4,
           "est_output_tokens": len(jobs) * EST_OUT_TOKENS_PER_CALL,
           "problems": problems}
    if estimate_only:
        return est

    os.makedirs(out_dir, exist_ok=True)
    if llm is None:
        if not model:
            raise SystemExit("replay needs --model")
        llm = LLMClient(model=model, api_key=api_key or arbiter.resolve_key(model),
                        api_base=api_base)
    if judge_llm is None and rescue_judge_model:
        judge_llm = LLMClient(model=rescue_judge_model,
                              api_key=rescue_judge_api_key
                              or arbiter.resolve_key(rescue_judge_model))
    sink = _ResponseLog(os.path.join(out_dir, "raw_responses.jsonl"))
    usage_before = {m: dict(v) for m, v in llm_client.usage_summary().items()}
    t_start = time.time()
    results: List[Dict[str, Any]] = []
    res_lock = threading.Lock()

    def one(job):
        restored = job["restored"]
        wrapped = _LoggingLLM(llm, sink, job["run"], restored.get("id"),
                              log_prompts=log_prompts,
                              force_temperature=temperature)
        arbiter.run([restored], job["sources"], wrapped, workers=1)
        new = restored.get("arbiter")
        old = job["old"]
        new_compact = _compact(new)   # before _rejudge adds "rescued"
        rescue_rec = amber_rec = None
        if new:
            rescue_rec, amber_rec = _rejudge(restored, job["sources"], job["run"],
                                             judge_llm, sink,
                                             log_prompts=log_prompts)
        rec = {"run": job["run"], "claim_id": restored.get("id"),
               "strata": job["row"]["strata"], "gt": job["row"].get("gt"),
               "restored_approx": job["approx"],
               "trigger_recorded": old.get("trigger"),
               "trigger_replay": job["computed_trigger"],
               "old": _compact(old), "new": new_compact,
               "action_match": bool(new) and new.get("action") == old.get("action"),
               "no_response": new is None,
               "rescue": rescue_rec, "amber": amber_rec,
               "new_payload": new}
        with res_lock:
            results.append(rec)

    parallel_map(one, jobs, workers=workers)

    usage_after = llm_client.usage_summary()
    usage_delta = {}
    for m, v in usage_after.items():
        b = usage_before.get(m, {})
        d = {k: round(v.get(k, 0) - b.get(k, 0), 6)
             for k in ("calls", "prompt_tokens", "completion_tokens", "cost_usd")}
        if d.get("calls"):
            usage_delta[m] = d

    results.sort(key=lambda r: (r["run"], str(r["claim_id"])))
    with open(os.path.join(out_dir, "results.jsonl"), "w", encoding="utf-8") as f:
        for rec in results:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    resc = [r["rescue"] for r in results if r.get("rescue")]
    ambers = [r["amber"] for r in results if r.get("amber")]
    summary = {"model": getattr(llm, "model", model), "claims": len(results),
               "runs": len(by_run), "no_response": sum(r["no_response"] for r in results),
               "problems": problems, "estimate": est, "usage_delta": usage_delta,
               "rescue_judge_model": getattr(judge_llm, "model", None),
               "rescue": {"unsupported_answered": len(resc),
                          "proposed": sum(1 for r in resc if r["proposed"]),
                          "would_flip": sum(1 for r in resc if r.get("would_flip")),
                          "no_window": sum(1 for r in resc if r.get("no_window"))},
               "amber": {"eligible": len(ambers),
                         "would_resolve": sum(1 for a in ambers if a["would_resolve"])},
               "wall_seconds": round(time.time() - t_start, 1)}
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    write_report(out_dir)
    return summary


# ----------------------------------------------------------------- report

def _pct(a: int, b: int) -> str:
    return f"{a}/{b} ({100.0 * a / b:.0f}%)" if b else "0/0"


def write_report(out_dir: str) -> str:
    with open(os.path.join(out_dir, "results.jsonl"), "r", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    summary = {}
    spath = os.path.join(out_dir, "summary.json")
    if os.path.exists(spath):
        with open(spath, "r", encoding="utf-8") as f:
            summary = json.load(f)

    answered = [r for r in rows if not r["no_response"]]
    n_unparsed = len(rows) - len(answered)
    lines = [f"# Arbiter replay — {summary.get('model', '?')} vs recorded", ""]
    lines.append(f"Claims replayed: {len(rows)} across {summary.get('runs', '?')} runs; "
                 f"wall time {summary.get('wall_seconds', '?')}s.")
    if n_unparsed:
        lines.append(f"\n**⚠ {n_unparsed} claims returned unparseable output — "
                     f"per-claim comparisons below are INCOMPLETE; do not quote "
                     f"the numbers without saying so.**")
    if summary.get("problems"):
        lines.append("\nSkipped/problem rows:")
        lines += [f"- {p}" for p in summary["problems"]]

    lines.append("\n## Action agreement vs the recorded arbiter")
    lines.append("(Agreement is context, not correctness — disagreements are the "
                 "adjudication queue, see the labeled table below.)\n")
    lines.append("| stratum | agree | of answered |")
    lines.append("|---|---|---|")
    strata_names = ["labeled", "flip", "amber_survivor"]
    for s in ["ALL"] + strata_names:
        grp = answered if s == "ALL" else [r for r in answered if s in r["strata"]]
        agree = sum(r["action_match"] for r in grp)
        lines.append(f"| {s} | {_pct(agree, len(grp))} | {len(grp)} |")
    conf = Counter((r["old"]["action"], r["new"]["action"]) for r in answered)
    lines.append("\nConfusion (recorded → candidate):")
    for (a, b), n in sorted(conf.items(), key=lambda kv: -kv[1]):
        mark = "" if a == b else "  ←"
        lines.append(f"- {a} → {b}: {n}{mark}")

    lines.append("\n## Verbatim quote gate (label-free)")
    lines.append("`quotes_dropped` = proof quotes the model OFFERED that are not "
                 "verbatim in the source — the hallucinated-grounding class.\n")
    lines.append("| side | proofs kept | quotes dropped | drop rate |")
    lines.append("|---|---|---|---|")
    for side in ("old", "new"):
        kept = sum(r[side]["n_proofs"] for r in answered)
        drop = sum(r[side]["quotes_dropped"] for r in answered)
        rate = f"{100.0 * drop / (kept + drop):.1f}%" if kept + drop else "—"
        lines.append(f"| {side} ({'recorded' if side == 'old' else 'candidate'}) "
                     f"| {kept} | {drop} | {rate} |")
    new_finds = [r for r in answered if "amber_survivor" in r["strata"]
                 and r["new"]["n_proofs"] and not r["old"]["n_proofs"]]
    lost = [r for r in answered if r["old"]["n_proofs"] and not r["new"]["n_proofs"]]
    if new_finds:
        lines.append("\nSurvivor ambers where the candidate produced gate-verified "
                     "proofs the recorded arbiter did not (real quotes — adjudicate "
                     "relevance): " + ", ".join(f"{r['run']}/{r['claim_id']}" for r in new_finds))
    if lost:
        lines.append("\nClaims where the recorded arbiter had proofs and the candidate "
                     "produced none: " + ", ".join(f"{r['run']}/{r['claim_id']}" for r in lost))

    flips = [r for r in rows if "flip" in r["strata"]]
    if flips:
        lines.append("\n## Flip claims (the recorded arbiter changed what the reader sees)")
        lines.append("| run/claim | recorded action (proofs) | candidate action (proofs) | approx? |")
        lines.append("|---|---|---|---|")
        for r in flips:
            new = r["new"] or {}
            lines.append(f"| {r['run']}/{r['claim_id']} "
                         f"| {r['old']['action']} ({r['old']['n_proofs']}) "
                         f"| {new.get('action', 'NO RESPONSE')} ({new.get('n_proofs', '—')}) "
                         f"| {'yes' if r['restored_approx'] else ''} |")

    resc = [r for r in rows if r.get("rescue")]
    if resc:
        judged = [r for r in resc if r["rescue"].get("would_flip") is not None]
        proposed = [r for r in resc if r["rescue"]["proposed"]]
        r_flips = [r for r in judged if r["rescue"].get("would_flip")]
        no_win = [r for r in judged if r["rescue"].get("no_window")]
        vetoed = [r for r in judged if r["rescue"]["proposed"]
                  and not r["rescue"].get("would_flip")
                  and not r["rescue"].get("no_window")]
        lines.append("\n## Rescue re-judge (real production machinery)")
        lines.append(f"Judge = {summary.get('rescue_judge_model') or 'NOT CONFIGURED'}. "
                     "A candidate ruling 'provable' on an unsupported claim runs the "
                     "actual arbiter.rescue(): window location + subject guard + the "
                     "primary judge's unanimity bar. This is what the candidate would "
                     "DO to the reader's page, not what it says.\n")
        lines.append("| metric | n |")
        lines.append("|---|---|")
        lines.append(f"| unsupported claims answered | {len(resc)} |")
        lines.append(f"| candidate proposed a rescue (provable + verified proofs) | {len(proposed)} |")
        lines.append(f"| would FLIP (survived the unanimous judge re-check) | {len(r_flips)} |")
        lines.append(f"| held — no locatable proof window | {len(no_win)} |")
        lines.append(f"| held — judge vetoed | {len(vetoed)} |")
        if r_flips:
            lines.append("\nWould-flip claims (the class #23's panel would gate):")
            for r in r_flips:
                lines.append(f"- {r['run']}/{r['claim_id']}: "
                             f"{(r['rescue'].get('flip_reason') or '')[:200]}")
        unjudged = [r for r in proposed if r["rescue"].get("would_flip") is None]
        if unjudged:
            lines.append(f"\n({len(unjudged)} proposal(s) NOT re-judged — no "
                         "--rescue-judge was configured.)")

    ambers = [r for r in rows if r.get("amber")]
    if ambers:
        cleared = [r for r in ambers if r["amber"]["would_resolve"]]
        lines.append("\n## Amber resolution (production display contract, $0)")
        lines.append("Would the candidate's ruling clear a NOT-PROVEN-AS-WRITTEN "
                     "badge, per arbiter.resolve_ambers() — provable action + "
                     "gate-verified proofs.\n")
        lines.append(f"- eligible (supported, proof_state=partial): {len(ambers)}")
        lines.append(f"- would clear the badge: {len(cleared)}"
                     + (" — " + ", ".join(f"{r['run']}/{r['claim_id']}" for r in cleared)
                        if cleared else ""))

    labeled = [r for r in rows if "labeled" in r["strata"]]
    if labeled:
        lines.append("\n## Labeled claims (human ground truth)")
        lines.append("| run/claim | gt | recorded action | candidate action |")
        lines.append("|---|---|---|---|")
        for r in labeled:
            gt = r.get("gt") or {}
            gt_s = gt.get("expect") or gt.get("kind") or "?"
            new = r["new"] or {}
            flag = "" if r["action_match"] else " **≠**"
            lines.append(f"| {r['run']}/{r['claim_id']} | {gt_s} "
                         f"| {r['old']['action']} | {new.get('action', 'NO RESPONSE')}{flag} |")

    if summary.get("usage_delta"):
        lines.append("\n## Usage (this replay only)")
        for m, u in summary["usage_delta"].items():
            lines.append(f"- {m}: {u['calls']:.0f} calls, {u['prompt_tokens']:.0f} in / "
                         f"{u['completion_tokens']:.0f} out tokens, ${u['cost_usd']:.4f}")
    lines.append("\nRaw responses: raw_responses.jsonl (response length there = the "
                 "truncation / thinking-burn check against the 3000-token cap).")

    path = os.path.join(out_dir, "report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


# -------------------------------------------------------------------- CLI

def _guard_out_dir(out_dir: str) -> None:
    if os.path.exists(os.path.join(out_dir, "analysis.json")):
        raise SystemExit(f"{out_dir} looks like a run dir (has analysis.json) — "
                         f"refusing to write replay output there.")


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    for name in ("inventory", "sample", "estimate", "replay", "report"):
        p = sub.add_parser(name)
        p.add_argument("--out", required=True, help="workspace dir (never a run dir)")
        p.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
        if name == "sample":
            p.add_argument("--survivors", type=int, default=DEFAULT_SURVIVORS)
            p.add_argument("--seed", type=int, default=DEFAULT_SEED)
        if name in ("estimate", "replay"):
            p.add_argument("--all", action="store_true",
                           help="use the full inventory instead of sample.json")
        if name == "replay":
            p.add_argument("--model", required=True)
            p.add_argument("--api-key")
            p.add_argument("--api-base",
                           help="OpenAI-compatible endpoint for a provider the "
                                "installed litellm has no native route for, e.g. "
                                "https://api.moonshot.ai/v1 with --model openai/kimi-k2.6")
            p.add_argument("--temperature", type=float,
                           help="force the CANDIDATE's temperature (the rescue "
                                "judge keeps its own). Needed by hosts that "
                                "accept exactly one value — kimi-k2.6 rejects "
                                "the arbiter's 0.1 and requires 1")
            p.add_argument("--workers", type=int, default=4)
            p.add_argument("--log-prompts", action="store_true")
            p.add_argument("--rescue-judge", metavar="MODEL",
                           help="PRIMARY-judge model for the real rescue "
                                "re-judge on candidate 'provable' rulings "
                                "(e.g. gemini/gemma-4-31b-it); without it, "
                                "rescue proposals are recorded but not re-judged")
            p.add_argument("--rescue-judge-api-key")
            p.add_argument("--yes", action="store_true",
                           help="skip the confirmation on >200 claims")
    args = ap.parse_args(argv)
    _guard_out_dir(args.out)
    os.makedirs(args.out, exist_ok=True)
    inv_path = os.path.join(args.out, "inventory.json")
    sample_path = os.path.join(args.out, "sample.json")

    if args.mode == "inventory":
        inv = build_inventory(args.data_dir)
        with open(inv_path, "w", encoding="utf-8") as f:
            json.dump(inv, f, indent=1, ensure_ascii=False)
        print(json.dumps(inv["totals"], indent=2))
        print(f"({inv['n_duplicate_runs_skipped']} duplicate rerun(s) excluded) → {inv_path}")
        return

    if args.mode == "sample":
        inv = _load_json(inv_path) if os.path.exists(inv_path) else build_inventory(args.data_dir)
        if not os.path.exists(inv_path):
            with open(inv_path, "w", encoding="utf-8") as f:
                json.dump(inv, f, indent=1, ensure_ascii=False)
        smp = make_sample(inv, n_survivors=args.survivors, seed=args.seed)
        with open(sample_path, "w", encoding="utf-8") as f:
            json.dump(smp, f, indent=1, ensure_ascii=False)
        print(json.dumps(smp["counts"], indent=2))
        print(f"→ {sample_path}")
        return

    if args.mode == "report":
        print(write_report(args.out))
        return

    # estimate / replay share row selection
    if getattr(args, "all", False):
        inv = _load_json(inv_path) if os.path.exists(inv_path) else build_inventory(args.data_dir)
        rows = inv["claims"]
    else:
        if not os.path.exists(sample_path):
            raise SystemExit(f"no {sample_path} — run the `sample` mode first (or pass --all)")
        rows = _load_json(sample_path)["rows"]

    if args.mode == "estimate":
        est = replay(rows, args.out, data_dir=args.data_dir, estimate_only=True)
        print(json.dumps(est, indent=2))
        print("(input tokens = real assembled prompts / 4 chars-per-token; "
              f"output assumes the measured {EST_OUT_TOKENS_PER_CALL}/call mean)")
        return

    if len(rows) > 200 and not args.yes:
        raise SystemExit(f"{len(rows)} claims — pass --yes to confirm a replay this size.")
    summary = replay(rows, args.out, data_dir=args.data_dir, model=args.model,
                     api_key=args.api_key, workers=args.workers,
                     log_prompts=args.log_prompts, api_base=args.api_base,
                     temperature=args.temperature,
                     rescue_judge_model=args.rescue_judge,
                     rescue_judge_api_key=args.rescue_judge_api_key)
    print(json.dumps({k: summary[k] for k in ("model", "claims", "no_response",
                                              "rescue", "amber",
                                              "wall_seconds", "usage_delta")}, indent=2))
    print(f"→ {os.path.join(args.out, 'report.md')}")


if __name__ == "__main__":
    main()
