"""Offline tests for the commit-sweep hook pair:
  .claude/hooks/record_touched_files.py   (PostToolUse)
  .claude/hooks/warn_commit_sweep.py      (PreToolUse, Bash matcher)

No network. Each test gets its own tmp state dir (COMMIT_SWEEP_STATE_DIR) and
blocks log (RULE_HOOK_BLOCKS_LOG) so tests never touch the real logs/ tree,
and a scratch git repo for the commit-warning tests.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RECORD_HOOK = REPO_ROOT / ".claude" / "hooks" / "record_touched_files.py"
WARN_HOOK = REPO_ROOT / ".claude" / "hooks" / "warn_commit_sweep.py"


def run_hook(script, payload, tmp_path, stdin_text=None):
    """Run a hook script with `payload` (a dict, JSON-encoded) or raw
    `stdin_text` on stdin. Returns (returncode, stderr)."""
    env = dict(os.environ)
    env["COMMIT_SWEEP_STATE_DIR"] = str(tmp_path / "state")
    env["RULE_HOOK_BLOCKS_LOG"] = str(tmp_path / "blocks.log")
    stdin = stdin_text if stdin_text is not None else json.dumps(payload)
    r = subprocess.run(
        [sys.executable, str(script)],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    return r.returncode, r.stderr


def state_dir(tmp_path):
    return tmp_path / "state"


def touched_list(tmp_path, session_id):
    f = state_dir(tmp_path) / ("%s.txt" % session_id)
    if not f.exists():
        return []
    return [l for l in f.read_text().splitlines() if l]


def make_repo(tmp_path):
    """A scratch git repo with one committed base file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *args: subprocess.run(
        ["git"] + list(args), cwd=repo, capture_output=True, text=True, check=True
    )
    run("init", "-q")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Test")
    (repo / "base.txt").write_text("base\n")
    run("add", "base.txt")
    run("commit", "-q", "-m", "base")
    return repo


# ---------------------------------------------------------------------------
# record_touched_files.py
# ---------------------------------------------------------------------------

def test_record_edit_appends_realpath_no_dup(tmp_path):
    session_id = "sess-edit"
    target = tmp_path / "foo.py"
    target.write_text("x")
    payload = {
        "session_id": session_id,
        "cwd": str(tmp_path),
        "tool_name": "Edit",
        "tool_input": {"file_path": str(target), "old_string": "x", "new_string": "y"},
    }
    rc, err = run_hook(RECORD_HOOK, payload, tmp_path)
    assert rc == 0
    assert touched_list(tmp_path, session_id) == [os.path.realpath(str(target))]

    # Second identical event must not duplicate the entry.
    rc, err = run_hook(RECORD_HOOK, payload, tmp_path)
    assert rc == 0
    assert touched_list(tmp_path, session_id) == [os.path.realpath(str(target))]


def test_record_git_add_bash_records_both_not_flags(tmp_path):
    session_id = "sess-add"
    (tmp_path / "foo.txt").write_text("a")
    (tmp_path / "bar.txt").write_text("b")
    payload = {
        "session_id": session_id,
        "cwd": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {"command": "git add foo.txt bar.txt"},
    }
    rc, err = run_hook(RECORD_HOOK, payload, tmp_path)
    assert rc == 0
    got = set(touched_list(tmp_path, session_id))
    expected = {
        os.path.realpath(str(tmp_path / "foo.txt")),
        os.path.realpath(str(tmp_path / "bar.txt")),
    }
    assert got == expected


def test_record_malformed_stdin_exits_0_writes_nothing(tmp_path):
    rc, err = run_hook(RECORD_HOOK, None, tmp_path, stdin_text="not json {{{")
    assert rc == 0
    assert not (state_dir(tmp_path)).exists() or list(state_dir(tmp_path).iterdir()) == []


def test_record_empty_stdin_exits_0(tmp_path):
    rc, err = run_hook(RECORD_HOOK, None, tmp_path, stdin_text="")
    assert rc == 0


# ---------------------------------------------------------------------------
# warn_commit_sweep.py
# ---------------------------------------------------------------------------

def test_warn_untouched_modified_file_blocks_and_warns_once(tmp_path):
    repo = make_repo(tmp_path)
    session_id = "sess-1"
    (repo / "base.txt").write_text("changed\n")

    payload = {
        "session_id": session_id,
        "cwd": str(repo),
        "tool_name": "Bash",
        "tool_input": {"command": 'git commit -am "x"'},
    }
    rc, err = run_hook(WARN_HOOK, payload, tmp_path)
    assert rc == 2
    assert "base.txt" in err
    assert "Commit-sweep warning" in err

    ack = state_dir(tmp_path) / ("%s.ack" % session_id)
    assert ack.exists()

    blocks_log = tmp_path / "blocks.log"
    assert blocks_log.exists()
    assert "commit-sweep-warn" in blocks_log.read_text()
    assert "base.txt" in blocks_log.read_text()


