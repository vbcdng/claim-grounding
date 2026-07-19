#!/usr/bin/env python3
"""Stream D CLI — find replacement papers for claims marked 'wrong source'.

Reads a run's ``review.json`` (exported from the viewer), searches for a better
paper per marked claim via Stream B's snowball search, downloads the top
open-access candidates, and registers them in the project's manifest + refs so
the next ``verify_my_text.py`` run can use them. Writes ``replacements.json`` +
``replacement_report.md`` into the run dir.

PROPOSE-ONLY: it never edits your text or picks the citation — it readies the
sources and tells you which ``[[key]]`` to consider. You (or ``/apply-review``)
cite it after confirming the passage establishes the claim.

    python3 find_replacement_sources.py <run_dir>            # find + download
    python3 find_replacement_sources.py <run_dir> --dry-run  # search only, no writes
    python3 find_replacement_sources.py <run_dir> --model claude-code/haiku  # $0 LLM gate

Cost: search ranking is local (cosine) and $0 by default; --model adds one small
LLM relevance-gate call per claim. Downloading is open-access only.
"""

import os
import sys
import json
import glob
import logging
import argparse

from modules.papertrail import review_paper_finder as rpf

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _resolve_review(path: str) -> str:
    """Accept a review file path or a dir; return the newest review*.json found
    (the viewer exports distinguishable names like review_<run>_<date>.json
    since 2026-07-07; the bare review.json is still accepted)."""
    if os.path.isfile(path):
        return path
    cands = glob.glob(os.path.join(path, "review*.json"))
    if not cands:
        cands = glob.glob(os.path.join(path, "**", "review*.json"), recursive=True)
    if not cands:
        logger.error(f"No review*.json found in {path}. Export one from the viewer first.")
        sys.exit(1)
    return max(cands, key=os.path.getmtime)


def _resolve_project_dir(review: dict, override: str) -> str:
    if override:
        return override
    run = review.get("run", {}) or {}
    pdir = run.get("project_dir")
    if not pdir and run.get("sources_dir"):
        # sources_dir is a CHILD of the project dir ("<project>/sources") —
        # using it directly would nest a second sources/ + manifest where the
        # next verify run never looks.
        pdir = os.path.dirname(os.path.normpath(run["sources_dir"]))
    if not pdir and run.get("text_file"):
        pdir = os.path.dirname(run["text_file"])
    if not pdir or not os.path.isdir(pdir):
        logger.error("Could not resolve the project dir from review.json's run block; "
                     "pass --project-dir explicitly.")
        sys.exit(1)
    return pdir


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", help="Run dir holding review.json (or the review.json path).")
    ap.add_argument("--project-dir", default="",
                    help="Override the project dir (where sources/, the manifest, and "
                         "the *.refs.txt live). Defaults to review.json's run.project_dir.")
    ap.add_argument("--top-k", type=int, default=3,
                    help="Candidate papers to consider per claim (default 3).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Search only — do not download or touch the manifest/refs.")
    ap.add_argument("--model", default="",
                    help="Optional LLM for the relevance gate (e.g. claude-code/haiku "
                         "for $0). Default: cosine-only, no API.")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--api-base", default=None)
    args = ap.parse_args(argv)

    review_path = _resolve_review(args.run_dir)
    with open(review_path, encoding="utf-8") as f:
        review = json.load(f)
    logger.info(f"Loaded {review_path}")
    project_dir = _resolve_project_dir(review, args.project_dir)

    marks = rpf._wrong_source_marks(review)
    if not marks:
        logger.info("No claims marked 'wrong_source' — nothing to do.")
        return 0
    logger.info(f"{len(marks)} claim(s) marked wrong_source; project dir: {project_dir}")

    llm = None
    if args.model:
        from modules.papertrail.llm_client import LLMClient
        llm = LLMClient(model=args.model, api_key=args.api_key, api_base=args.api_base)

    session = None
    if not args.dry_run:
        from modules.papertrail import direct_downloader as dd
        session = dd.setup_session()

    report = rpf.find_replacements(
        review, project_dir, top_k=args.top_k, download=not args.dry_run,
        llm=llm, session=session, cache_dir=project_dir)

    run_dir = os.path.dirname(review_path)
    with open(os.path.join(run_dir, "replacements.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    md = rpf.render_report(report)
    with open(os.path.join(run_dir, "replacement_report.md"), "w", encoding="utf-8") as f:
        f.write(md)

    n_ready = sum(1 for p in report["proposals"] if p.get("suggested_key"))
    logger.info(f"Wrote replacements.json + replacement_report.md to {run_dir}")
    logger.info(f"{n_ready}/{len(report['proposals'])} claim(s) have a downloaded candidate "
                "ready to cite. Review replacement_report.md — nothing was cited for you.")
    print("\n" + md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
