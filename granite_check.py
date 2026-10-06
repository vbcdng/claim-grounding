#!/usr/bin/env python3
"""Free local second opinion on the claims the tool APPROVED, after the fact.

Run it on a finished run's folder:

    ./venv/bin/python3 granite_check.py data/my_run

It hands each approved claim, one at a time, to Granite Guardian — a freely
usable checker model whose weight file sits on this computer — together with a
six-thousand-character excerpt of the cited source, and asks whether the excerpt
supports the claim. Where Granite is not convinced, the claim's card gets a
caution mark that reads as a question. No verdict is ever changed, no money is
ever spent, and analysis.json is not touched: the answers go to
<run>/granite_check.json and the viewer is regenerated to show them.

The cost is time. One excerpt takes roughly two and a half to four minutes on
this computer's processor, so twenty approved claims take about an hour. Use
--limit while trying it out.

Why only approved claims, and how much its disagreement is worth, is explained
at the top of modules/papertrail/granite_check.py.
"""

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.papertrail import granite_check, granite_model, viewer  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("granite_check")


def regenerate_viewers(run_dir: str, analysis: dict, payload: dict) -> list:
    """Redraw both viewers with the answers attached, in memory only."""
    usable, report = granite_check.validate(payload, analysis)
    logger.info(granite_check.report_sentence(report))
    granite_check.attach(analysis, usable)
    source_texts = {}
    for s in analysis.get("sources", []) or []:
        fn = s.get("filename")
        if fn and not fn.lower().endswith(".pdf"):
            p = os.path.join(run_dir, "sources", fn)
            if os.path.exists(p):
                with open(p, encoding="utf-8", errors="ignore") as f:
                    source_texts[s["paper_id"]] = f.read()
    text_file = (analysis.get("metadata") or {}).get("text_file", "")
    title = f"Verification — {os.path.basename(text_file) or 'run'}"
    written = []
    out = os.path.join(run_dir, "viewer.html")
    viewer.generate(analysis, out, title=title, source_texts=source_texts)
    written.append(out)
    try:
        from modules.papertrail import viewer_v2
        out2 = os.path.join(run_dir, "viewer_v2.html")
        viewer_v2.generate(analysis, out2, title=title, source_texts=source_texts)
        written.append(out2)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"The second viewer could not be redrawn: {e}")
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", help="a finished run's output folder (must hold analysis.json)")
    ap.add_argument("--limit", type=int, default=0,
                    help="only the first N approved claims (useful while trying it out)")
    ap.add_argument("--threshold", type=float, default=granite_model.THRESHOLD,
                    help=f"the score below which Granite counts as unconvinced "
                         f"(default {granite_model.THRESHOLD}, the value the "
                         f"benchmark's numbers were measured at)")
    ap.add_argument("--slice-chars", type=int, default=granite_model.SLICE_CHARS,
                    help=f"how much source text to hand it per claim (default "
                         f"{granite_model.SLICE_CHARS}; the measured catch and "
                         f"false-alarm rates only hold at this size)")
    ap.add_argument("--threads", type=int, default=0, help="processor threads (default: all)")
    ap.add_argument("--gguf", default=None, help="path to the weight file, if it is not "
                                                 "in the usual place")
    ap.add_argument("--worker-python", default=None,
                    help="an interpreter that has llama-cpp-python installed "
                         "(default: the checker benchmark's own virtual environment)")
    ap.add_argument("--no-viewer", action="store_true",
                    help="write granite_check.json but do not redraw the viewers")
    a = ap.parse_args()

    ap_path = os.path.join(a.run_dir, "analysis.json")
    if not os.path.exists(ap_path):
        logger.error(f"No analysis.json in {a.run_dir} — that is not a finished run.")
        return 2
    with open(ap_path, encoding="utf-8") as f:
        analysis = json.load(f)

    try:
        results = granite_check.check_run(
            a.run_dir, analysis, limit=a.limit, threshold=a.threshold,
            gguf_path=a.gguf, worker_python=a.worker_python,
            n_threads=(a.threads or None), slice_chars=a.slice_chars)
    except Exception as e:  # noqa: BLE001
        logger.error(f"The local checker could not run: {e}")
        return 3
    if not results:
        return 0
    payload = granite_check.wrap(analysis, results)
    path = granite_check.write_payload(a.run_dir, payload)
    s = granite_check.summary(results)
    logger.info(f"Wrote {path}: {s['flagged']} of {s['checked']} approved claim(s) "
                f"got a caution mark, {s['errors']} could not be checked, "
                f"{s['minutes']} minutes of processor time and no money spent.")
    if not a.no_viewer:
        for p in regenerate_viewers(a.run_dir, analysis, payload):
            logger.info(f"viewer refreshed: {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
