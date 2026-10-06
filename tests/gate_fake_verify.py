#!/usr/bin/env python3
"""Stand-in for verify_my_text.py in tests/test_gate_resume.py (card #134).

Makes no model call. Writes an analysis.json shaped like the real one (the
metadata fields the gate's skip check reads) and appends one line per call to
<output-dir>/fake_calls.log ("full" or "retry"), so a test can count how often
the gate really judged a text. FAKE_FAIL_NAME=<substring of output dir> makes
the call exit 3 before writing (a crash); FAKE_REFUSED=1 writes a refused-call
marker into the analysis.
"""
import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from modules.papertrail import prompt_store  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--text")
p.add_argument("--sources")
p.add_argument("--output-dir")
p.add_argument("--model")
p.add_argument("--full", action="store_true")
p.add_argument("--yes", action="store_true")
p.add_argument("--no-arbiter", action="store_true")
p.add_argument("--concurrency")
a, _extra = p.parse_known_args()

out = a.output_dir
with open(os.path.join(out, "fake_calls.log"), "a", encoding="utf-8") as f:
    f.write(("full" if a.full else "retry") + "\n")
fail = os.environ.get("FAKE_FAIL_NAME")
if fail and fail in out:
    sys.exit(3)

names = sorted(prompt_store.snapshot())
if names:
    prompt_store.load(names[0])  # so metadata.prompts.used is non-empty, as in a real run
claim = {"id": "t1", "text": "x", "verdict": "supported"}
if os.environ.get("FAKE_REFUSED") == "1":
    claim["judge_error"] = True
analysis = {
    "text_claims": [claim],
    "metadata": {
        "model": a.model,
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
        "prompts": prompt_store.metadata_block(),
    },
}
with open(os.path.join(out, "analysis.json"), "w", encoding="utf-8") as f:
    json.dump(analysis, f, indent=2)
