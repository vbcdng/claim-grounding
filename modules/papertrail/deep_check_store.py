"""Version bookkeeping for deep_check.json (task #57, 2026-08-19).

deep_check.py is a TESTING aid: a stronger model re-reads every judged claim
WITH source context and writes a comment onto each claim card. Its results live
in <run>/deep_check.json, keyed by claim id ONLY — and claim ids are positional
(text_decomposer hands out t0, t1, t2 ... down the document), so after any edit
to the author's text the same id means a different sentence. Nothing recorded
which analysis a comment was about, so every consumer that joined the file to
today's analysis.json by id could silently pair a July comment with an August
verdict (watchdog report 2026-08-19; the report's own age triage was the manual
patch for exactly this).

Two halves of the fix live here:
  1. `wrap()` stamps a fresh result set: run-level fingerprint of the analysis
     it judged + per-claim text hash + the verdict at check time.
  2. `validate()` / `load_valid()` hand a consumer ONLY the comments whose claim
     text and verdict still match, plus a report of what was dropped and why.
Old (pre-#57) files carry no stamp at all — they are dropped wholesale under
reason "no_version_information", never guessed at.

`archive_previous()` is the re-run half: verify_my_text.py sets an existing
deep_check.json aside as deep_check_prev.json the same way it archives the
previous analysis.json, so a folder never shows fresh verdicts beside an old
checker file. One generation is kept; nothing is deleted.
"""

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

FORMAT = "deep_check/2"          # bumped when the stamp's meaning changes
FILENAME = "deep_check.json"
PREV_FILENAME = "deep_check_prev.json"

# Why a stored comment could not be used. Plain-language wording lives in
# `report_sentence()` — these keys are the machine-side buckets.
DROP_REASONS = (
    "no_version_information",   # pre-#57 file: no claim text / verdict recorded
    "claim_gone",               # the id no longer exists in this analysis
    "claim_text_changed",       # same id, different sentence (positional ids)
    "verdict_changed",          # same sentence, the tool now says something else
    "unusable_result",          # the entry itself is an error / not a dict
)


