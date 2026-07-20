#!/usr/bin/env python3
"""Copy the WiCE benchmark run outputs into benchmarks/wice_runs/.

The WiCE evaluation (docs/NEWSYS_EVAL_2026-07-12.md) scored 7 fresh runs
against WiCE's human labels with `wice_bench.py score`. The runs and the
batch ground truths live in the gitignored data/ tree, so the scoring was
not checkable from the repository alone. This script copies each run's
analysis.json plus its ground truth under benchmarks/wice_runs/ so any
reviewer can execute the scorer themselves (see FOR_REVIEWERS.md).
Absolute machine paths in run metadata are replaced with placeholders.

Needs data/ present (maintainer machine only). No LLM calls, no network.
"""
import json
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

# batch name (as in docs/NEWSYS_EVAL_2026-07-12.md) -> (ground truth, run)
BATCHES = {
    "dev2_pilot": ("data/first_check/wice_ground_truth.json",
                   "data/newsys_wice_dev2/analysis.json"),
    "dev1": ("data/wice/batch_dev_1/wice_ground_truth.json",
             "data/newsys_wice_dev1/analysis.json"),
    "dev3": ("data/wice/batch_dev_3/wice_ground_truth.json",
             "data/newsys_wice_dev3/analysis.json"),
    "dev4": ("data/wice/batch_dev_4/wice_ground_truth.json",
             "data/newsys_wice_dev4/analysis.json"),
    "dev5": ("data/wice/batch_dev_5/wice_ground_truth.json",
             "data/newsys_wice_dev5/analysis.json"),
    "train1": ("data/wice/batch_train_1/wice_ground_truth.json",
               "data/newsys_wice_train1/analysis.json"),
    # the headline train2 row is the post-subject-guard re-run
    # (docs/SUBJECT_GUARD.md); the pre-guard run is kept separately because
    # it contains the one caught false-support (waleedmajid) — the
    # "happened only once" event the submission describes.
    "train2": ("data/wice/batch_train_2/wice_ground_truth.json",
               "data/scratch_train2_guard/analysis.json"),
    "train2_preguard": ("data/wice/batch_train_2/wice_ground_truth.json",
                        "data/newsys_wice_train2/analysis.json"),
}


def export_run(batch, gt_path, run_path):
    out_dir = os.path.join(HERE, "wice_runs", batch)
    os.makedirs(out_dir, exist_ok=True)
    analysis = json.load(open(os.path.join(ROOT, run_path)))
    meta = analysis.get("metadata", {})
    for field in ("text_file", "sources_dir", "output_dir"):
        if field in meta:
            meta[field] = "<local path, removed>"
    with open(os.path.join(out_dir, "analysis.json"), "w") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=1)
    gt = json.load(open(os.path.join(ROOT, gt_path)))
    from wice_bench import EXCLUDED_NON_ENGLISH
    gt["claims"] = {k: v for k, v in gt["claims"].items()
                    if k not in EXCLUDED_NON_ENGLISH}
    with open(os.path.join(out_dir, "wice_ground_truth.json"), "w") as f:
        json.dump(gt, f, ensure_ascii=False, indent=1)
    print(f"{batch} -> {out_dir} ({len(gt['claims'])} claims)")


def main():
    for batch, (gt, run) in BATCHES.items():
        export_run(batch, gt, run)


if __name__ == "__main__":
    main()