def test_warn_repeat_exact_command_passes_and_clears_ack(tmp_path):
    repo = make_repo(tmp_path)
    session_id = "sess-2"
    (repo / "base.txt").write_text("changed\n")

    payload = {
        "session_id": session_id,
        "cwd": str(repo),
        "tool_name": "Bash",
        "tool_input": {"command": 'git commit -am "x"'},
    }
    rc1, _ = run_hook(WARN_HOOK, payload, tmp_path)
    assert rc1 == 2
    ack = state_dir(tmp_path) / ("%s.ack" % session_id)
    assert ack.exists()

    rc2, err2 = run_hook(WARN_HOOK, payload, tmp_path)
    assert rc2 == 0
    assert not ack.exists()


def test_warn_file_in_touched_list_passes_no_ack(tmp_path):
    repo = make_repo(tmp_path)
    session_id = "sess-3"
    (repo / "base.txt").write_text("changed\n")

    sd = state_dir(tmp_path)
    sd.mkdir(parents=True, exist_ok=True)
    (sd / ("%s.txt" % session_id)).write_text(
        os.path.realpath(str(repo / "base.txt")) + "\n"
    )

    payload = {
        "session_id": session_id,
        "cwd": str(repo),
        "tool_name": "Bash",
        "tool_input": {"command": 'git commit -am "x"'},
    }
    rc, err = run_hook(WARN_HOOK, payload, tmp_path)
    assert rc == 0
    ack = sd / ("%s.ack" % session_id)
    assert not ack.exists()


def test_warn_pathspec_form_untouched_staged_file_blocks(tmp_path):
    repo = make_repo(tmp_path)
    session_id = "sess-4"
    (repo / "other.txt").write_text("new\n")
    subprocess.run(["git", "add", "other.txt"], cwd=repo, check=True)

    payload = {
        "session_id": session_id,
        "cwd": str(repo),
        "tool_name": "Bash",
        "tool_input": {"command": 'git commit -m "x" -- other.txt'},
    }
    rc, err = run_hook(WARN_HOOK, payload, tmp_path)
    assert rc == 2
    assert "other.txt" in err


def test_warn_staged_only_commit_untouched_then_touched(tmp_path):
    repo = make_repo(tmp_path)
    session_id = "sess-5"
    (repo / "new.txt").write_text("new\n")

    add_payload = {
        "session_id": session_id,
        "cwd": str(repo),
        "tool_name": "Bash",
        "tool_input": {"command": "git add new.txt"},
    }
    rc, _ = run_hook(WARN_HOOK, add_payload, tmp_path)  # not a commit, passes
    assert rc == 0
    subprocess.run(["git", "add", "new.txt"], cwd=repo, check=True)

    commit_payload = {
        "session_id": session_id,
        "cwd": str(repo),
        "tool_name": "Bash",
        "tool_input": {"command": 'git commit -m "x"'},
    }
    rc, err = run_hook(WARN_HOOK, commit_payload, tmp_path)
    assert rc == 2
    assert "new.txt" in err

    # Clear the ack this first block just wrote, so the next run is judged
    # on the touched-list logic itself, not the "exact repeat" bypass (that
    # bypass is covered separately above).
    ack = state_dir(tmp_path) / ("%s.ack" % session_id)
    if ack.exists():
        ack.unlink()

    # Now record new.txt as touched (as record_touched_files.py would have
    # done for a real `git add new.txt` Bash call) and retry: passes clean.
    rc, _ = run_hook(RECORD_HOOK, add_payload, tmp_path)
    assert rc == 0

    rc2, err2 = run_hook(WARN_HOOK, commit_payload, tmp_path)
    assert rc2 == 0


def test_warn_non_commit_command_passes(tmp_path):
    payload = {
        "session_id": "sess-6",
        "cwd": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {"command": "ls -la"},
    }
    rc, err = run_hook(WARN_HOOK, payload, tmp_path)
    assert rc == 0
    assert err == ""


def test_warn_unparseable_commit_segment_fails_open(tmp_path):
    repo = make_repo(tmp_path)
    payload = {
        "session_id": "sess-7",
        "cwd": str(repo),
        "tool_name": "Bash",
        "tool_input": {"command": 'git commit -m "unterminated'},
    }
    rc, err = run_hook(WARN_HOOK, payload, tmp_path)
    assert rc == 0


def test_warn_dash_c_repo_dir_from_different_cwd(tmp_path):
    repo = make_repo(tmp_path)
    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()
    session_id = "sess-8"
    (repo / "base.txt").write_text("changed again\n")

    payload = {
        "session_id": session_id,
        "cwd": str(other_cwd),
        "tool_name": "Bash",
        "tool_input": {"command": "git -C %s commit -am x" % repo},
    }
    rc, err = run_hook(WARN_HOOK, payload, tmp_path)
    assert rc == 2
    assert "base.txt" in err


def test_warn_malformed_stdin_exits_0(tmp_path):
    rc, err = run_hook(WARN_HOOK, None, tmp_path, stdin_text="not json")
    assert rc == 0


def test_warn_empty_stdin_exits_0(tmp_path):
    rc, err = run_hook(WARN_HOOK, None, tmp_path, stdin_text="")
    assert rc == 0
