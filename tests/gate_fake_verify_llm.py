#!/usr/bin/env python3
"""Stand-in for verify_my_text.py in tests/test_answer_reuse.py (card #141).

Goes through the REAL LLMClient.call (answer store, fingerprint, call log) with
only the network call replaced: the fake model answers "live:<prompt>". The
first (--full) pass asks three questions built from the text file; the retry
pass asks nothing. FAKE_CHANGE=1 changes the wording of question 2 only, the
way a prompt edit in the second arm would.
"""
import argparse
import datetime
import json
import os
import sys
from unittest.mock import MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from modules.papertrail import llm_client  # noqa: E402

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
llm_client.set_call_log(os.path.join(out, "llm_calls.jsonl"))
if llm_client._reuse_env():
    try:
        llm_client.answer_store_active()
    except (RuntimeError, FileNotFoundError) as e:
        print(e, file=sys.stderr)
        sys.exit(2)


def _fake_completion(**kw):
    choice = MagicMock()
    choice.message.content = "live:" + kw["messages"][0]["content"]
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage.prompt_tokens = 1
    resp.usage.completion_tokens = 1
    return resp


if a.full:
    client = llm_client.LLMClient(model=a.model)
    client._completion = _fake_completion
    with open(a.text, encoding="utf-8") as f:
        body = f.read().strip()
    for i in (1, 2, 3):
        q = f"{body} | question {i}"
        if i == 2 and os.environ.get("FAKE_CHANGE") == "1":
            q += " (reworded)"
        client.call(q, purpose="fake")

analysis = {"text_claims": [{"id": "t1", "text": "x", "verdict": "supported"}],
            "metadata": {"model": a.model,
                         "timestamp": datetime.datetime.now().isoformat()}}
with open(os.path.join(out, "analysis.json"), "w", encoding="utf-8") as f:
    json.dump(analysis, f)
