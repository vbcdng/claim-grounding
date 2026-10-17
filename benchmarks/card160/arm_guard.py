#!/usr/bin/env python3
"""Stability study (internal label CARD160): the two guards that keep a
used-up allowance from becoming verdicts.

Both are installed by arm_verify.py from OUTSIDE the measured code (the clean
checkout of 4899c40 is never edited). Without them, the measured checker turns
a refused request into a stored verdict with judge_error set — the gate's
second pass re-asks those, but if the allowance is still gone the second pass
fails the same way; on 2026-09-30 a Sonnet run caught by the five-hour limit
recorded 581 failed calls.

1. Claude subscription (claude-code/<model> judge). The CLI answers a used-up
   allowance with rc=1 and "You've hit your session limit · resets 12:20am
   (Europe/Prague)" (seen in a run log of 2026-09-30). The guard
   wraps the subprocess call inside the checker's claude_code_backend: on that
   message the call WAITS (until the printed reset time + 2 minutes, or 10
   minutes when no time is printed) and then asks again, so no failed call is
   recorded. Every wait is logged in <output-dir>/.card160_limit_waits.jsonl.
   When the spell (wall clock from its first sighting to the end of the planned
   wait; parallel worker threads are not added up) would pass
   CARD160_LIMIT_MAX_WAIT_H (default 6 h; a weekly limit usually does), it
   STOPS instead (point 3).

2. OpenRouter balance (openrouter/... judge). OpenRouter answers an empty
   balance with HTTP 402 ("Insufficient credits" / "requires more credits ...
   can only afford"). The guard wraps litellm.completion: on that error it
   STOPS (point 3) before the checker can store anything. Before each text it
   also reads the balance (no model call, fail-open) and refuses to start the
   text below CARD160_MIN_BALANCE_USD (default 0.30).

3. STOP = write <root>/.card160_STOP_<kind> (root = the folder holding the
   gate's output folders, i.e. data/), print one line starting
   "CARD160 BALANCE EMPTY" or "CARD160 SUBSCRIPTION LIMIT" on stdout and
   stderr, and end the checker process at once with exit code 75 — the text's
   analysis.json is never written, so nothing half-done can be scored. Every
   later arm_verify start (the gate's retry pass, the next texts, the next arms
   of the same kind) sees the file and exits 75 with the same line, without a
   single request. After a top-up or reset:
       ./venv/bin/python3 benchmarks/card160/arm_guard.py clear <kind>
   and re-run the arm's same command (COMMANDS.md, "If an arm stops").

CLI (no model call anywhere):
  status [--root DIR]            list stop files
  clear KIND [--root DIR]        remove one (openrouter_balance | claude_limit)
  balance [--min USD]            read the OpenRouter balance; exit 1 below --min
"""

import argparse
import datetime as _dt
import json
import os
import re
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DATA = os.path.realpath(os.path.join(REPO, "data"))

KINDS = ("openrouter_balance", "claude_limit")
STOP_EXIT = 75
LINE = {"openrouter_balance": "CARD160 BALANCE EMPTY",
        "claude_limit": "CARD160 SUBSCRIPTION LIMIT"}


# ---- stop files ---------------------------------------------------------------

def stop_path(root, kind):
    return os.path.join(root, f".card160_STOP_{kind}")


def read_stops(root):
    """{kind: record} for every stop file under root."""
    out = {}
    for kind in KINDS:
        p = stop_path(root, kind)
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    out[kind] = json.load(f)
            except ValueError:
                out[kind] = {"message": "(unreadable stop file)"}
    return out


def stop_message(kind, rec):
    return (f"{LINE[kind]}: {rec.get('message', '')} (stopped at {rec.get('at', '?')}, "
            f"arm {rec.get('arm', '?')}, folder {rec.get('out_dir', '?')}). Nothing more "
            f"is sent until the stop file is cleared: ./venv/bin/python3 "
            f"benchmarks/card160/arm_guard.py clear {kind}")


def _say(msg):
    for stream in (sys.stdout, sys.stderr):
        try:
            print(msg, file=stream, flush=True)
        except Exception:
            pass


def stop(root, kind, message, arm="", out_dir="", exit_fn=os._exit):
    """Write the stop file, print the line, end the process (exit 75). os._exit
    on purpose: the checker runs its calls in worker threads, and a normal exit
    would let it write a result file built from the refused calls."""
    rec = {"kind": kind, "message": message, "arm": arm, "out_dir": out_dir,
           "at": _dt.datetime.now().isoformat(timespec="seconds")}
    try:
        os.makedirs(root, exist_ok=True)
        with open(stop_path(root, kind), "w", encoding="utf-8") as f:
            json.dump(rec, f, sort_keys=True)
    except OSError:
        pass
    _say(stop_message(kind, rec))
    exit_fn(STOP_EXIT)


# ---- recognising the two messages ---------------------------------------------

_BALANCE_HINTS = ("insufficient credits", "insufficient credit", "requires more credits",
                  "can only afford", "insufficient balance", "payment required",
                  "add more credits")


