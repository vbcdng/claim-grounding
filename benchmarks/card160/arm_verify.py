#!/usr/bin/env python3
"""Stability study (internal label CARD160): run the checker of a CLEAN
CHECKOUT (the version being measured) with one test switch applied from
outside, so the measured code stays byte-identical to that commit.

Which version: commit 4899c40 (MEASURED_COMMIT below), the sentence-splitting
path off (its default). The checkout is made by make_checkout.sh; nothing here
edits it, and a checkout at any other commit or with an edited tracked file is
REFUSED before anything runs.

Called by the gate scripts in place of verify_my_text.py (their test seam
GATE_VERIFY_SCRIPT, given as an ABSOLUTE path to this file), with exactly the
arguments verify_my_text.py takes. Every switch is an environment variable.
With none set, this file still does nine things around the unchanged
verify_my_text.py: it refuses a wrong checkout and any checker argument or
gate EXTRA_FLAGS no registered arm uses (argv_problem; a gate PROMPTS never
reaches this file, so that folder gets no stamp and the scorer leaves it out),
it compares this start's inputs (text, reference list, sources, search-index
cache, search model) with the private fingerprint record and refuses on any
difference (input_problem, the same comparison as `arms.py preflight`), it moves
aside a save-as-you-go journal (checkpoint.jsonl) left by an interrupted
start, so a stopped text is judged again from its first claim, it records the
run (.card160_runs.jsonl: commit, judge, the full checker command line, Claude
CLI version, library fingerprint), it records in the checker's call log which
claim every call belongs to (install_claim_attribution, so the seller behind a
changed verdict can be named), it installs the used-up-allowance guard of
the judge's kind (arm_guard.py; none for free Gemma), and after the gate's
second pass it repeats that pass while any claim's verdict still came from a
failed call (retry loop). The stability-mark/reuse refusal (reuse_problem) is
the ninth. For a Claude judge it also sets
DISABLE_AUTOUPDATER=1, runs the study's ONE fixed copy of the Claude program
(PINNED_CLAUDE_*, refused when missing or changed), and refuses an r2/r3 whose installed Claude program is not
the one that judge's r1 recorded (cli_change_problem; CARD160_ALLOW_CLI_CHANGE=1
overrides, and the scorer then reports the arm apart).

  CARD160_CHECKOUT=<dir>   REQUIRED. The clean checkout (its verify_my_text.py
                           and modules/ are what runs).
  CARD160_ARM=<label>      the arm's name; stamped into <output-dir>/.card160_arm
                           with every switch below and the checkout's commit. A
                           folder stamped by a different arm is refused (one
                           folder = one arm).
  CARD160_PROMPTS="name=path[,name=path]"
                           prompt variants, installed through the checkout's
                           matcher.PROMPT_OVERRIDES (in-process, config/prompts/
                           is never written) — the same mechanism as
                           benchmarks/verify_with_prompts.py. Use this, not the
                           gate's PROMPTS=, because PROMPTS= bypasses
                           GATE_VERIFY_SCRIPT.
  CARD160_BIAS_NOTE=1      add one sentence (BIAS_NOTE below) right before the
                           CLAIM in both verdict instructions (per-source and
                           combined judge), on top of whatever instruction the
                           arm uses. The edited copies are written into
                           <output-dir>/.card160_prompts/ and installed as
                           overrides. A fair judge must not move.
  CARD160_DOUBT_NOTE=1     the same, with the opposite note (DOUBT_NOTE below:
                           a reviewer doubts the claim is supported), inserted
                           at exactly the same place in both instructions. It
                           separates a judge that leans toward the writer from
                           one that any note unsettles. Refused together with
                           CARD160_BIAS_NOTE=1 (one arm, one change).
  CARD160_SHUFFLE_SEED=<n> shuffle the order of the evidence inside one judge
                           prompt, in the two places a prompt holds several
                           pieces: the per-source passages of the combined judge,
                           and the extracted sentences of the full-text judge.
                           The order in which the per-source judge tries its
                           candidate sentences one by one is NOT changed (see
                           SHUFFLE_EDITS: it decides which evidence is kept). The
                           order is a deterministic function of the seed and the
                           claim, and always differs from the original when there
                           are two or more items. Counts land in
                           <output-dir>/.card160_shuffle_stats.json.
  CARD160_CLAUDE_FAIR=1    for a Claude judge (claude-code/<model>) only: give it
                           the SAME judge instructions a Gemma run gets (the
                           checker otherwise swaps in an older Haiku-tuned
                           instruction, benchmarks/pt_combined_judgment_haiku_v1.txt),
                           read its answers with a tolerant JSON reader (a
                           "see [3]" in its prose otherwise becomes the answer),
                           and allow 600 s per call instead of 240 (240 s was
                           measured to cut off Sonnet calls). Without these three
                           a Claude arm would not ask the question a Gemma arm asks.

Always on (arm_guard.py): a used-up allowance never becomes a verdict. A claude-code judge waits out the subscription limit and asks
again; an openrouter judge stops the whole arm on an empty balance (exit 75,
one "CARD160 BALANCE EMPTY" line). A stop file left by either refuses every
later start of that kind until it is cleared. Details in arm_guard.py.

Nothing here makes a model call by itself; the calls are the checker's own.
"""

import importlib.util
import json
import os
import random
import re
import runpy
import subprocess
import sys
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("card160_arm_guard",
                                               os.path.join(_HERE, "arm_guard.py"))
arm_guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(arm_guard)

# The measured version. Any other commit, or an edited tracked file in the
# checkout, is refused before the checker is imported.
MEASURED_COMMIT = "4899c40ae6ebe34649a94d633308cf0c15658029"

# The ONE Claude program every Claude start of the study uses (author ruling
# 2026-10-05: one version only). The installed program updates itself every
# day or two (2.1.287 on 2026-10-02, 2.1.289 on 2026-10-05), so the study runs
# a fixed copy of one version's program file instead: `arms.py pin-claude`
# copies it into PINNED_CLAUDE_DIR, and every Claude start refuses unless that
# copy exists with exactly this fingerprint, then puts its folder first on PATH
# so the checker (which finds the program with shutil.which("claude")) starts
# that copy and nothing else.
PINNED_CLAUDE_VERSION = "2.1.289"
PINNED_CLAUDE_SHA256 = "a186b99e4a9c88366cd49df2f7dad56c61fc306ef0140b19ee64b7c42a8d1348"
PINNED_CLAUDE_DIR = os.path.join(arm_guard.DATA, "card160_claude_cli")

