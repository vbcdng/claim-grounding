"""Split the `own` verdict: what KIND of uncited text is this?

`own` currently holds everything without a [[key]] marker — headings and
transitions, the author's genuine argument, AND factual assertions that quietly
escaped citation. The last group is a trust hole: an uncited wrong fact renders
indigo ("your own claim, nothing checked") and never red. One tiny LLM call per
own claim tags each as

    structural — headings/transitions/signposting; asserts nothing
    opinion    — the author's argument/judgment; legitimately uncited
    fact       — a checkable assertion a reader would expect a source for

Stored as c["own_kind"] = {kind, reason, model}. `fact` renders as an amber
"citation needed?" chip + a Citation needed filter in the viewer — a prompt to
the author, never a verdict (nothing was checked against any source).
Incremental runs reuse the tag with the rest of the claim; same-model tags are
never re-bought. Owner approved 2026-07-04 (IDEAS.md "Split 'own' claims").
"""
import hashlib
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from . import matcher
from .llm_client import extract_json, parallel_map

logger = logging.getLogger("papertrail.own_claims")

KINDS = ("structural", "opinion", "fact")

PROMPT_FILE = "pt_own_claim_class_prompt.txt"


def _prompt_sha(prompt: str) -> str:
    return hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:8]


def _parse_kind(raw: str) -> Tuple[Optional[str], str]:
    """(kind | None, reason). None = unparseable — the claim stays untagged
    (and gets retried on the next run) rather than guessing a class."""
    if not raw:
        return None, "no LLM response"
    obj = extract_json(raw)
    if isinstance(obj, dict) and str(obj.get("kind", "")).lower() in KINDS:
        return str(obj["kind"]).lower(), str(obj.get("reason", ""))
    m = re.search(r'"kind"\s*:\s*"(structural|opinion|fact)"', raw, re.IGNORECASE)
    if m:
        rmatch = re.search(r'"reason"\s*:\s*"([^"]*)', raw)
        return m.group(1).lower(), (rmatch.group(1).strip() if rmatch else "")
    return None, "unparseable classification"


def classify(claims: List[Dict[str, Any]], llm, workers: int = 4) -> Dict[str, Any]:
    """Tag every own-verdict claim in place with c["own_kind"]; returns
    {"checked", "reused", "unparsed", "counts": {kind: n}, "fact_ids": [...]}.
    Claims already tagged by the SAME model keep their tag (incremental runs
    carry the whole claim dict over, tag included)."""
    prompt = matcher._load_prompt(PROMPT_FILE)
    psha = _prompt_sha(prompt)
    todo, reused = [], 0
    for c in claims:
        if c.get("verdict") != "own":
            continue
        prior = c.get("own_kind") or {}
        # Reuse only tags bought with the same model AND the same prompt —
        # otherwise a prompt tune would silently keep every stale tag.
        if prior.get("model") == llm.model and prior.get("prompt_sha") == psha:
            reused += 1
            continue
        todo.append(c)

    unparsed = []

    def tag(c: Dict[str, Any]) -> None:
        raw = llm.call(prompt.replace("{CLAIM}", c["text"]),
                       temperature=0.0, max_output_tokens=1024)
        kind, reason = _parse_kind(raw)
        if kind is None:
            c.pop("own_kind", None)     # honest gap beats a guessed class
            unparsed.append(c["id"])
            return
        c["own_kind"] = {"kind": kind, "reason": reason, "model": llm.model,
                         "prompt_sha": psha}

    parallel_map(lambda c: tag(c), todo, workers=workers)
    if unparsed:
        logger.warning(f"own-claim classification unparseable for {', '.join(unparsed)} "
                       f"— left untagged (retried next run)")

    counts = {k: 0 for k in KINDS}
    fact_ids = []
    for c in claims:
        kind = (c.get("own_kind") or {}).get("kind")
        if kind in counts:
            counts[kind] += 1
            if kind == "fact":
                fact_ids.append(c["id"])
    return {"checked": len(todo), "reused": reused, "unparsed": len(unparsed),
            "counts": counts, "fact_ids": fact_ids}
