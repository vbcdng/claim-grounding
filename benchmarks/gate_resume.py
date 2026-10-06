#!/usr/bin/env python3
"""Skip-a-finished-text helper for benchmarks/run_gate_gemma.sh (card #134, 2026-09-28).

Problem: the gate runs every text's first pass with --full, so after a crash,
a restart or a laptop sleep the texts that had already FINISHED were judged
again from zero (card 45, 2026-09-27: 5 of 6 texts done at the restart, all 5
re-run, ~20 h lost). checkpoint.py only resumes a text cut off halfway.

Contract:
  * After a text's two passes both exit 0, the gate calls `stamp`, which writes
    <out>/.gate_text_done.json recording: the code commit, the model, the prompt
    arm, EXTRA_FLAGS, sha1 of the text file (+ its .refs.txt), one sha1 over every
    file under the sources dir (relative name + bytes), sha1 of analysis.json, and
    the analysis's metadata.prompts fingerprints.
  * Before the first pass, the gate calls `check`. Exit 0 = SKIP this text; exit 1
    = run it as before. Skip ONLY when every recorded field equals now AND the
    analysis.json on disk is byte-identical to the stamped one AND the current
    prompt snapshot (config/prompts/ + the arm's overrides) shows no change in
    any prompt the analysis used (rerun.changed_prompts == empty set) AND the
    analysis holds no failure marker (task #37). Anything missing, unreadable or
    different => run. A dirty tracked tree ("<sha>+dirty") never skips, because
    the commit then does not name the code.
  * `forget` deletes the stamp; the gate calls it right before a first pass so a
    pass that crashes leaves no stamp behind.
  * `commit` prints the code state; the gate writes it to <out>/.code_commit.

A fresh tag has no stamp, so it behaves exactly as before. RESUME=0 in the gate
turns the skip off. GATE_CODE_COMMIT overrides the code state (tests only).
Pure stdlib + prompt_store/rerun, no LLM, no network.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from modules.papertrail import prompt_store, rerun  # noqa: E402

STAMP = ".gate_text_done.json"
STAMP_VERSION = 1
# The same markers the gate's refused-call count greps for (task #37).
FAILURE_MARKERS = re.compile(r'"no LLM response"|"judge_error": true|"checks_failed"')


def file_sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def text_sha1(text):
    """The text file plus its sibling refs file (the marker -> source map)."""
    h = hashlib.sha1(file_sha1(text).encode())
    refs = text + ".refs.txt"
    if os.path.isfile(refs):
        h.update(b"|refs|" + file_sha1(refs).encode())
    return h.hexdigest()


def sources_sha1(sources):
    h = hashlib.sha1()
    for dirpath, dirnames, filenames in os.walk(sources):
        dirnames.sort()
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            h.update(os.path.relpath(p, sources).encode("utf-8") + b"\0")
            h.update(file_sha1(p).encode() + b"\n")
    return h.hexdigest()


def code_state():
    forced = os.environ.get("GATE_CODE_COMMIT")
    if forced:
        return forced
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                               cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return sha + ("+dirty" if dirty else "")


def overrides_from_arm(arm):
    """PROMPTS="name=path[,name=path]" (the gate's .prompt_arm value; 'none' = no override)."""
    if not arm or arm == "none":
        return {}
    out = {}
    for pair in arm.split(","):
        name, _, path = pair.partition("=")
        if name and path:
            out[name.strip()] = path.strip()
    return out


def current_fields(a):
    return {
        "version": STAMP_VERSION,
        "code_commit": code_state(),
        "model": a.model,
        "prompt_arm": a.arm or "none",
        "extra_flags": " ".join((a.extra or "").split()),
        "text_sha1": text_sha1(a.text),
        "sources_sha1": sources_sha1(a.sources),
    }


def decide(a):
    """(skip: bool, reason: str)."""
    out = a.out
    stamp_path = os.path.join(out, STAMP)
    analysis_path = os.path.join(out, "analysis.json")
    if not os.path.isfile(stamp_path):
        return False, "no finished-text record from an earlier run of this tag"
    if not os.path.isfile(analysis_path):
        return False, "no analysis.json on disk"
    try:
        with open(stamp_path, encoding="utf-8") as f:
            stamp = json.load(f)
    except (OSError, ValueError) as e:
        return False, f"finished-text record unreadable ({e})"
    now = current_fields(a)
    if now["code_commit"] in ("unknown", "") or now["code_commit"].endswith("+dirty"):
        return False, (f"code state is '{now['code_commit']}' (uncommitted changes to tracked "
                       f"files, or no git), so the commit does not name the code")
    for key, val in now.items():
        if stamp.get(key) != val:
            return False, f"{key} changed (recorded {stamp.get(key)!r}, now {val!r})"
    if stamp.get("analysis_sha1") != file_sha1(analysis_path):
        return False, "analysis.json was rewritten after the finished-text record"
    with open(analysis_path, encoding="utf-8") as f:
        raw = f.read()
    if FAILURE_MARKERS.search(raw):
        return False, "analysis.json holds refused or failed calls"
    try:
        analysis = json.loads(raw)
    except ValueError as e:
        return False, f"analysis.json unreadable ({e})"
    meta = analysis.get("metadata") or {}
    prompts_now = prompt_store.snapshot(overrides_from_arm(a.arm))
    changed = rerun.changed_prompts(meta.get("prompts"), prompts_now)
    if changed is None:
        return False, "analysis.json has no prompt fingerprints"
    if changed:
        return False, f"prompt files changed: {', '.join(sorted(changed))}"
    return True, (f"finished earlier under the same code {now['code_commit'][:12]}, model, "
                  f"prompts, text and sources ({stamp.get('stamped_at', '?')})")


def cmd_check(a):
    skip, reason = decide(a)
    print(("SKIP: " if skip else "RUN: ") + reason)
    return 0 if skip else 1


def cmd_stamp(a):
    analysis_path = os.path.join(a.out, "analysis.json")
    if not os.path.isfile(analysis_path):
        print("no analysis.json — nothing recorded", file=sys.stderr)
        return 1
    rec = current_fields(a)
    rec["analysis_sha1"] = file_sha1(analysis_path)
    with open(analysis_path, encoding="utf-8") as f:
        meta = (json.load(f).get("metadata") or {})
    rec["analysis_model"] = meta.get("model")
    rec["analysis_timestamp"] = meta.get("timestamp")
    import datetime
    rec["stamped_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tmp = os.path.join(a.out, STAMP + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)
    os.replace(tmp, os.path.join(a.out, STAMP))
    print(f"recorded {a.out} as finished")
    return 0


def cmd_forget(a):
    try:
        os.remove(os.path.join(a.out, STAMP))
    except FileNotFoundError:
        pass
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("action", choices=["check", "stamp", "forget", "commit"])
    p.add_argument("--out")
    p.add_argument("--text")
    p.add_argument("--sources")
    p.add_argument("--model")
    p.add_argument("--arm", default="none")
    p.add_argument("--extra", default="")
    a = p.parse_args(argv)
    if a.action == "commit":
        print(code_state())
        return 0
    if not a.out:
        p.error("--out is required")
    if a.action == "forget":
        return cmd_forget(a)
    for need in ("text", "sources", "model"):
        if not getattr(a, need):
            p.error(f"--{need} is required")
    return cmd_check(a) if a.action == "check" else cmd_stamp(a)


if __name__ == "__main__":
    sys.exit(main())
