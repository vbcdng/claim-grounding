"""Run-lifetime prompt snapshot + fingerprints (task #44, 2026-08-18).

Problem this solves: every prompt loader used to re-open its file in
config/prompts/ on EVERY LLM call — no caching, no snapshot at run start — so
an edit to a prompt file while a run was in flight silently swapped the
instructions for the run's remaining claims, and analysis.json recorded
nothing about which instruction text produced a verdict.

Three guarantees, all here:
  1. First read wins for the whole process: text is cached by RESOLVED path,
     so a mid-run edit to the same file never reaches later calls. Caching by
     path (not by name) keeps matcher.PROMPT_OVERRIDES dynamic — installing an
     override after a load still takes effect, because the override resolves
     to a different path and therefore a different cache slot.
  2. snapshot() eagerly reads every prompt file at run start, so even a
     prompt whose first call happens hours in (e.g. the arbiter's) is frozen
     at run start, and returns {name: 12-hex sha1} for metadata.
  3. load() records which names the process actually requested (used_names),
     so the incremental rerun can ignore edits to files no verdict depended on.

All pure stdlib, no LLM, thread-safe (matcher judges claims from a pool).
"""

import hashlib
import os
import threading
from typing import Dict, Optional, Set

PROMPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config", "prompts")

_lock = threading.Lock()
_text_by_path: Dict[str, str] = {}   # resolved path -> text (first read wins)
_fp_by_name: Dict[str, str] = {}     # prompt file name -> 12-hex sha1 of the text used
_overridden: Set[str] = set()        # names served from a PROMPT_OVERRIDES path
_used_names: Set[str] = set()        # names actually requested via load()


def fingerprint(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _read(name: str, overrides: Optional[Dict[str, str]]) -> str:
    override = (overrides or {}).get(name)
    path = override or os.path.join(PROMPTS_DIR, name)
    with _lock:
        text = _text_by_path.get(path)
    if text is None:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        with _lock:
            # setdefault: if a parallel thread read the file first, ITS copy
            # stays canonical — every caller sees one consistent text.
            text = _text_by_path.setdefault(path, text)
    with _lock:
        _fp_by_name[name] = fingerprint(text)
        if override:
            _overridden.add(name)
        else:
            _overridden.discard(name)
    return text


def load(name: str, overrides: Optional[Dict[str, str]] = None) -> str:
    """Read a prompt by file name (override-aware, cached for the process)."""
    text = _read(name, overrides)
    with _lock:
        _used_names.add(name)
    return text


def snapshot(overrides: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Eagerly read every *.txt in config/prompts/ (plus any override names)
    right now, freezing their text for the rest of the process, and return
    {name: 12-hex sha1}. Does NOT mark anything as used."""
    names = {n for n in os.listdir(PROMPTS_DIR) if n.endswith(".txt")}
    names.update(overrides or {})
    for name in sorted(names):
        try:
            _read(name, overrides)
        except OSError:
            pass  # unreadable stray file: not fingerprintable, not fatal
    with _lock:
        return dict(_fp_by_name)


def metadata_block() -> Dict[str, object]:
    """The metadata.prompts payload for analysis.json: every fingerprint the
    run saw, which names were served from an override, which names the run
    actually loaded, and the file count."""
    with _lock:
        return {
            "fingerprints": dict(_fp_by_name),
            "overridden": sorted(_overridden),
            "used": sorted(_used_names),
            "count": len(_fp_by_name),
        }


def reset() -> None:
    """Tests only: forget all cached text and bookkeeping."""
    with _lock:
        _text_by_path.clear()
        _fp_by_name.clear()
        _overridden.clear()
        _used_names.clear()
