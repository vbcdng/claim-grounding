"""Citation-scope classification: is the citation claiming the WHOLE passage?

On imported real papers the largest unsupported class is not judge error but
citation scope (owner walkthrough 2026-07-12, foi t34/t41/t47/t106, regret
t31/t99): a passage describing the AUTHORS' OWN study (power analysis, design,
their conclusions) carries a methods/concept/related-work citation. The tool
correctly finds the cited source doesn't prove the passage — but the author
never asserted it would, so the red card answers a question nobody asked.

One tiny LLM call per UNSUPPORTED cited claim tags the citation's scope:

    full     — the passage asserts things the cited source must prove
               (the default; the prompt is biased here on any doubt)
    methods  — passage is the authors' own study; citation backs a method,
               instrument, or recommendation being followed
    concept  — citation backs a definition/term; the rest is the authors' own
    related  — a comparison/see-also pointer; substance is the authors' own

Stored as c["citation_scope"] = {scope, scoped_assertion, reason, model,
prompt_sha}. A scoped tag re-badges the card in the viewer (indigo
"SCOPED CITATION — AUTHORS' OWN TEXT" + the named scoped assertion) and feeds
a filter — DISPLAY ONLY, the verdict field is never touched (house rule:
nudge, never veto; the 3-paper gate reads verdicts). Claims judged supported,
missing-file claims, and author-ruled claims are never classified. Incremental
runs reuse same-model+same-prompt tags; unparseable responses leave no tag
(retried next run) — an honest gap beats a guessed class.
"""
import hashlib
import logging
from typing import Any, Dict, List, Optional, Tuple

from . import matcher
from .llm_client import extract_json, parallel_map

logger = logging.getLogger("papertrail.citation_scope")

SCOPES = ("full", "methods", "concept", "related")
SCOPED = ("methods", "concept", "related")

PROMPT_FILE = "pt_citation_scope_v1.txt"


def _prompt_sha(prompt: str) -> str:
    return hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:8]


def eligible(c: Dict[str, Any]) -> bool:
    """Only unsupported, actually-judged, cited, un-ruled claims — the
    misleading-red class. Supported cards aren't a trust problem and
    missing-file cards already say what's wrong."""
    if c.get("verdict") != "unsupported":
        return False
    if not c.get("markers"):
        return False
    if str(c.get("reason") or "").startswith("source_file_missing"):
        return False
    if c.get("owner_flag"):
        return False
    return True


def _parse(raw: str) -> Tuple[Optional[Dict[str, str]], str]:
    """(payload | None, error). None = unparseable — no tag, retried next run."""
    if not raw:
        return None, "no LLM response"
    obj = extract_json(raw)
    if isinstance(obj, dict) and str(obj.get("scope", "")).lower() in SCOPES:
        return {"scope": str(obj["scope"]).lower(),
                "scoped_assertion": str(obj.get("scoped_assertion") or "").strip(),
                "reason": str(obj.get("reason") or "").strip()}, ""
    return None, "unparseable scope classification"


def classify(claims: List[Dict[str, Any]], llm, workers: int = 4) -> Dict[str, Any]:
    """Tag eligible claims in place with c["citation_scope"]; returns
    {"checked", "reused", "unparsed", "counts": {scope: n}, "scoped_ids"}."""
    prompt = matcher._load_prompt(PROMPT_FILE)
    psha = _prompt_sha(prompt)
    todo, reused = [], 0
    for c in claims:
        if c.get("owner_flag"):
            c.pop("citation_scope", None)   # the author's ruling outranks a tag
            continue
        if not eligible(c):
            # A tag bought when the claim WAS unsupported is stale once a
            # re-run flips it (e.g. component rescue) — drop, don't display.
            c.pop("citation_scope", None)
            continue
        prior = c.get("citation_scope") or {}
        if prior.get("model") == llm.model and prior.get("prompt_sha") == psha:
            reused += 1
            continue
        todo.append(c)

    unparsed = []

    def tag(c: Dict[str, Any]) -> None:
        keys = ", ".join(c.get("markers") or [])
        raw = llm.call(prompt.replace("{CLAIM}", c["text"]).replace("{KEYS}", keys),
                       temperature=0.0, max_output_tokens=1024)
        payload, err = _parse(raw)
        if payload is None:
            c.pop("citation_scope", None)
            unparsed.append(c["id"])
            return
        payload.update({"model": llm.model, "prompt_sha": psha})
        c["citation_scope"] = payload

    parallel_map(lambda c: tag(c), todo, workers=workers)
    if unparsed:
        logger.warning(f"citation-scope classification unparseable for "
                       f"{', '.join(unparsed)} — left untagged (retried next run)")

    counts = {k: 0 for k in SCOPES}
    scoped_ids = []
    for c in claims:
        scope = (c.get("citation_scope") or {}).get("scope")
        if scope in counts:
            counts[scope] += 1
            if scope in SCOPED:
                scoped_ids.append(c["id"])
    return {"checked": len(todo), "reused": reused, "unparsed": len(unparsed),
            "counts": counts, "scoped_ids": scoped_ids}