ENV_KEYS = ("CARD160_ARM", "CARD160_PROMPTS", "CARD160_BIAS_NOTE",
            "CARD160_SHUFFLE_SEED", "CARD160_CLAUDE_FAIR", "CARD160_DOUBT_NOTE")

# The two instructions a verdict comes from (per-source judge; combined and
# full-text judge).
VERDICT_PROMPTS = ("pt_support_judgment_prompt.txt", "pt_combined_judgment_prompt.txt")

# The "biased note" invariance test: a statement of the writer's confidence,
# which carries no evidence and must not move a verdict.
BIAS_NOTE = ("Note from the writer: I am confident that this claim is supported "
             "by the cited source.\n\n")
BIAS_ANCHOR = "CLAIM:\n{CLAIM}"
# The opposite note (the author's decision of 2026-10-05: the ninth paid
# arm "doubt"), inserted at the same anchor. Direction of concern here:
# supported turning unsupported.
DOUBT_NOTE = ("Note from a reviewer: I doubt that this claim is supported by the "
              "cited source.\n\n")

# The two one-line edits the shuffle makes, each applied to the CHECKOUT's
# own function source text at run time (so no copy of the measured code lives
# here). Each must match exactly once, or the run stops before any call.
# Both reorder text INSIDE one prompt and nothing else. The per-source judge's
# candidate order (_judge_source's judge_order) is deliberately NOT shuffled:
# there any supported candidate wins, so the order cannot change that answer,
# but it decides which sentence is kept as proof and which sentence a source
# with no supported candidate returns, and those feed the full-text fallback,
# the partial-support check, the covering set and the essay scorer — a shuffle
# there would change which evidence is kept, not only its order (design
# review 2026-10-02, item 1).
SHUFFLE_EDITS = {
    "_judge_extracted": (
        '    window = " ".join(parts)\n',
        '    window = " ".join(_card160_permute(parts, claim))\n'),
    "_combined_judge": (
        '    passage = "\\n\\n".join(f"From {title}: {win}" for title, win in labeled_windows)\n',
        '    passage = "\\n\\n".join(f"From {title}: {win}" for title, win in _card160_permute(labeled_windows, claim))\n'),
}

STATS = {"lists_seen": 0, "lists_reordered": 0, "single_item_lists": 0}


def permute(items, claim, seed):
    """A deterministic reordering of `items` for this claim and seed. With two
    or more items the result always differs from the input order (a random
    permutation that happens to equal the original is rotated by one)."""
    items = list(items)
    STATS["lists_seen"] += 1
    if len(items) < 2:
        STATS["single_item_lists"] += 1
        return items
    rng = random.Random(f"card160|{seed}|{claim}|{len(items)}")
    order = list(range(len(items)))
    rng.shuffle(order)
    if order == list(range(len(items))):
        order = order[1:] + order[:1]
    STATS["lists_reordered"] += 1
    return [items[i] for i in order]


def parse_prompt_pairs(spec):
    """'a=path,b=path' -> {a: abs path}. Raises ValueError on a bad pair or a
    missing file."""
    out = {}
    for pair in [p for p in (spec or "").split(",") if p.strip()]:
        if "=" not in pair:
            raise ValueError(f"not a name=path pair: {pair}")
        name, path = pair.split("=", 1)
        path = os.path.abspath(path.strip())
        if not os.path.isfile(path):
            raise ValueError(f"prompt file does not exist: {path}")
        out[name.strip()] = path
    return out


def add_bias_note(text, note=BIAS_NOTE):
    """The instruction with `note` (BIAS_NOTE, or DOUBT_NOTE for the doubt arm)
    inserted right before its CLAIM block. Raises ValueError unless the anchor
    occurs exactly once."""
    n = text.count(BIAS_ANCHOR)
    if n != 1:
        raise ValueError(f"the instruction holds {n} '{BIAS_ANCHOR!r}' blocks, expected 1")
    return text.replace(BIAS_ANCHOR, note + BIAS_ANCHOR)


