"""Card #134: the free-gemma gate skips a text it already finished in the same tag.

Two layers, both offline (no model call):
  * decide() in benchmarks/gate_resume.py — every recorded input must match,
    else the text runs as before;
  * the real benchmarks/run_gate_gemma.sh driven end to end with a stand-in
    checker (tests/gate_fake_verify.py) on two tiny texts in a temp folder.
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "benchmarks"))
sys.path.insert(0, ROOT)

import gate_resume  # noqa: E402
from modules.papertrail import prompt_store  # noqa: E402

GATE = os.path.join(ROOT, "benchmarks", "run_gate_gemma.sh")
FAKE = os.path.join(ROOT, "tests", "gate_fake_verify.py")
MODEL = "gemini/gemma-4-31b-it"


# ---------------------------------------------------------------- decide()

class Args:
    def __init__(self, out, text, sources, model=MODEL, arm="none", extra=""):
        self.out, self.text, self.sources = str(out), str(text), str(sources)
        self.model, self.arm, self.extra = model, arm, extra


def _real_prompts_block():
    prompt_store.reset()
    names = sorted(prompt_store.snapshot())
    prompt_store.load(names[0])
    block = prompt_store.metadata_block()
    prompt_store.reset()
    return block


@pytest.fixture
def finished(tmp_path, monkeypatch):
    """A text folder that finished both passes and was stamped."""
    monkeypatch.setenv("GATE_CODE_COMMIT", "c0ffee")
    text = tmp_path / "my_text.md"
    text.write_text("A claim [[a]].\n")
    (tmp_path / "my_text.md.refs.txt").write_text("a: a.txt\n")
    src = tmp_path / "sources"
    src.mkdir()
    (src / "a.txt").write_text("The source says so.\n")
    out = tmp_path / "out"
    out.mkdir()
    analysis = {"text_claims": [{"id": "t1", "verdict": "supported"}],
                "metadata": {"model": MODEL, "prompts": _real_prompts_block()}}
    (out / "analysis.json").write_text(json.dumps(analysis))
    a = Args(out, text, src)
    assert gate_resume.main(["stamp", "--out", a.out, "--text", a.text,
                             "--sources", a.sources, "--model", MODEL]) == 0
    return a


def _decide(a):
    prompt_store.reset()
    try:
        return gate_resume.decide(a)
    finally:
        prompt_store.reset()


def test_identical_inputs_skip(finished):
    skip, reason = _decide(finished)
    assert skip, reason


def test_no_stamp_runs(finished):
    os.remove(os.path.join(finished.out, gate_resume.STAMP))
    assert not _decide(finished)[0]


def test_code_commit_change_runs(finished, monkeypatch):
    monkeypatch.setenv("GATE_CODE_COMMIT", "deadbeef")
    skip, reason = _decide(finished)
    assert not skip and "code_commit" in reason


def test_dirty_tree_never_skips(finished, monkeypatch):
    monkeypatch.setenv("GATE_CODE_COMMIT", "c0ffee+dirty")
    # Even a stamp written under the same dirty state must not skip.
    gate_resume.main(["stamp", "--out", finished.out, "--text", finished.text,
                      "--sources", finished.sources, "--model", MODEL])
    skip, reason = _decide(finished)
    assert not skip and "uncommitted" in reason


def test_model_change_runs(finished):
    finished.model = "gemini/other"
    assert not _decide(finished)[0]


def test_prompt_arm_change_runs(finished):
    finished.arm = "pt_x.txt=benchmarks/prompt_variants/x.txt"
    assert not _decide(finished)[0]


def test_extra_flags_change_runs(finished):
    finished.extra = "--direction-check"
    assert not _decide(finished)[0]


def test_text_change_runs(finished):
    with open(finished.text, "a") as f:
        f.write("Another claim [[a]].\n")
    skip, reason = _decide(finished)
    assert not skip and "text_sha1" in reason


def test_refs_change_runs(finished):
    with open(finished.text + ".refs.txt", "a") as f:
        f.write("b: b.txt\n")
    assert not _decide(finished)[0]


def test_source_change_runs(finished):
    with open(os.path.join(finished.sources, "a.txt"), "a") as f:
        f.write("More.\n")
    skip, reason = _decide(finished)
    assert not skip and "sources_sha1" in reason


def test_new_source_file_runs(finished):
    with open(os.path.join(finished.sources, "b.txt"), "w") as f:
        f.write("New.\n")
    assert not _decide(finished)[0]


def test_analysis_rewritten_runs(finished):
    p = os.path.join(finished.out, "analysis.json")
    data = json.load(open(p))
    data["metadata"]["timestamp"] = "later"
    json.dump(data, open(p, "w"))
    skip, reason = _decide(finished)
    assert not skip and "rewritten" in reason


def test_prompt_fingerprint_change_runs(finished):
    p = os.path.join(finished.out, "analysis.json")
    data = json.load(open(p))
    used = data["metadata"]["prompts"]["used"][0]
    data["metadata"]["prompts"]["fingerprints"][used] = "000000000000"
    json.dump(data, open(p, "w"))
    gate_resume.main(["stamp", "--out", finished.out, "--text", finished.text,
                      "--sources", finished.sources, "--model", MODEL])
    skip, reason = _decide(finished)
    assert not skip and "prompt files changed" in reason


def test_missing_prompt_fingerprints_runs(finished):
    p = os.path.join(finished.out, "analysis.json")
    data = json.load(open(p))
    del data["metadata"]["prompts"]
    json.dump(data, open(p, "w"))
    gate_resume.main(["stamp", "--out", finished.out, "--text", finished.text,
                      "--sources", finished.sources, "--model", MODEL])
    assert not _decide(finished)[0]


def test_refused_call_in_analysis_runs(finished):
    p = os.path.join(finished.out, "analysis.json")
    data = json.load(open(p))
    data["text_claims"][0]["judge_error"] = True
    json.dump(data, open(p, "w"), indent=1)
    gate_resume.main(["stamp", "--out", finished.out, "--text", finished.text,
                      "--sources", finished.sources, "--model", MODEL])
    skip, reason = _decide(finished)
    assert not skip and "refused" in reason


def test_forget_removes_stamp(finished):
    gate_resume.main(["forget", "--out", finished.out])
    assert not os.path.exists(os.path.join(finished.out, gate_resume.STAMP))


def test_overrides_from_arm():
    assert gate_resume.overrides_from_arm("none") == {}
    assert gate_resume.overrides_from_arm("a.txt=x/a.txt,b.txt=y/b.txt") == {
        "a.txt": "x/a.txt", "b.txt": "y/b.txt"}


# ------------------------------------------------- the real gate script, end to end

@pytest.fixture
def project(tmp_path):
    rows = []
    for name in ("alpha", "beta"):
        d = tmp_path / "in" / name
        (d / "sources").mkdir(parents=True)
        (d / "my_text.md").write_text(f"{name} claim [[a]].\n")
        (d / "sources" / "a.txt").write_text(f"{name} source.\n")
        rows.append(f"{name}|{d / 'my_text.md'}|{d / 'sources'}|{tmp_path / 'nodonor'}")
    return tmp_path, ";".join(rows)


def _gate(tmp_path, texts, tag="t134", **env_extra):
    env = dict(os.environ)
    env.update({"PY": sys.executable, "GATE_TEXTS": texts,
                "GATE_OUT_ROOT": str(tmp_path / "out"), "GATE_VERIFY_SCRIPT": FAKE,
                "GATE_CODE_COMMIT": "c0ffee"})
    env.pop("RESUME", None)
    env.pop("PROMPTS", None)
    env.pop("EXTRA_FLAGS", None)
    env.update(env_extra)
    r = subprocess.run(["bash", GATE, tag, "2"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=300)
    return r.stdout + r.stderr


def _calls(tmp_path, name, tag="t134"):
    p = tmp_path / "out" / f"gate_{tag}_{name}" / "fake_calls.log"
    return p.read_text().split() if p.exists() else []


def test_gate_rerun_skips_finished_texts(project):
    tmp_path, texts = project
    log1 = _gate(tmp_path, texts)
    assert _calls(tmp_path, "alpha") == ["full", "retry"]
    assert _calls(tmp_path, "beta") == ["full", "retry"]
    assert "skipped" not in log1
    out_alpha = tmp_path / "out" / "gate_t134_alpha"
    assert (out_alpha / ".code_commit").read_text().strip() == "c0ffee"
    assert (out_alpha / gate_resume.STAMP).exists()

    log2 = _gate(tmp_path, texts)
    assert "alpha skipped: already finished in this test" in log2
    assert "beta skipped: already finished in this test" in log2
    assert _calls(tmp_path, "alpha") == ["full", "retry"]   # no new call
    assert _calls(tmp_path, "beta") == ["full", "retry"]
    # The refused-call check still reads every text.
    section = log2.split("REFUSED CALLS PER RUN", 1)[1]
    assert "alpha" in section and "beta" in section


def test_gate_crash_reruns_only_the_unfinished_text(project):
    tmp_path, texts = project
    _gate(tmp_path, texts, FAKE_FAIL_NAME="gate_t134_beta")
    assert not (tmp_path / "out" / "gate_t134_beta" / gate_resume.STAMP).exists()
    log = _gate(tmp_path, texts)
    assert "alpha skipped" in log
    assert "beta skipped" not in log
    assert _calls(tmp_path, "alpha") == ["full", "retry"]
    assert _calls(tmp_path, "beta") == ["full", "retry", "full", "retry"]


def test_gate_changed_text_reruns_only_that_text(project):
    tmp_path, texts = project
    _gate(tmp_path, texts)
    with open(tmp_path / "in" / "beta" / "my_text.md", "a") as f:
        f.write("edited\n")
    log = _gate(tmp_path, texts)
    assert "alpha skipped" in log and "beta skipped" not in log
    assert len(_calls(tmp_path, "beta")) == 4


def test_gate_new_code_commit_reruns_everything(project):
    tmp_path, texts = project
    _gate(tmp_path, texts)
    log = _gate(tmp_path, texts, GATE_CODE_COMMIT="newcommit")
    assert "skipped" not in log
    assert len(_calls(tmp_path, "alpha")) == 4
    assert (tmp_path / "out" / "gate_t134_alpha" / ".code_commit").read_text().strip() == "newcommit"


def test_gate_resume_off_reruns_everything(project):
    tmp_path, texts = project
    _gate(tmp_path, texts)
    log = _gate(tmp_path, texts, RESUME="0")
    assert "skipped" not in log
    assert len(_calls(tmp_path, "alpha")) == 4 and len(_calls(tmp_path, "beta")) == 4


def test_gate_fresh_tag_runs_everything(project):
    tmp_path, texts = project
    _gate(tmp_path, texts)
    log = _gate(tmp_path, texts, tag="t134b")
    assert "skipped" not in log
    assert _calls(tmp_path, "alpha", "t134b") == ["full", "retry"]


def test_gate_refused_calls_are_not_skipped(project):
    tmp_path, texts = project
    _gate(tmp_path, texts, FAKE_REFUSED="1")
    log = _gate(tmp_path, texts)
    assert "skipped" not in log
    assert "refused or failed calls" in log
