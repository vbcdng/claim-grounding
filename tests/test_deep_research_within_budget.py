"""Card 120: the checked-research workflow script, run with fake helpers under node.

No model and no web: tests/js/run_deep_research_harness.mjs stands in for the workflow
runtime (agent/parallel/pipeline/budget) and answers every helper with canned output in
which every claim is central + primary — the tie that, in run wf_8611bd4f-248, let the
first two angles take all 25 verification slots.
"""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(ROOT, "tests", "js", "run_deep_research_harness.mjs")
INSTALLED = os.path.join(ROOT, ".claude", "skills", "checked-research", "deep_research_within_budget.js")
PROPOSED = os.path.join(ROOT, "docs", "proposed_skills", "checked-research", "deep_research_within_budget.js")
SCRIPT = INSTALLED if os.path.exists(INSTALLED) else PROPOSED
# The built-in script as Claude Code 2.1.270 recorded it for card 67's live run (local
# only). Its path is a setting (card 166: no machine-specific path in a published
# file); without it the comparison test skips.
BUILTIN = os.environ.get("PAPERTRAIL_BUILTIN_DEEP_RESEARCH_JS", "")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def run(tmp_path, script=SCRIPT, **scenario):
    scenario.setdefault("args", {"question": "What did the chaebol do in 1987?"})
    p = tmp_path / "scenario.json"
    p.write_text(json.dumps(scenario))
    out = subprocess.run(["node", HARNESS, script, str(p)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def verify_angles(res):
    """How many vote helpers ran per angle (label v<n>:"claim k from https://siteangleX...")."""
    counts = {}
    for c in res["calls"]:
        if c["label"] and c["label"][:1] == "v" and ":" in c["label"]:
            angle = c["label"].split("siteangle")[1].split(".")[0]
            counts[angle] = counts.get(angle, 0) + 1
    return counts


@pytest.mark.skipif(not os.path.exists(BUILTIN), reason="recorded built-in script not on this machine")
def test_builtin_reproduces_the_starvation(tmp_path):
    # Same fake answers through the unchanged built-in. Every claim ties (central + primary),
    # so the 25 slots go in angle order: the first angle alone yields 25+ claims and takes
    # all of them (on 9/13 the first two angles did, with 8 + 17 such claims).
    res = run(tmp_path, script=BUILTIN, args="What did the chaebol do in 1987?")
    counts = verify_angles(res)
    assert sorted(counts) == ["0"]
    assert sum(counts.values()) == 75
    # with 2 claims per source the first angles still fill the slots and the last get none
    res = run(tmp_path, script=BUILTIN, args="q?", claimsPerSource=2)
    counts = verify_angles(res)
    assert "4" not in counts and sum(counts.values()) == 75
    assert sum(1 for c in res["calls"] if (c["label"] or "").startswith("fetch:")) > 15  # MAX_FETCH is bypassed
    assert all(c["model"] is None for c in res["calls"])


def test_every_angle_gets_its_share(tmp_path):
    res = run(tmp_path)
    counts = verify_angles(res)
    assert counts == {str(i): 2 * 3 for i in range(5)}  # 2 claims x 3 votes, every angle
    cov = res["result"]["coverage"]
    assert [c["confirmed"] for c in cov] == [2] * 5
    assert [c["subquestion"] for c in cov] == ["sub%d" % i for i in range(5)]
    fetches = [c for c in res["calls"] if (c["label"] or "").startswith("fetch:")]
    assert len(fetches) == 15  # 3 per angle, strict
    assert len(res["calls"]) == 52 == res["result"]["stats"]["helpersLaunched"]
    assert res["result"]["reportWrittenBy"] == "synthesis helper"


def test_helper_model_default_and_override(tmp_path):
    res = run(tmp_path)
    by = {c["label"]: c["model"] for c in res["calls"]}
    assert by["scope"] is None and by["synthesize"] is None
    assert {c["model"] for c in res["calls"] if c["label"] not in ("scope", "synthesize")} == {"sonnet"}
    res = run(tmp_path, args={"question": "q?", "limits": {"helperModel": None}})
    assert all(c["model"] is None for c in res["calls"])


def test_plain_string_args_still_work(tmp_path):
    res = run(tmp_path, args="What did the chaebol do in 1987?")
    assert res["result"]["question"] == "What did the chaebol do in 1987?"
    assert res["result"]["reportWrittenBy"] == "synthesis helper"


def test_report_written_when_allowance_runs_out(tmp_path):
    # 40 helpers answer, then every later one returns null (the 9/13 limit message).
    res = run(tmp_path, dieAfter=40)
    r = res["result"]
    assert r["reportWrittenBy"].startswith("script (no model)")
    assert r["findings"], "the confirmed claims must be in the written report"
    assert all(f["sources"] and f["evidence"].startswith("Quote:") for f in r["findings"])
    assert "summary" in r and "caveats" in r


def test_helper_cap_stops_launching_and_is_logged(tmp_path):
    res = run(tmp_path, args={"question": "q?", "limits": {"maxHelpers": 30}})
    r = res["result"]
    assert r["stats"]["helpersLaunched"] <= 31  # 30 + the synthesis helper
    assert r["stats"]["helpersNotLaunchedForLimit"] > 0
    assert any("not launched (helper cap 30)" in m for m in res["logs"])
    assert r["reportWrittenBy"] in ("synthesis helper",) or r["reportWrittenBy"].startswith("script")


def test_output_token_ceiling_keeps_reserve_for_synthesis(tmp_path):
    res = run(tmp_path, spentPerCall=5000,
              args={"question": "q?", "limits": {"maxOutputTokens": 150000, "reserveOutputTokens": 40000}})
    r = res["result"]
    # The ceiling minus the reserve is 110,000 = 22 fake helpers of 5,000 each; after that no
    # new claim's votes are admitted, but the synthesis helper still runs on the reserve.
    assert r["stats"]["helpersLaunched"] < 52
    assert r["stats"]["helpersNotLaunchedForLimit"] > 0
    assert r["stats"]["confirmed"] >= 1
    assert r["reportWrittenBy"] == "synthesis helper"
    assert r["unverified"] == []  # votes are admitted per claim, so no claim is left short of votes


def test_six_angles_fit_default_cap(tmp_path):
    res = run(tmp_path, angles=6)
    assert res["result"]["stats"]["helpersNotLaunchedForLimit"] == 0
    assert res["result"]["stats"]["helpersLaunched"] == 62
