"""Second-opinion pass — a DIFFERENT model re-judges every verdict, as a flag.

Why: the judge bench shows the models are complementary, not ordered —
flash-lite has zero false positives but stable TOO_STRICT misses (t22/t27/t68
family), while plain flash / DeepSeek judge exactly those correctly but round
up compound claims (t37/t30 family). A second model reading the SAME evidence
catches both directions: a supported claim the second judge rejects is a
false-positive risk (t37 would have been flagged in every run it slipped
through); an unsupported claim the second judge accepts is a strictness miss.

The flag NEVER changes a verdict — it renders as a viewer chip + drops the
confidence chip to "low", so the human looks. Both models read through the same
API key (Gemini); cost is ~1 small call per judged claim.

Also consumes `<run dir>/verdict_feedback.json` (written by /apply-review for
"verdict wrong" marks): a claim the author already ruled on gets an
"author disputed" chip instead of a second-opinion call — the owner's verdict
outranks any model's.
"""
import json
import logging
import os
from typing import Any, Dict, List, Optional

from . import matcher
from .llm_client import parallel_map

logger = logging.getLogger("papertrail.second_opinion")

DEFAULT_MODEL = "gemini/gemini-2.5-flash"

# A lone disagreement from a borderline-flippy model would spam the review with
# noise; a disagreement only stands if it survives a majority-of-3 (the first
# call + 2 confirmations). Agreements are accepted on the single first call, so
# the steady-state cost stays ~1 call per claim.
CONFIRM_VOTES = 2


def _norm_text(s: str) -> str:
    return " ".join((s or "").split()).lower()


def load_feedback(output_dir: str) -> List[Dict[str, Any]]:
    """Read verdict_feedback.json from the run folder ([] if absent/unreadable)."""
    path = os.path.join(output_dir, "verdict_feedback.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"Could not read {path}: {e}")
        return []


def annotate_feedback(claims: List[Dict[str, Any]], feedback: List[Dict[str, Any]]) -> int:
    """Attach `owner_flag` to claims the author has disputed. The file is the
    source of truth: stale flags (from reused previous-run claims) are cleared
    first, and an entry only applies while the claim TEXT is unchanged — once
    the sentence is rewritten, the old dispute no longer describes it."""
    by_id = {}
    for fb in feedback:
        cid = fb.get("claim_id")
        if cid:
            by_id[cid] = fb          # later entries win (newest ruling)
    n = 0
    for c in claims:
        c.pop("owner_flag", None)
        fb = by_id.get(c.get("id"))
        if fb and _norm_text(fb.get("text", "")) == _norm_text(c.get("text", "")):
            c["owner_flag"] = {"author_says": fb.get("author_says", "wrong"),
                               "note": fb.get("note", ""),
                               "timestamp": fb.get("timestamp", "")}
            n += 1
    return n


def checkable(c: Dict[str, Any]) -> bool:
    """Is a second opinion meaningful for this claim? Only judged verdicts where
    the first judge actually read something — never `own`/`omitted`, never
    missing-file claims, never verdicts with no evidence sentence to re-read."""
    if c.get("verdict") not in ("supported", "unsupported"):
        return False
    if str(c.get("reason", "")).startswith("source_file_missing"):
        return False
    if c.get("owner_flag"):
        return False                 # the author already ruled on this one
    return any(e.get("sentence") for e in (c.get("evidences") or []) if e)


def _passages(c: Dict[str, Any]) -> List[str]:
    """The same evidence the first judge saw, labeled by source (provenance
    matters for attribution claims), window preferred over the bare sentence."""
    out = []
    for e in (c.get("evidences") or []):
        if not e or not e.get("sentence"):
            continue
        body = e.get("window") or e["sentence"]
        title = e.get("source_title")
        out.append(f"From {title}: {body}" if title else body)
    return out


def _judge_once(claim_text: str, passages: List[str], llm,
                judgment_prompt: str, combined_prompt: str):
    prompt = combined_prompt if len(passages) > 1 else judgment_prompt
    raw = llm.call(prompt.replace("{CLAIM}", claim_text)
                         .replace("{PASSAGE}", "\n\n".join(passages)),
                   temperature=0.0, max_output_tokens=2048)
    return matcher._parse_support(raw)


def run(claims: List[Dict[str, Any]], llm, workers: int = 4) -> Dict[str, Any]:
    """Annotate each checkable claim in place with

        c["second_opinion"] = {model, verdict, agrees, reason, votes}

    and return a summary: {"checked", "fp_flags", "strict_flags", "reused"}.
    fp_flags = supported claims the second model rejects (false-positive risk);
    strict_flags = unsupported claims it accepts (judge too strict?). Claims
    that already carry a second opinion from the SAME model keep it (incremental
    re-runs reuse the whole previous claim dict, opinion included)."""
    judgment_prompt = matcher._load_judgment_prompt()
    combined_prompt = matcher._load_prompt("pt_combined_judgment_prompt.txt")
    todo, reused = [], 0
    for c in claims:
        if c.get("owner_flag"):
            # The author already ruled on this claim (verdict_feedback.json).
            # Drop any opinion carried over from a previous run too — the
            # "author disputed" chip REPLACES the model's, never sits beside it.
            c.pop("second_opinion", None)
            continue
        prior = c.get("second_opinion") or {}
        if prior.get("model") == llm.model:
            reused += 1
            continue
        if checkable(c):
            todo.append(c)

    def check(c: Dict[str, Any]) -> None:
        first_says = c["verdict"] == "supported"
        passages = _passages(c)
        supported, reason = _judge_once(c["text"], passages, llm,
                                        judgment_prompt, combined_prompt)
        votes = None
        if supported != first_says:
            # Confirm before flagging: majority of 3 (this call + 2 more).
            tally = [(supported, reason)]
            for _ in range(CONFIRM_VOTES):
                tally.append(_judge_once(c["text"], passages, llm,
                                         judgment_prompt, combined_prompt))
            n_dis = sum(1 for s, _ in tally if s != first_says)
            supported = (not first_says) if n_dis * 2 > len(tally) else first_says
            reason = next(r for s, r in tally if s == supported)
            votes = f"{max(n_dis, len(tally) - n_dis)}-{min(n_dis, len(tally) - n_dis)}"
        c["second_opinion"] = {
            "model": llm.model,
            "verdict": "supported" if supported else "unsupported",
            "agrees": supported == first_says,
            "reason": reason,
            "votes": votes,
        }

    parallel_map(lambda c: check(c), todo, workers=workers)

    fp = [c["id"] for c in claims
          if c.get("verdict") == "supported"
          and (c.get("second_opinion") or {}).get("agrees") is False]
    strict = [c["id"] for c in claims
              if c.get("verdict") == "unsupported"
              and (c.get("second_opinion") or {}).get("agrees") is False]
    return {"checked": len(todo), "reused": reused,
            "fp_flags": fp, "strict_flags": strict}
