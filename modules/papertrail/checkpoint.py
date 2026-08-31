"""Save-as-you-go checkpoint for interrupted runs (2026-08-10).

A verification run keeps every judged verdict in memory and writes
analysis.json only once, at the very end — so a power cut used to cost the
whole run (the 2026-08-09 battery death lost 69 of 81 judged claims, ~24h
of the free backend's time). This module gives the run a journal: the
moment a claim finishes, its record is appended to
<output-dir>/checkpoint.jsonl and forced to disk (fsync), and a restarted
run with the SAME model, text and source files recovers those claims
through the normal incremental-reuse path instead of re-judging them. The
caller deletes the file right after analysis.json is written, so a finished
run leaves no journal behind and ordinary incremental re-runs are untouched.

Validity guard: the first line is a header
{model, text_sha1, source_hashes, source_reader}. If ANY of them differs in the
restarted run, the whole checkpoint is ignored — a recovered verdict must never
silently cross a model, text, source or PDF-reader change. `source_reader`
(task #71) exists because source files are compared by BYTES: the same PDF read
by a different library yields different text, so a journal written under the old
reader holds verdicts this run cannot reproduce. A journal with NO source_reader
field is treated as `rerun.PRE_TRACKING_READER`, not as unknown — it predates the
2026-08-31 swap, so it is known to have used PyPDF2 (author ruling, "option B")
— and is therefore discarded. A journal for a project with no PDF sources is
exempt, since no PDF was read. Recovery applies even under --full: --full means "don't trust the
FINISHED previous run", while the checkpoint is this same run, interrupted.
Delete checkpoint.jsonl by hand to forbid recovery.

Purely save/load — never touches how a claim is judged.
"""

import json
import logging
import os
import threading
from typing import Any, Dict, List

logger = logging.getLogger("papertrail")


def load(path: str, model: str, text_sha1: str,
         source_hashes: Dict[str, str],
         source_reader: str = "") -> List[Dict[str, Any]]:
    """Claim records journaled by an interrupted run of the same configuration.

    Returns [] when the file is absent, belongs to a different configuration,
    or is unreadable. A truncated final line (the write the power cut caught
    mid-flight) is dropped silently — everything before it is intact by
    construction (each record was fsynced)."""
    if not os.path.exists(path):
        return []
    claims: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            header = json.loads(f.readline())
            if (header.get("model") != model
                    or header.get("text_sha1") != text_sha1
                    or header.get("source_hashes") != source_hashes):
                logger.info("checkpoint.jsonl belongs to a different run "
                            "configuration (model/text/sources changed) — ignored")
                return []
            if source_reader:
                from . import rerun
                has_pdf = any(str(f).lower().endswith(".pdf") for f in source_hashes)
                prev_reader = header.get("source_reader")
                if rerun.source_reader_changed(prev_reader, source_reader, has_pdf):
                    prev_desc = (prev_reader if prev_reader else
                                 f"{rerun.PRE_TRACKING_READER} (not recorded, so it "
                                 f"predates the 2026-08-31 reader swap)")
                    logger.info(f"checkpoint.jsonl was written while PDFs were read by "
                                f"{prev_desc}; this run uses {source_reader} — the same "
                                f"PDF yields different text, so the journaled verdicts "
                                f"cannot be reproduced and are ignored")
                    return []
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    claims.append(json.loads(line))
                except json.JSONDecodeError:
                    break   # truncated tail — keep what's whole, drop the rest
    except Exception as e:
        logger.warning(f"Unreadable checkpoint.jsonl ({e}) — ignored")
        return []
    return claims


class Writer:
    """Appends one JSON line per finished claim, fsynced so it survives a
    power cut. Creating the writer truncates any old journal (its usable
    claims were already loaded and re-enter through the reuse path, so they
    get re-journaled instantly). A journal failure is logged and swallowed —
    the journal must never kill the run it protects."""

    def __init__(self, path: str, model: str, text_sha1: str,
                 source_hashes: Dict[str, str], source_reader: str = ""):
        self._lock = threading.Lock()
        self._f = None
        try:
            self._f = open(path, "w", encoding="utf-8")
            self._write({"model": model, "text_sha1": text_sha1,
                         "source_hashes": source_hashes,
                         "source_reader": source_reader})
        except Exception as e:
            logger.warning(f"Could not open checkpoint journal ({e}) — "
                           f"running without save-as-you-go protection")
            self._f = None

    def _write(self, obj: Dict[str, Any]) -> None:
        self._f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._f.flush()
        os.fsync(self._f.fileno())

    def __call__(self, claim: Dict[str, Any]) -> None:
        if self._f is None:
            return
        with self._lock:
            try:
                self._write(claim)
            except Exception as e:
                logger.warning(f"Checkpoint write failed ({e}) — continuing")
                self._f = None

    def close(self) -> None:
        if self._f is not None:
            try:
                self._f.close()
            except Exception:
                pass
            self._f = None