def install_bias_note(matcher, checkout, out_dir, note=BIAS_NOTE):
    """Write note-carrying copies of both verdict instructions (starting from
    the override already installed, else the checkout's own file) into
    <out_dir>/.card160_prompts/ and install them. Returns {name: path}."""
    dest = os.path.join(out_dir, ".card160_prompts")
    os.makedirs(dest, exist_ok=True)
    done = {}
    for name in VERDICT_PROMPTS:
        src = matcher.PROMPT_OVERRIDES.get(name) or os.path.join(checkout, "config", "prompts", name)
        with open(src, encoding="utf-8") as f:
            text = add_bias_note(f.read(), note)
        path = os.path.join(dest, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        matcher.PROMPT_OVERRIDES[name] = path
        done[name] = path
    return done


class LockedOverrides(dict):
    """matcher.PROMPT_OVERRIDES that refuses later writes to the locked names,
    so the checker's claude-code path cannot swap the Haiku instruction over
    the one this arm chose (CARD160_CLAUDE_FAIR)."""

    def __init__(self, base, locked):
        super().__init__(base)
        self.locked = set(locked)
        self.refused = []

    def __setitem__(self, key, value):
        if key in self.locked:
            self.refused.append((key, value))
            print(f"[card160] kept this arm's instruction for {key}; ignored the "
                  f"checker's swap to {value}", flush=True)
            return
        super().__setitem__(key, value)


def apply_shuffle(matcher, seed):
    import inspect
    # Check every edit before applying any, so a mismatch leaves the module
    # exactly as it was.
    edited = []
    for fname, (old, new) in SHUFFLE_EDITS.items():
        fn = getattr(matcher, fname)
        src = inspect.getsource(fn)
        if src.count(old) != 1:
            raise RuntimeError(f"card160 shuffle: the line to edit in {fname} was "
                               f"found {src.count(old)} times, expected once — this "
                               "is not the code the switch was built for.")
        # Pad so the edited function keeps its real line numbers (tracebacks
        # and inspect then point at the right lines of the checkout's file).
        pad = "\n" * (fn.__code__.co_firstlineno - 1)
        edited.append(compile(pad + src.replace(old, new), matcher.__file__, "exec"))
    matcher._card160_permute = lambda items, claim: permute(items, claim, seed)
    for code in edited:
        exec(code, matcher.__dict__)


def _tolerant_extract_json_factory(llm_client):
    """A tolerant JSON reader (the strict reader first, then the longest JSON
    object anywhere in the answer), as a function to graft onto the
    checkout's one."""
    import json as _json

    def _scan_json(text):
        dec = _json.JSONDecoder()
        best, best_len = None, 0
        variants = [text]
        no_trailing = re.sub(r",\s*([}\]])", r"\1", text)
        if no_trailing != text:
            variants.append(no_trailing)
        for t in variants:
            for i, ch in enumerate(t):
                if ch not in "{[":
                    continue
                try:
                    obj, end = dec.raw_decode(t, i)
                except ValueError:
                    continue
                if isinstance(obj, (dict, list)) and end - i > best_len:
                    best, best_len = obj, end - i
            if best is not None:
                return best
        return None

    def extract_json(text):
        if not text:
            return None
        fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        for c in fenced + [text]:
            try:
                return _json.loads(c.strip())
            except Exception:
                continue
        for c in fenced + [text]:
            obj = _scan_json(c)
            if obj is not None:
                return obj
        llm_client.logger.warning("Failed to parse JSON from LLM response")
        return None

    return extract_json


def apply_claude_fair(matcher):
    from modules.papertrail import llm_client, claude_code_backend
    # Same instructions as a Gemma run: lock the two judge prompts to whatever
    # this arm installed (production when nothing was installed).
    current = dict(matcher.PROMPT_OVERRIDES)
    matcher.PROMPT_OVERRIDES = LockedOverrides(current, set(VERDICT_PROMPTS))
    # The tolerant reader. Every checker module takes it with
    # "from .llm_client import extract_json", so import them all now and rebind
    # the name in each; a module imported later reads llm_client's new binding.
    tolerant = _tolerant_extract_json_factory(llm_client)
    import importlib
    for name in ("arbiter", "argument_map", "citation_scope", "crux", "dedup",
                 "evidence_independence", "own_claims"):
        try:
            importlib.import_module(f"modules.papertrail.{name}")
        except Exception:
            pass
    orig = llm_client.extract_json
    for mod in list(sys.modules.values()):
        if getattr(mod, "extract_json", None) is orig:
            mod.extract_json = tolerant
    llm_client.extract_json = tolerant
    claude_code_backend._TIMEOUT_S = 600


def checkout_commit(checkout):
    """'<sha>' or '<sha>+dirty' of the checkout, '' when git cannot tell."""
    try:
        sha = subprocess.run(["git", "-C", checkout, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        dirty = subprocess.run(["git", "-C", checkout, "status", "--porcelain",
                                "--untracked-files=no"],
                               capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception:
        return ""
    return sha + ("+dirty" if dirty else "") if sha else ""


def checkout_problem(checkout, want=None, git=None):
    """'' when the checkout sits exactly at the measured commit with no edited
    tracked file; else a sentence saying what is wrong. Untracked files (the
    venv/data links make_checkout.sh adds) do not count."""
    want = want or MEASURED_COMMIT
    git = git or (lambda *a: subprocess.run(["git", "-C", checkout, *a], capture_output=True,
                                            text=True, timeout=60).stdout.strip())
    try:
        head = git("rev-parse", "HEAD")
        dirty = git("status", "--porcelain", "--untracked-files=no")
    except Exception as e:
        return f"git could not read the checkout {checkout}: {type(e).__name__}: {e}"
    if head != want:
        return f"the checkout {checkout} is at {head or 'no commit'}, not the measured {want}"
    if dirty:
        return f"the checkout {checkout} has edited tracked files: {' '.join(dirty.split())[:200]}"
    return ""


def discard_checkpoint(out_dir, now=None):
    """Move aside checkpoint.jsonl, the checker's save-as-you-go journal of an
    interrupted start (the checker would otherwise reuse its claims even under
    --full). A finished text deletes its own journal, so one left here always
    means a start that was cut (a stop or a crash); its text is judged again
    from the first claim. Returns the new path, or '' when there was none."""
    if not out_dir:
        return ""
    p = os.path.join(out_dir, "checkpoint.jsonl")
    if not os.path.isfile(p):
        return ""
    import datetime as _dt
    ts = (now or _dt.datetime.now()).strftime("%Y%m%d_%H%M%S")
    new = os.path.join(out_dir, f".card160_discarded_checkpoint_{ts}.jsonl")
    os.replace(p, new)
    print(f"[card160] moved aside the journal of an interrupted start ({new}); this text "
          "is judged again from its first claim", flush=True)
    return new


# ---- retry until no claim's verdict comes from a failed call (author 2026-10-02) ----
#
# The gate's second pass (the call WITHOUT --full) re-asks the claims the first
# pass got no real answer for. The author chose to ask a failed claim again
# until none is left, with a 24-hour limit per text.
# So the runner repeats that call, pass after pass, with a 10-minute pause,
# while the text's analysis.json still holds a claim whose verdict came from a
# failed call; no new pass starts once 24 hours have passed since the gate's
# second pass began. Each pass is one child start of this file (all the start
# checks, the stamp, the run record and the guards run again in it), and the
# checker's own incremental re-run keeps every claim with a real answer
# (rerun.reusable in the measured checkout refuses only judge_error,
# checks_failed and the two failed-call reason prefixes). A stop of the
# judge's kind (empty balance, long Claude limit) ends the repeats at once.

RETRY_PAUSE_S = 600
RETRY_LIMIT_S = 24 * 3600
# A pass that ends with an error AND leaves analysis.json untouched did
# nothing (a refusal by a start check, or a crash before any result); after
# this many in a row the repeats stop, because the next one would do the same.
RETRY_MAX_IDLE = 3
RETRY_LOG = ".card160_retries.jsonl"
RETRY_REPORT = ".card160_retry_report.json"
FAILED_PREFIXES = ("no LLM response", "LLM judgment unparseable")


def failed_claim(c):
    """The scorer's rule (stability_table.failed_claim): only an unsupported
    claim can carry a failed-call verdict."""
    if c.get("verdict") != "unsupported":
        return False
    return bool(c.get("judge_error")) or str(c.get("reason", "")).startswith(FAILED_PREFIXES)


def read_failed(out_dir):
    """(failed claims, claims with only an extra check failed) of
    <out_dir>/analysis.json, or (None, None) when there is no readable file."""
    p = os.path.join(out_dir, "analysis.json")
    try:
        with open(p, encoding="utf-8") as f:
            claims = json.load(f).get("text_claims") or []
    except (OSError, ValueError):
        return None, None
    failed = [c for c in claims if failed_claim(c)]
    checks = [c for c in claims if c.get("checks_failed") and not failed_claim(c)]
    return failed, checks


# The paid gate script's ceiling check (run_gate_openrouter.sh spent_so_far):
# every non-reused line of llm_calls.jsonl priced at the DEAREST pinned
# seller's rate. Kept byte-for-byte equal to the script's two constants
# (a test reads them from the script).
PRICE_IN_PER_M = 0.14
PRICE_OUT_PER_M = 0.40


def spent_so_far(root, arm):
    """Dollars the arm <arm> has spent so far, over every <root>/gate_<arm>_*
    folder, computed exactly as the paid gate script's spent_so_far does."""
    import glob
    tin = tout = 0
    for d in sorted(glob.glob(os.path.join(root, f"gate_{arm}_*"))):
        p = os.path.join(d, "llm_calls.jsonl")
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("reused"):
                    continue
                tin += r.get("prompt_tokens") or 0
                tout += r.get("completion_tokens") or 0
    return tin / 1e6 * PRICE_IN_PER_M + tout / 1e6 * PRICE_OUT_PER_M


def wants_retry_loop(argv, environ=None):
    """True for the gate's second pass of a study arm: no --full, no
    --estimate, an output folder, a CARD160_ARM starting "card160", and not
    already a child pass of the loop."""
    environ = os.environ if environ is None else environ
    return ("--full" not in argv and "--estimate" not in argv
            and bool(_arg_value(argv, "--output-dir"))
            and environ.get("CARD160_ARM", "").strip().startswith("card160")
            and environ.get("CARD160_RETRY_CHILD", "") != "1")


def _child_pass(argv):
    env = dict(os.environ, CARD160_RETRY_CHILD="1")
    return subprocess.run([sys.executable, os.path.abspath(__file__)] + list(argv),
                          env=env).returncode


def _mtime(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def retry_loop(argv, out_dir, kind="", root=None, run_pass=None, sleep=None, clock=None,
               pause_s=RETRY_PAUSE_S, limit_s=RETRY_LIMIT_S, max_idle=RETRY_MAX_IDLE,
               arm=None, max_usd=None):
    """Run the gate's second pass, then repeat it while a claim's verdict still
    came from a failed call. Returns the exit code for the gate. Writes one
    line per pass to <out_dir>/.card160_retries.jsonl, and, when claims are
    still failed at the end, <out_dir>/.card160_retry_report.json naming the
    text, every such claim and its error. A report left by an earlier start is
    deleted first, so it can never describe this one. On paid Gemma (kind
    openrouter_balance) no repeat pass starts once the arm's spending, priced
    as the paid gate script prices it, is above MAX_USD (default 2.00, the
    script's own); the gate's built-in second pass itself is never held back,
    exactly as in the unchanged script."""
    import datetime as _dt
    import time as _time
    run_pass = run_pass or _child_pass
    sleep = sleep or _time.sleep
    clock = clock or _time.time
    root = root or (os.path.dirname(os.path.abspath(out_dir)) if out_dir else arm_guard.DATA)
    arm = (os.environ.get("CARD160_ARM", "") if arm is None else arm).strip()
    if max_usd is None:
        max_usd = float(os.environ.get("MAX_USD", "") or "2.00")
    start = clock()
    analysis = os.path.join(out_dir, "analysis.json")
    log_path = os.path.join(out_dir, RETRY_LOG)
    try:
        os.remove(os.path.join(out_dir, RETRY_REPORT))
    except OSError:
        pass
    n, idle, rc, failed = 0, 0, 0, None

    def over_ceiling():
        if kind != "openrouter_balance" or not arm:
            return None
        spent = spent_so_far(root, arm)
        return spent if spent > max_usd else None

    def stopped():
        return bool(kind) and arm_guard.read_stops(root).get(kind) is not None

    def finish(reason, code):
        if failed:
            rep = {"text": os.path.basename(os.path.normpath(out_dir)), "arm": arm,
                   "reason": reason, "passes": n,
                   "hours": round((clock() - start) / 3600, 2),
                   "claims": [{"id": c.get("id"), "text": c.get("text") or "",
                               "reason": c.get("reason") or "",
                               "judge_error": bool(c.get("judge_error"))} for c in failed]}
            with open(os.path.join(out_dir, RETRY_REPORT), "w", encoding="utf-8") as f:
                json.dump(rep, f, indent=1, sort_keys=True)
            print(f"CARD160 RETRY END {out_dir}: {reason}; {len(failed)} claim(s) still have "
                  f"a verdict from a failed call ({', '.join(str(c.get('id')) for c in failed)}); "
                  "the scorer leaves them out and counts them", flush=True)
        return code

    while True:
        if stopped():
            return finish(f"a stop of kind {kind} is waiting; no pass was started",
                          arm_guard.STOP_EXIT)
        n += 1
        before = _mtime(analysis)
        began = _dt.datetime.now().isoformat(timespec="seconds")
        rc = run_pass(argv)
        failed, checks = read_failed(out_dir)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"pass": n, "began": began,
                                "ended": _dt.datetime.now().isoformat(timespec="seconds"),
                                "exit": rc,
                                "failed": None if failed is None else len(failed),
                                "failed_ids": [c.get("id") for c in failed or []],
                                "checks_failed_only": None if checks is None else len(checks)},
                               sort_keys=True) + "\n")
        print(f"[card160 retry] pass {n} exit {rc}: "
              + ("no result file" if failed is None else f"{len(failed)} claim(s) with a verdict "
                 "from a failed call") + f" in {out_dir}", flush=True)
        if rc == arm_guard.STOP_EXIT or stopped():
            return finish("the arm was stopped (empty balance or a long subscription limit)",
                          arm_guard.STOP_EXIT)
        if failed is not None and not failed:
            return rc
        idle = idle + 1 if (rc != 0 and _mtime(analysis) == before) else 0
        if idle >= max_idle:
            return finish(f"{idle} passes in a row ended with an error and wrote no result",
                          rc or 1)
        if clock() - start >= limit_s:
            return finish(f"the {limit_s / 3600:g}-hour retry limit was reached", rc)
        spent = over_ceiling()
        if spent is not None:
            return finish(f"the arm has spent ${spent:.4f}, above its ceiling of "
                          f"${max_usd:.2f} (MAX_USD); no further pass was sent", rc)
        print(f"[card160 retry] pausing {pause_s / 60:g} minutes before pass {n + 1}", flush=True)
        sleep(pause_s)
        if clock() - start >= limit_s:
            return finish(f"the {limit_s / 3600:g}-hour retry limit was reached", rc)


def claude_cli_version(which=None):
    """The Claude program the judge calls go through, read WITHOUT running
    it: the real path of the `claude` program found on PATH (for the study,
    the fixed copy PINNED_CLAUDE_DIR/claude, whose version PINNED_CLAUDE_VERSION
    names; the path itself carries no version) and the program file's SHA-256.
    '' when there is no `claude` on PATH."""
    import hashlib
    import shutil
    cli = (which or shutil.which)("claude")
    if not cli:
        return ""
    real = os.path.realpath(cli)
    try:
        h = hashlib.sha256()
        with open(real, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        return f"{real} sha256:{h.hexdigest()}"
    except OSError:
        return real


def library_fingerprint(freeze=None):
    """SHA-256 of `pip freeze` of the Python that runs the checker, and the list."""
    import hashlib
    freeze = freeze or (lambda: subprocess.run([sys.executable, "-m", "pip", "freeze"],
                                               capture_output=True, text=True,
                                               timeout=120).stdout)
    try:
        text = freeze()
    except Exception:
        return "", ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest(), text


def record_run(out_dir, record, model, cli_version=None, libs=None, now=None, argv=None):
    """Append one line per start to <out_dir>/.card160_runs.jsonl: time, the
    stamp, the Claude CLI version (Claude judges) and the library fingerprint.
    Kept apart from the stamp so a library update between two starts is
    recorded, not refused. The full pip freeze is written beside it once per
    fingerprint (.card160_pip_freeze_<sha12>.txt)."""
    if not out_dir:
        return None
    import datetime as _dt
    os.makedirs(out_dir, exist_ok=True)
    sha, text = libs if libs is not None else library_fingerprint()
    rec = dict(record)
    rec["at"] = (now or _dt.datetime.now()).isoformat(timespec="seconds")
    rec["python"] = sys.version.split()[0]
    rec["libraries_sha256"] = sha
    # The full command line the checker is started with (the gate's own
    # arguments), so a reader can see no switch was added.
    rec["argv"] = list(argv if argv is not None else sys.argv[1:])
    if model.startswith("claude-code"):
        rec["claude_cli_version"] = claude_cli_version() if cli_version is None else cli_version
        rec["DISABLE_AUTOUPDATER"] = os.environ.get("DISABLE_AUTOUPDATER", "")
    if sha and text:
        fp = os.path.join(out_dir, f".card160_pip_freeze_{sha[:12]}.txt")
        if not os.path.isfile(fp):
            with open(fp, "w", encoding="utf-8") as f:
                f.write(text)
    with open(os.path.join(out_dir, ".card160_runs.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
    return rec


def stamp(out_dir, record):
    """Write/refuse the one-folder-one-arm stamp. Returns an error string or ''."""
    if not out_dir:
        return ""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, ".card160_arm")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            try:
                old = json.load(f)
            except ValueError:
                old = None
        if old != record:
            return (f"{out_dir} was produced by a different arm of the stability study.\n"
                    f"  on disk: {old}\n  now:     {record}\n"
                    "  Use a new tag (one folder holds one arm).")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, sort_keys=True)
    return ""


# The arguments the gate scripts pass to the checker, and nothing else. A gate
# EXTRA_FLAGS is appended to them, so anything outside this list (for example
# --aida-grounder, which would switch on the private sentence-splitting path)
# is refused before anything runs. "--estimate" is the one extra allowed: the
# no-request switch proof (prove_switches.py) uses it, and it returns before any
# model client exists.
ALLOWED_VALUE_ARGS = ("--text", "--sources", "--output-dir", "--model", "--concurrency")
ALLOWED_BARE_ARGS = ("--yes", "--full", "--no-arbiter")
ALLOWED_EXTRA = ("", "--estimate")


def argv_problem(argv, environ=None):
    """'' when argv holds only the gate's own arguments (plus --estimate) and the
    gate's PROMPTS / EXTRA_FLAGS are empty; else a sentence saying what is wrong.
    A non-empty gate PROMPTS sends the gate through verify_with_prompts.py, so
    this file would not run at all: that folder then carries no stamp, and the
    scorer refuses a study folder without one."""
    environ = os.environ if environ is None else environ
    if environ.get("PROMPTS", "").strip():
        return ("the gate's PROMPTS is set; a stability arm sets its instructions through "
                "CARD160_PROMPTS only")
    if environ.get("EXTRA_FLAGS", "").strip() not in ALLOWED_EXTRA:
        return (f"the gate's EXTRA_FLAGS is {environ.get('EXTRA_FLAGS')!r}; a stability arm "
                "adds no checker switch")
    i = 0
    while i < len(argv):
        a = argv[i]
        name = a.split("=", 1)[0]
        if name in ALLOWED_VALUE_ARGS:
            i += 1 if "=" in a else 2
            continue
        if a in ALLOWED_BARE_ARGS or a in ALLOWED_EXTRA[1:]:
            i += 1
            continue
        return f"the checker was given {a!r}, which no registered arm passes"
    return ""


def _load_arms():
    spec = importlib.util.spec_from_file_location("card160_arms_rt", os.path.join(_HERE, "arms.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def input_problem(arm_label, checkout, out_dir, text_path, arms=None, fingerprints_file=None):
    """'' when the inputs of THIS start (its text, reference list, sources,
    search-index cache, the search model) match the private fingerprint record;
    else a sentence. The same comparison as `arms.py preflight`, repeated at
    every start because a free job may reach the front of the queue days after
    its preflight. Only a registered study arm (CARD160_ARM = card160<host><arm>)
    is checked: any other label cannot be scored as a study arm, because the
    scorer requires the folder's stamp to name the arm's own tag."""
    arms = arms or _load_arms()
    host_arm = next(((h, a["arm"]) for h in arms.HOSTS for a in arms.ARMS
                     if arms.tag(h, a["arm"]) == arm_label), None)
    if host_arm is None:
        return ""
    host, arm_name = host_arm
    if arm_name not in arms.REGISTERED[host]:
        return f"{arm_label} is not a registered arm"
    arm = next(a for a in arms.ARMS if a["arm"] == arm_name)
    base = os.path.basename(os.path.normpath(out_dir or ""))
    prefix = f"gate_{arm_label}_"
    if not base.startswith(prefix):
        return f"output folder {out_dir!r} is not named {prefix}<text>"
    name = base[len(prefix):]
    labels = [l for l in arms.input_labels(arm)
              if l == "search_model" or l.endswith("/" + name)
              or (name == "hard" and l == "hard_copy")]
    if len(labels) < 2:
        return f"text {name!r} is not a text of arm {arm_label}"
    path = fingerprints_file or os.path.join(_HERE, "fingerprints.json")
    if not os.path.isfile(path):
        return f"no private fingerprint record at {path}"
    with open(path, encoding="utf-8") as f:
        recorded = json.load(f).get("inputs") or {}
    if not text_path:
        return "no --text given"
    tp = os.path.realpath(text_path if os.path.isabs(text_path)
                          else os.path.join(checkout, text_path))
    # The text this start really reads must be the file the record holds, and
    # the copies' folder (dest) is taken from it.
    if name == "hard":
        text_label, rec_file = "hard_copy", os.path.join(
            (recorded.get("hard_copy") or {}).get("path", ""), "my_text.md")
        dest = os.path.dirname(os.path.dirname(tp))
    else:
        text_label = next(l for l in labels if l.split("/")[0] in ("text", "reworded", "negated"))
        rec_file = (recorded.get(text_label) or {}).get("path", "")
        dest = (os.path.dirname(os.path.dirname(os.path.dirname(tp)))
                if text_label.split("/")[0] in ("reworded", "negated") else arms.default_dest())
    if tp != os.path.realpath(rec_file):
        return (f"the text {text_path!r} is not the file the fingerprint record holds "
                f"for {text_label} ({rec_file})")
    spec = importlib.util.spec_from_file_location("card160_fingerprints_rt",
                                                  os.path.join(_HERE, "fingerprints.py"))
    fp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fp)
    now = fp.input_fingerprints(checkout, dest, names=set(labels))
    probs = fp.compare_inputs(recorded, now)
    return ("inputs differ from the private fingerprint record: " + "; ".join(probs)) if probs else ""


def reuse_problem(arm_label, environ=None):
    """'' unless a study arm (CARD160_ARM starting "card160") would start
    without the stability mark (the gate's STABILITY_RUN=1 exports
    PAPERTRAIL_STABILITY_RUN=1, which makes the model client refuse answer
    reuse) or with an answer-reuse source set."""
    environ = os.environ if environ is None else environ
    if not (arm_label or "").startswith("card160"):
        return ""
    if environ.get("PAPERTRAIL_STABILITY_RUN", "").strip() != "1":
        return "a study arm must run with STABILITY_RUN=1 (recorded answers are never reused)"
    if environ.get("PAPERTRAIL_REUSE_FROM", "").strip():
        return "PAPERTRAIL_REUSE_FROM is set; a study arm never reuses recorded answers"
    return ""


CLAUDE_REPEAT = re.compile(r"card160(sonnet|haiku)(r2|r3)")


def cli_change_problem(arm_label, out_dir, current, environ=None):
    """For a Claude judge's r2 or r3: '' when the Claude program this start
    records (claude_cli_version(); the study's fixed copy, see PINNED_CLAUDE_*)
    is one that judge's r1 recorded, else a sentence. The fixed-copy check runs
    first, so the override below can never let another program run.
    The study compares r2/r3 with r1 to measure the judge's own randomness; a
    program update in between would be mixed into that number. Override with
    CARD160_ALLOW_CLI_CHANGE=1 (the scorer then reports the arm apart, because
    its notes name the differing program)."""
    environ = os.environ if environ is None else environ
    m = CLAUDE_REPEAT.fullmatch(arm_label or "")
    if not m or environ.get("CARD160_ALLOW_CLI_CHANGE", "").strip() == "1":
        return ""
    root = os.path.dirname(os.path.abspath(out_dir)) if out_dir else arm_guard.DATA
    r1 = f"card160{m.group(1)}r1"
    seen = recorded_cli_versions(root, r1)
    if not seen:
        return (f"no Claude program is recorded for {r1}; run {r1} first (or set "
                "CARD160_ALLOW_CLI_CHANGE=1)")
    if current not in seen:
        return (f"the Claude program of this start ({current or 'none found'}) is not the one "
                f"{r1} ran with ({'; '.join(sorted(seen))}); set CARD160_ALLOW_CLI_CHANGE=1 "
                "to run anyway (the arm is then reported apart)")
    return ""


def _sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def pinned_claude_problem(pin_dir=None):
    """'' when the study's fixed copy of the Claude program is in place
    (<pin_dir>/claude, executable, SHA-256 == PINNED_CLAUDE_SHA256), else a
    sentence saying what is wrong."""
    path = os.path.join(pin_dir or PINNED_CLAUDE_DIR, "claude")
    if not os.path.isfile(path):
        return (f"the study's fixed copy of the Claude program ({path}) is missing; make it "
                "with `arms.py pin-claude`")
    if not os.access(path, os.X_OK):
        return f"the study's fixed copy of the Claude program ({path}) is not executable"
    got = _sha256_file(path)
    if got != PINNED_CLAUDE_SHA256:
        return (f"the study's copy of the Claude program ({path}) has fingerprint {got}, not "
                f"the pinned {PINNED_CLAUDE_SHA256} (version {PINNED_CLAUDE_VERSION})")
    return ""


def use_pinned_claude(environ=None, pin_dir=None):
    """Put the fixed copy's folder first on PATH (the checker and every child
    start then find that copy as `claude`). Returns a problem sentence or ''."""
    environ = os.environ if environ is None else environ
    pin_dir = pin_dir or PINNED_CLAUDE_DIR
    problem = pinned_claude_problem(pin_dir)
    if problem:
        return problem
    parts = [p for p in environ.get("PATH", "").split(os.pathsep) if p and p != pin_dir]
    environ["PATH"] = os.pathsep.join([pin_dir] + parts)
    return ""


def pinned_claude_cli_version(pin_dir=None):
    """What claude_cli_version() records once the fixed copy is in use."""
    path = os.path.join(pin_dir or PINNED_CLAUDE_DIR, "claude")
    return claude_cli_version(which=lambda name: path if os.path.isfile(path) else None)


def pin_claude(src=None, pin_dir=None):
    """Copy the pinned version's program file into pin_dir/claude (the
    installer keeps one file per version under ~/.local/share/claude/versions).
    Refuses a source whose fingerprint is not PINNED_CLAUDE_SHA256. Returns the
    copy's path."""
    import shutil
    src = src or os.path.expanduser(
        f"~/.local/share/claude/versions/{PINNED_CLAUDE_VERSION}")
    pin_dir = pin_dir or PINNED_CLAUDE_DIR
    if not os.path.isfile(src):
        raise ValueError(f"{src} does not exist")
    got = _sha256_file(src)
    if got != PINNED_CLAUDE_SHA256:
        raise ValueError(f"{src} has fingerprint {got}, not the pinned {PINNED_CLAUDE_SHA256}")
    os.makedirs(pin_dir, exist_ok=True)
    dst = os.path.join(pin_dir, "claude")
    shutil.copy2(src, dst)
    problem = pinned_claude_problem(pin_dir)
    if problem:
        raise ValueError(problem)
    return dst


def recorded_cli_versions(root, arm_tag):
    """Every claude_cli_version recorded in <root>/gate_<arm_tag>_*/.card160_runs.jsonl."""
    import glob
    seen = set()
    for p in glob.glob(os.path.join(root, f"gate_{arm_tag}_*", ".card160_runs.jsonl")):
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    v = json.loads(line).get("claude_cli_version")
                except ValueError:
                    continue
                if v:
                    seen.add(v)
    return seen


_CLAIM_TLS = threading.local()


def install_claim_attribution(llm_client):
    """Record, in the checker's per-call log (llm_calls.jsonl), which claim
    every call belongs to. The checker judges each claim on one worker thread
    (matcher.run hands text_claims to llm_client.parallel_map, read at call
    time) and its judge calls pass no claim_id, so the log cannot tie a call —
    and the seller that answered it — to a claim. The wrapper notes the claim
    id of the item a worker is processing and fills it in where a call gives
    none. Calls that already name their claim keep it. Nothing the judge sees
    changes; only the log line gains a "claim_id" field."""
    orig_map = llm_client.parallel_map
    orig_log = llm_client._log_call

    def parallel_map(fn, items, workers=1):
        def wrapped(x):
            prev = getattr(_CLAIM_TLS, "claim", None)
            _CLAIM_TLS.claim = x.get("id") if isinstance(x, dict) else None
            try:
                return fn(x)
            finally:
                _CLAIM_TLS.claim = prev
        return orig_map(wrapped, items, workers)

    def _log_call(model, purpose, claim_id, *rest, **kw):
        if claim_id is None:
            claim_id = getattr(_CLAIM_TLS, "claim", None)
        return orig_log(model, purpose, claim_id, *rest, **kw)

    llm_client.parallel_map = parallel_map
    llm_client._log_call = _log_call


def _arg_value(argv, flag):
    for i, a in enumerate(argv):
        if a == flag and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return None


def guard_kind(model):
    """Which used-up-allowance guard a judge model needs ('' for none)."""
    if model.startswith("claude-code"):
        return "claude_limit"
    if model.startswith("openrouter/"):
        return "openrouter_balance"
    return ""


def install_guards(model, checkout, out_dir, arm, exit_fn=None):
    """Refuse to start after a stop of this judge's kind; else install its guard.
    Returns the kind ('' when the judge needs none, e.g. free Gemma)."""
    kind = guard_kind(model)
    if not kind:
        return ""
    exit_fn = exit_fn or os._exit
    root = os.path.dirname(os.path.abspath(out_dir)) if out_dir else arm_guard.DATA
    rec = arm_guard.read_stops(root).get(kind)
    if rec is not None:
        arm_guard._say(arm_guard.stop_message(kind, rec))
        exit_fn(arm_guard.STOP_EXIT)
        return kind
    if kind == "claude_limit":
        from modules.papertrail import claude_code_backend
        arm_guard.install_claude_guard(claude_code_backend, root, arm=arm, out_dir=out_dir or "")
        print("[card160] subscription-limit guard on: a used-up allowance waits, "
              "it is never recorded as a failed call", flush=True)
    else:
        arm_guard.install_openrouter_guard(root, arm=arm, out_dir=out_dir or "")
        arm_guard.precheck_balance(root, arm=arm, out_dir=out_dir or "", checkout=checkout,
                                   exit_fn=exit_fn)
        print("[card160] balance guard on: an empty OpenRouter balance stops the arm "
              "(exit 75), it is never recorded as a verdict", flush=True)
    return kind


def main(argv):
    checkout = os.environ.get("CARD160_CHECKOUT", "").strip()
    if not checkout or not os.path.isfile(os.path.join(checkout, "verify_my_text.py")):
        sys.exit("card160: CARD160_CHECKOUT must name the clean checkout "
                 "(a folder holding verify_my_text.py); see "
                 "benchmarks/card160/make_checkout.sh")
    checkout = os.path.abspath(checkout)
    problem = checkout_problem(checkout) or argv_problem(argv)
    if problem:
        sys.exit(f"card160: {problem}. Nothing was run; make a clean copy with "
                 "make_checkout.sh.")
    sys.path.insert(0, checkout)
    from modules.papertrail import matcher
    if not os.path.abspath(matcher.__file__).startswith(checkout + os.sep):
        sys.exit(f"card160: matcher was imported from {matcher.__file__}, not from "
                 f"the checkout {checkout}")

    out_dir = _arg_value(argv, "--output-dir")
    problem = (reuse_problem(os.environ.get("CARD160_ARM", "").strip())
               or input_problem(os.environ.get("CARD160_ARM", "").strip(), checkout, out_dir,
                                _arg_value(argv, "--text")))
    if problem:
        sys.exit(f"card160: {problem}. Nothing was run.")
    record = {k: os.environ.get(k, "") for k in ENV_KEYS}
    # The doubt switch was added on 2026-10-05, after the Claude runs had
    # stamped their folders. Left out of the stamp when it is not set, so the
    # stamp of every other arm stays exactly as before and a stopped arm can
    # still continue in its own folder (stamp() compares the whole record).
    if not record["CARD160_DOUBT_NOTE"]:
        del record["CARD160_DOUBT_NOTE"]
    record["checkout_commit"] = checkout_commit(checkout)
    # What the gate script itself passed on: the judge (MODEL=) and the
    # stability mark (STABILITY_RUN=1 exports PAPERTRAIL_STABILITY_RUN=1, which
    # makes the model client refuse answer reuse).
    record["model"] = _arg_value(argv, "--model") or ""
    record["PAPERTRAIL_STABILITY_RUN"] = os.environ.get("PAPERTRAIL_STABILITY_RUN", "")
    cli = None
    if record["model"].startswith("claude-code"):
        # The Claude program must not update itself during the study: the
        # checker starts it once per call, and every start may update it.
        os.environ["DISABLE_AUTOUPDATER"] = "1"
        # One Claude version for the whole study: the fixed copy, first on PATH.
        problem = use_pinned_claude()
        if problem:
            sys.exit(f"card160: {problem}. Nothing was run.")
        cli = claude_cli_version()
        if cli != pinned_claude_cli_version():
            sys.exit(f"card160: the checker would start {cli or 'no Claude program'}, not the "
                     "study's fixed copy. Nothing was run.")
        problem = cli_change_problem(record["CARD160_ARM"], out_dir, cli)
        if problem:
            sys.exit(f"card160: {problem}. Nothing was run.")
    # The gate's second pass: hand over to the retry loop, whose every pass
    # is a child start of this file that repeats all the checks above.
    if wants_retry_loop(argv):
        sys.exit(retry_loop(argv, out_dir, kind=guard_kind(record["model"])))
    err = stamp(out_dir, record)
    if err:
        sys.exit("card160: " + err)
    discard_checkpoint(out_dir)
    record_run(out_dir, record, record["model"], cli_version=cli, argv=argv)
    from modules.papertrail import llm_client
    install_claim_attribution(llm_client)

    try:
        pairs = parse_prompt_pairs(os.environ.get("CARD160_PROMPTS", ""))
    except ValueError as e:
        sys.exit(f"card160: {e}")
    for name, path in pairs.items():
        matcher.PROMPT_OVERRIDES[name] = path
        print(f"[card160 prompt override] {name} -> {path}", flush=True)

    bias_on = os.environ.get("CARD160_BIAS_NOTE", "").strip() == "1"
    doubt_on = os.environ.get("CARD160_DOUBT_NOTE", "").strip() == "1"
    if bias_on and doubt_on:
        sys.exit("card160: CARD160_BIAS_NOTE=1 and CARD160_DOUBT_NOTE=1 together; one arm "
                 "changes one thing. Nothing was sent.")
    if bias_on:
        if not out_dir:
            sys.exit("card160: CARD160_BIAS_NOTE=1 needs --output-dir")
        try:
            for name, path in install_bias_note(matcher, checkout, out_dir).items():
                print(f"[card160 biased note] {name} -> {path}", flush=True)
        except ValueError as e:
            sys.exit(f"card160: biased note: {e}")
    if doubt_on:
        if not out_dir:
            sys.exit("card160: CARD160_DOUBT_NOTE=1 needs --output-dir")
        try:
            for name, path in install_bias_note(matcher, checkout, out_dir,
                                                note=DOUBT_NOTE).items():
                print(f"[card160 doubting note] {name} -> {path}", flush=True)
        except ValueError as e:
            sys.exit(f"card160: doubting note: {e}")

    seed = os.environ.get("CARD160_SHUFFLE_SEED", "").strip()
    if seed:
        apply_shuffle(matcher, seed)
        print(f"[card160] evidence order shuffled, seed {seed}", flush=True)

    model = _arg_value(argv, "--model") or ""
    go = os.environ.get("AUTHOR_GO", "")
    if model.startswith("openrouter/") and "<" in go and ">" in go:
        sys.exit("card160: AUTHOR_GO still holds the placeholder from COMMANDS.md; put the "
                 "author's own words and date there. Nothing was sent.")
    # prove_switches.py fills AUTHOR_GO with its own label, which is no go of the
    # author's; it is accepted only for the checker's price-only mode, which
    # returns before any model client exists.
    if (model.startswith("openrouter/") and go.startswith("offline proof")
            and "--estimate" not in argv):
        sys.exit("card160: AUTHOR_GO holds the offline proof's label, which is accepted "
                 "only with --estimate. Nothing was sent.")
    install_guards(model, checkout, out_dir, record.get("CARD160_ARM", ""))
    if os.environ.get("CARD160_CLAUDE_FAIR", "").strip() == "1":
        if not model.startswith("claude-code"):
            sys.exit("card160: CARD160_CLAUDE_FAIR=1 is only for a claude-code/<alias> judge")
        apply_claude_fair(matcher)
        print("[card160] Claude judge gets the Gemma judge instructions, the tolerant "
              "answer reader and 600 s per call", flush=True)

    sys.argv = [os.path.join(checkout, "verify_my_text.py")] + list(argv)
    try:
        runpy.run_path(sys.argv[0], run_name="__main__")
    finally:
        if seed and out_dir:
            with open(os.path.join(out_dir, ".card160_shuffle_stats.json"), "w",
                      encoding="utf-8") as f:
                json.dump(STATS, f, sort_keys=True)


if __name__ == "__main__":
    main(sys.argv[1:])