def is_balance_error(text):
    t = (text or "").lower()
    if any(h in t for h in _BALANCE_HINTS):
        return True
    return bool(re.search(r"\b402\b", t)) and ("credit" in t or "balance" in t or "afford" in t)


_LIMIT_RE = re.compile(r"(hit your (session |weekly |usage |opus |sonnet )?limit"
                       r"|usage limit reached|session limit reached|weekly limit reached"
                       r"|5-hour limit reached)", re.I)


def limit_kind(text):
    """'weekly' / 'session' for a used-up subscription message, else None."""
    t = text or ""
    if not _LIMIT_RE.search(t):
        return None
    return "weekly" if "weekly" in t.lower() else "session"


_RESET_RE = re.compile(r"resets\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]m)?"
                       r"(?:\s*\(([^)]+)\))?", re.I)


def parse_reset(text, now=None):
    """The next datetime the message's 'resets 12:20am (Europe/Prague)' names,
    as a naive local datetime, or None. A weekday or date in the message is not
    read (a weekly reset then comes out as None, and the guard waits 10 minutes
    at a time until the 6-hour stop)."""
    m = _RESET_RE.search(text or "")
    if not m:
        return None
    if re.search(r"resets\s+(?:at\s+)?(mon|tue|wed|thu|fri|sat|sun|jan|feb|mar|apr|may|jun|"
                 r"jul|aug|sep|oct|nov|dec)", text or "", re.I):
        return None
    hour, minute, ampm, zone = int(m.group(1)), int(m.group(2) or 0), m.group(3), m.group(4)
    if ampm:
        hour = hour % 12 + (12 if ampm.lower() == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    tz = None
    if zone:
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(zone.strip())
        except Exception:
            tz = None
    now = now or _dt.datetime.now()              # naive = this computer's local time
    if tz:
        now = now.astimezone(tz)                 # the clock the message speaks in
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += _dt.timedelta(days=1)
    if tz:
        target = target.astimezone().replace(tzinfo=None)
    return target


# ---- guard 1: the Claude subscription -------------------------------------------

class LimitAwareSubprocess:
    """Stands in for the `subprocess` module inside claude_code_backend: run()
    waits out a used-up subscription and asks again; everything else
    (TimeoutExpired, PIPE, ...) is the real module's."""

    def __init__(self, real, root, arm="", out_dir="", max_wait_s=6 * 3600,
                 sleep=time.sleep, now=_dt.datetime.now, exit_fn=os._exit):
        self._real, self._root, self._arm, self._out = real, root, arm, out_dir
        self._max_wait_s, self._sleep, self._now, self._exit = max_wait_s, sleep, now, exit_fn
        self._lock = threading.Lock()
        # Wall-clock start of the current used-up spell. Several worker
        # threads wait side by side, so their waits are NOT added up: the spell
        # is measured from its first sighting to the planned end of this wait.
        self._spell_start = None
        self.waits = 0

    def __getattr__(self, name):
        return getattr(self._real, name)

    def _log_wait(self, rec):
        if not self._out:
            return
        try:
            os.makedirs(self._out, exist_ok=True)
            with open(os.path.join(self._out, ".card160_limit_waits.jsonl"), "a",
                      encoding="utf-8") as f:
                f.write(json.dumps(rec, sort_keys=True) + "\n")
        except OSError:
            pass

    def run(self, *args, **kwargs):
        while True:
            out = self._real.run(*args, **kwargs)
            text = f"{getattr(out, 'stdout', '') or ''}\n{getattr(out, 'stderr', '') or ''}"
            # A real judge answer is JSON and never says this; still, read a
            # successful call only when it is short, so a long answer quoting
            # such words can never be mistaken for the limit.
            kind = limit_kind(text) if (out.returncode != 0 or len(text) < 400) else None
            if not kind:
                self._spell_start = None        # the allowance is back
                return out
            now = self._now()
            reset = parse_reset(text, now)
            wait_s = ((reset - now).total_seconds() + 120) if reset else 600
            msg_line = " ".join(text.split())[:200]
            with self._lock:
                if self._spell_start is None:
                    self._spell_start = now
                spell_s = (now - self._spell_start).total_seconds() + wait_s
                if spell_s > self._max_wait_s:
                    stop(self._root, "claude_limit",
                         f"the Claude subscription is used up ({kind}): '{msg_line}'; "
                         f"waiting until {(now + _dt.timedelta(seconds=wait_s)).strftime('%Y-%m-%d %H:%M')} "
                         f"would pass the {self._max_wait_s / 3600:.1f} h limit",
                         arm=self._arm, out_dir=self._out, exit_fn=self._exit)
                    return out      # only reached when exit_fn is a test stand-in
                self.waits += 1
                rec = {"at": now.isoformat(timespec="seconds"), "kind": kind,
                       "wait_s": round(wait_s), "reset": reset.isoformat() if reset else None,
                       "message": msg_line}
                self._log_wait(rec)
                _say(f"[card160] Claude subscription used up ({kind}); this call waits "
                     f"{wait_s / 60:.0f} min and asks again — nothing is recorded as failed. "
                     f"'{msg_line}'")
            self._sleep(wait_s)


def install_claude_guard(claude_code_backend, root, arm="", out_dir="", max_wait_s=None):
    if max_wait_s is None:
        try:
            max_wait_s = float(os.environ.get("CARD160_LIMIT_MAX_WAIT_H", "6")) * 3600
        except ValueError:
            max_wait_s = 6 * 3600
    real = claude_code_backend.subprocess
    if isinstance(real, LimitAwareSubprocess):
        return real
    guard = LimitAwareSubprocess(real, root, arm=arm, out_dir=out_dir, max_wait_s=max_wait_s)
    claude_code_backend.subprocess = guard
    return guard


# ---- guard 2: the OpenRouter balance ---------------------------------------------

def wrap_completion(completion, root, arm="", out_dir="", exit_fn=os._exit):
    def guarded(*args, **kwargs):
        try:
            return completion(*args, **kwargs)
        except Exception as e:
            if is_balance_error(str(e)):
                stop(root, "openrouter_balance",
                     f"OpenRouter refused a request because the balance is used up: "
                     f"'{' '.join(str(e).split())[:200]}'. Top up, then clear and re-run.",
                     arm=arm, out_dir=out_dir, exit_fn=exit_fn)
            raise
    guarded._card160_guard = True
    return guarded


def install_openrouter_guard(root, arm="", out_dir=""):
    """Wrap litellm.completion before the checker builds its client (the client
    reads litellm.completion once, in its __init__)."""
    import litellm
    if not getattr(litellm.completion, "_card160_guard", False):
        litellm.completion = wrap_completion(litellm.completion, root, arm=arm, out_dir=out_dir)


def openrouter_key(checkout=None):
    k = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if k:
        return k
    for base in [b for b in (checkout, REPO) if b]:
        p = os.path.join(base, "config", "openrouter_api_key.txt")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                return f.read().strip()
    return ""


def read_balance(key, urlopen=None, timeout=20):
    """Remaining OpenRouter credit in USD, read from /api/v1/credits (no model
    call, costs nothing). Raises on any failure."""
    import urllib.request
    urlopen = urlopen or urllib.request.urlopen
    req = urllib.request.Request("https://openrouter.ai/api/v1/credits",
                                 headers={"Authorization": f"Bearer {key}"})
    with urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8")).get("data") or {}
    return float(data.get("total_credits") or 0) - float(data.get("total_usage") or 0)


def precheck_balance(root, arm="", out_dir="", checkout=None, reader=read_balance,
                     exit_fn=os._exit):
    """Refuse to start a text below CARD160_MIN_BALANCE_USD. Fail-open: a balance
    that cannot be read prints a note and the text starts (the 402 guard still
    stops it the moment OpenRouter refuses)."""
    if os.environ.get("CARD160_BALANCE_READ", "1").strip() == "0":
        return None         # offline proof runs (prove_switches.py) only
    try:
        floor = float(os.environ.get("CARD160_MIN_BALANCE_USD", "0.30"))
    except ValueError:
        floor = 0.30
    key = openrouter_key(checkout)
    if not key:
        _say("[card160] no OpenRouter key found to read the balance; starting anyway")
        return None
    try:
        left = reader(key)
    except Exception as e:
        _say(f"[card160] could not read the OpenRouter balance ({type(e).__name__}); starting anyway")
        return None
    _say(f"[card160] OpenRouter balance before this text: ${left:.2f}")
    if left < floor:
        stop(root, "openrouter_balance",
             f"the OpenRouter balance is ${left:.2f}, below the ${floor:.2f} needed to "
             f"start a text. Top up, then clear and re-run.",
             arm=arm, out_dir=out_dir, exit_fn=exit_fn)
    return left


# ---- CLI ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    s.add_argument("--root", default=DATA)
    c = sub.add_parser("clear")
    c.add_argument("kind", choices=KINDS)
    c.add_argument("--root", default=DATA)
    b = sub.add_parser("balance")
    b.add_argument("--min", type=float, default=None)
    args = ap.parse_args(argv)
    if args.cmd == "status":
        stops = read_stops(args.root)
        for kind, rec in stops.items():
            print(stop_message(kind, rec))
        if not stops:
            print("no stop file of the stability study: every arm may start")
        return 1 if stops else 0
    if args.cmd == "clear":
        p = stop_path(args.root, args.kind)
        if os.path.isfile(p):
            os.remove(p)
            print(f"cleared {p}")
        else:
            print(f"nothing to clear at {p}")
        return 0
    key = openrouter_key()
    if not key:
        print("no OpenRouter key (OPENROUTER_API_KEY or config/openrouter_api_key.txt)")
        return 2
    try:
        left = read_balance(key)
    except Exception as e:
        print(f"could not read the balance: {type(e).__name__}: {e}")
        return 2
    print(f"OpenRouter balance: ${left:.2f}")
    if args.min is not None and left < args.min:
        print(f"{LINE['openrouter_balance']}: ${left:.2f} is below ${args.min:.2f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