# Plain-language wording for a log line or a report page the author reads.
REASON_WORDS = {
    "no_version_information": "because they carry no record of which run they were "
                              "written about",
    "claim_gone": "because the claim they were written about is no longer in the text",
    "claim_text_changed": "because the wording of the claim changed",
    "verdict_changed": "because the tool's verdict on that claim changed",
    "unusable_result": "because the saved comment itself is unusable",
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def claim_sha(text: str) -> str:
    return hashlib.sha256(_norm(text).encode("utf-8")).hexdigest()[:16]


def analysis_fingerprint(analysis: Dict[str, Any]) -> str:
    """Identity of the verdict set a deep check was run against.

    Covers every claim's id, text and verdict plus the judging model — i.e.
    everything a comment is an opinion ABOUT. Deliberately ignores timestamps
    and cost bookkeeping, so re-running with identical verdicts (the incremental
    path, zero LLM calls) keeps the fingerprint stable.
    """
    rows = [[c.get("id"), _norm(c.get("text")), c.get("verdict")]
            for c in (analysis.get("text_claims") or [])]
    payload = json.dumps({"model": (analysis.get("metadata") or {}).get("model"),
                          "claims": rows}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def wrap(analysis: Dict[str, Any], model: str, results: Dict[str, Dict],
         checked_at: Optional[str] = None) -> Dict[str, Any]:
    """Build the on-disk payload: results + everything needed to spot staleness."""
    by_id = {c.get("id"): c for c in (analysis.get("text_claims") or [])}
    stamped = {}
    for cid, r in (results or {}).items():
        r = dict(r) if isinstance(r, dict) else {"error": "unusable result"}
        c = by_id.get(cid) or {}
        r["claim_sha"] = claim_sha(c.get("text", ""))
        r["verdict_checked"] = c.get("verdict")
        stamped[cid] = r
    return {
        "format": FORMAT,
        "model": model,
        "checked_at": checked_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "analysis_fingerprint": analysis_fingerprint(analysis),
        "analysis_timestamp": (analysis.get("metadata") or {}).get("timestamp"),
        "results": stamped,
    }


def validate(payload: Any, analysis: Dict[str, Any],
             allow_unstamped: bool = False) -> Tuple[Dict[str, Dict], Dict[str, Any]]:
    """Split a payload into (usable comments, report).

    A comment is usable only when its claim still exists, its claim text hashes
    to the same value, and the verdict it was judging is still the verdict on the
    card. Everything else is dropped with a reason; nothing is repaired or guessed.

    `allow_unstamped=True` is the ONE escape hatch, for consumers that must still
    read pre-#57 files (the failure watchdog mines July comments; task #56 exists
    to settle them by re-running). Those results come back flagged
    `unverified_version: True` and counted in `report["unverified"]`, so the
    consumer has to say so — they are never silently treated as current.
    """
    report = {"format": None, "model": None, "checked_at": None,
              "fingerprint_matches": None, "total": 0, "usable": 0,
              "dropped": 0, "unverified": 0, "reasons": {}}
    if not isinstance(payload, dict):
        return {}, report
    results = payload.get("results")
    if not isinstance(results, dict):
        results = {}
    report["format"] = payload.get("format")
    report["model"] = payload.get("model")
    report["checked_at"] = payload.get("checked_at")
    report["total"] = len(results)

    def drop(reason):
        report["reasons"][reason] = report["reasons"].get(reason, 0) + 1

    stamped = payload.get("format") == FORMAT
    report["fingerprint_matches"] = (
        payload.get("analysis_fingerprint") == analysis_fingerprint(analysis)
        if stamped else None)

    by_id = {c.get("id"): c for c in (analysis.get("text_claims") or [])}
    usable = {}
    for cid, r in results.items():
        if not isinstance(r, dict) or "error" in r or "supported" not in r:
            drop("unusable_result")
            continue
        if not stamped or "claim_sha" not in r or "verdict_checked" not in r:
            if allow_unstamped and cid in by_id:
                r = dict(r)
                r["unverified_version"] = True
                usable[cid] = r
                report["unverified"] += 1
            else:
                drop("no_version_information")
            continue
        c = by_id.get(cid)
        if c is None:
            drop("claim_gone")
            continue
        if r["claim_sha"] != claim_sha(c.get("text", "")):
            drop("claim_text_changed")
            continue
        if r["verdict_checked"] != c.get("verdict"):
            drop("verdict_changed")
            continue
        usable[cid] = r
    report["usable"] = len(usable)
    report["dropped"] = report["total"] - report["usable"]
    return usable, report


def load_valid(run_dir: str, analysis: Dict[str, Any],
               filename: str = FILENAME,
               allow_unstamped: bool = False) -> Tuple[Dict[str, Dict], Dict[str, Any]]:
    """Read <run_dir>/deep_check.json and return only comments still in date.

    A missing / unreadable file is not an error: no comments, a report saying so.
    """
    path = os.path.join(run_dir, filename)
    if not os.path.exists(path):
        empty = {"format": None, "model": None, "checked_at": None,
                 "fingerprint_matches": None, "total": 0, "usable": 0,
                 "dropped": 0, "unverified": 0, "reasons": {},
                 "present": False, "path": path}
        return {}, empty
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        payload = None
    usable, report = validate(payload, analysis, allow_unstamped=allow_unstamped)
    report["present"] = True
    report["path"] = path
    return usable, report


def report_sentence(report: Dict[str, Any]) -> str:
    """One plain-language sentence for a log line or a report page."""
    if not report.get("present", True) or report.get("total", 0) == 0:
        return "No deep-check comments were found for this run."
    total = report.get("total", 0)
    noun = "comment" if total == 1 else "comments"
    describe = "describes" if report.get("usable") == 1 else "describe"
    was = "was" if report.get("dropped") == 1 else "were"
    unver = (f" {report['unverified']} of them carry no record of the run they were "
             f"written about and are shown only because the caller asked for them."
             if report.get("unverified") else "")
    if report.get("dropped"):
        why = ", ".join(f"{n} {REASON_WORDS.get(k, 'for an unrecorded reason')}"
                        for k, n in sorted(report.get("reasons", {}).items()))
        return (f"{report['usable']} of {total} deep-check {noun} still "
                f"{describe} the current verdicts; {report['dropped']} {was} left "
                f"out ({why}).{unver}")
    return (f"All {report['usable']} deep-check {noun} still {describe} the current "
            f"verdicts (checked {report.get('checked_at') or 'at an unrecorded time'}"
            f" by {report.get('model') or 'an unrecorded model'}).{unver}")


def archive_previous(run_dir: str) -> Optional[str]:
    """Move an existing deep_check.json aside to deep_check_prev.json.

    Called on a re-run into an existing output dir, next to the analysis_prev.json
    copy. Returns the archive path, or None when there was nothing to archive.
    Overwrites an older archive (one generation kept, like analysis_prev.json).
    """
    src = os.path.join(run_dir, FILENAME)
    if not os.path.exists(src):
        return None
    dst = os.path.join(run_dir, PREV_FILENAME)
    os.replace(src, dst)
    return dst


def read_payload(run_dir: str, filename: str = FILENAME) -> Optional[Dict[str, Any]]:
    """Raw payload read (no validation) — for callers that only want the stamp."""
    path = os.path.join(run_dir, filename)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None
