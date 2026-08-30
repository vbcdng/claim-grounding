"""
End-to-end regression check: score an analysis.json against the paper1
hand-audit ground truth. NO API calls — it reads a finished run.

Rule (2026-07-04): no prompt / matcher / config change ships without this
passing on a fresh paper1 run (`verify_my_text.py --full` on the paper1 inputs,
~$0.12), scored here.

    venv/bin/python3 benchmarks/regression_check.py
    venv/bin/python3 benchmarks/regression_check.py --analysis data/paper1_verification/analysis.json

Exit codes: 0 = no regressions, 1 = at least one hard expectation failed.
'watch' claims (gray areas / open owner calls) are reported, never a failure.
Ground truth: benchmarks/paper1_ground_truth.json — provenance in
docs/PAPER1_TUNING_STATE.md ("Hand-audit ground truth").
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail.rerun import _norm

DEFAULT_GT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper1_ground_truth.json")
DEFAULT_ANALYSIS = "data/paper1_verification/analysis.json"


def find_claim(gt_entry, claims_by_id, claims):
    """Locate the ground-truth claim in the run: by id if the text still
    matches, else by text anywhere (ids shift when the article is edited).
    Returns (claim | None, drifted: bool)."""
    want = _norm(gt_entry["text"])
    c = claims_by_id.get(gt_entry["id"])
    if c is not None and _norm(c.get("text")) == want:
        return c, False
    for other in claims:
        if _norm(other.get("text")) == want:
            return other, True
    return None, True


def score(analysis, gt):
    """Pure scorer. Returns a report dict:
    {failures: [...], passes: n, watch: [...], drifted: [...], missing: [...]}"""
    claims = analysis.get("text_claims", [])
    by_id = {c["id"]: c for c in claims}
    rep = {"failures": [], "passes": 0, "watch": [], "drifted": [], "missing": []}

    for entry in gt["claims"]:
        c, drifted = find_claim(entry, by_id, claims)
        if c is None:
            rep["missing"].append(entry)
            continue
        if drifted:
            rep["drifted"].append((entry, c["id"]))
        verdict = c.get("verdict")

        if entry["expect"] == "watch":
            item = {"id": entry["id"], "verdict": verdict,
                    "was": entry.get("at_creation"), "note": entry["note"]}
            if verdict != entry.get("at_creation"):
                imp = entry.get("improves_if")
                item["change"] = ("IMPROVED" if imp and verdict == imp else
                                  "changed — review")
            rep["watch"].append(item)
        elif verdict == entry["expect"]:
            rep["passes"] += 1
        else:
            rep["failures"].append({"id": entry["id"], "expect": entry["expect"],
                                    "got": verdict, "note": entry["note"],
                                    "reason": c.get("reason", "")})
    return rep


def contamination(analysis) -> str:
    """A short refusal message when the run must not be scored, else "".

    Task #37: a request to the model that fails is recorded on the claim
    (judge_error / checks_failed) and tallied in metadata.llm_failures — a
    verdict or dropped warning produced by dead calls is an outage artifact,
    so a run containing ANY of them can never be quoted as a result. The
    reason-prefix scan covers result files from runs older than the markers."""
    meta = analysis.get("metadata") or {}
    lf = meta.get("llm_failures") or {}
    n_failed = lf.get("failed_calls") or 0
    affected = [c.get("id") for c in analysis.get("text_claims", [])
                if c.get("judge_error") or c.get("checks_failed")
                or str(c.get("reason", "")).startswith(
                    ("no LLM response", "LLM judgment unparseable"))]
    if not n_failed and not affected:
        return ""
    ids = ", ".join(str(i) for i in affected[:12]) + ("…" if len(affected) > 12 else "")
    return (f"REFUSED to score: {n_failed} model request(s) failed during this "
            f"run and {len(affected)} claim(s) could not be fully checked"
            + (f" ({ids})" if affected else "")
            + ". A verdict minted by a dead call is an outage artifact, not a "
              "result. Re-run the same command (it retries just the affected "
              "claims), then score the clean run.")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Score an analysis.json against the paper1 ground truth")
    ap.add_argument("--analysis", default=DEFAULT_ANALYSIS)
    ap.add_argument("--ground-truth", default=DEFAULT_GT)
    args = ap.parse_args(argv)

    with open(args.ground_truth, encoding="utf-8") as f:
        gt = json.load(f)
    with open(args.analysis, encoding="utf-8") as f:
        analysis = json.load(f)

    bad = contamination(analysis)
    if bad:
        print(bad)
        return 2

    run_model = (analysis.get("metadata") or {}).get("model")
    if run_model and gt.get("model") and run_model != gt["model"]:
        print(f"note: run model {run_model} != ground-truth model {gt['model']} "
              f"(verdict profile may differ; the expectations still apply)")

    rep = score(analysis, gt)
    hard = rep["passes"] + len(rep["failures"])

    for f_ in rep["failures"]:
        print(f"FAIL  {f_['id']}: expected {f_['expect']}, got {f_['got']}")
        print(f"      ground truth: {f_['note']}")
        if f_["reason"]:
            print(f"      run reason:   {f_['reason'][:200]}")
    for entry, new_id in rep["drifted"]:
        print(f"note: {entry['id']} matched by text as {new_id} (ids shifted)")
    for entry in rep["missing"]:
        print(f"WARN  {entry['id']}: claim text not found in this run — article edited? "
              f"Update the ground truth if the edit was intentional.")

    print(f"\nHard expectations: {rep['passes']}/{hard} pass"
          + (f", {len(rep['failures'])} FAIL" if rep["failures"] else ""))

    if rep["watch"]:
        changed = [w for w in rep["watch"] if "change" in w]
        print(f"Watch (gray/open owner calls, never a failure): "
              f"{len(rep['watch'])} tracked, {len(changed)} changed")
        for w in rep["watch"]:
            mark = f"  [{w['change']}]" if "change" in w else ""
            print(f"  {w['id']}: {w['verdict']}"
                  + (f" (was {w['was']})" if w["verdict"] != w.get("was") else "")
                  + mark)
            if "change" in w:
                print(f"      {w['note']}")

    if rep["failures"]:
        print("\nREGRESSION — do not ship this config.")
        return 1
    print("\nOK — no regressions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
