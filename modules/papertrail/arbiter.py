"""Light-touch arbiter — a strong-but-cheap model re-reads ONLY flagged claims.

Design + evidence: docs/ARBITER_PLAN.md, docs/GEMINI_FAILURE_BREAKDOWN_2026-07-12.md
(the trigger set below caught 15/15 owner/Fable-confirmed verdict-level failures),
docs/PRINTING_SIX_JUDGE_TABLE.md addendum (deepseek-v4-flash tracked the Fable
grader 5/6 with verifiable quotes).

A claim is escalated iff the run itself flags it: unsupported verdict,
supported with post-audit uncovered components or a partial_support flag, or a
displayed evidence sentence judged not-supporting (conflict candidate). The
arbiter reads the SHOWN evidence plus a large section of the cited sources with
the grader-style prompt (pt_arbiter_v1.txt) and returns an expected outcome +
missing components + verbatim proof quotes + an explicit conflict check.

House rule: NUDGE, NEVER A VETO — the arbiter never decides a verdict; results
render as chips/commentary. The one verdict-path interaction is rescue()
(2026-07-12, owner): the arbiter only FETCHES gate-verified evidence, and the
PRIMARY judge re-judges it — a unanimous positive flips a false unsupported
(method="arbiter_rescue"), exactly the component-rescue contract. Every proof quote passes a deterministic verbatim
gate (normalized, ligature-folded substring of the cited sources' text) before
it can be displayed — unverifiable quotes are dropped and counted
(quotes_dropped). Claims with an author ruling (verdict_feedback.json) are
skipped, like --second-opinion.
"""
import hashlib
import logging
import os
import re
from collections import Counter
from typing import Any, Dict, List, Optional

from . import matcher
from .llm_client import extract_json, parallel_map

logger = logging.getLogger("papertrail.arbiter")

DEFAULT_MODEL = "deepseek/deepseek-v4-flash"
PROMPT_FILE = "pt_arbiter_v1.txt"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEEPSEEK_KEY_PATH = os.path.join(PROJECT_ROOT, "config", "deepseek_api_key.txt")

SECTION_FULL_WORDS = 30_000   # <= this: pass the whole source text
SECTION_CAP_WORDS = 20_000    # long docs: best contiguous section of ~this size
MAX_PROOFS_SHOWN = 4
MIN_QUOTE_NORM_CHARS = 20     # shorter normalized quotes match spuriously — drop
_LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff",
              "ﬃ": "ffi", "ﬄ": "ffl"}


def resolve_key(model: str) -> Optional[str]:
    """DeepSeek models: fall back to the project key file when the env var is
    absent (LLMClient's own file fallback is gemini-only). Other providers:
    None → LLMClient resolves the provider env var itself. Never pass the
    caller's --api-key here — that is the PRIMARY judge's (Gemini) key."""
    if model.startswith("deepseek/") and not os.environ.get("DEEPSEEK_API_KEY") \
            and os.path.exists(DEEPSEEK_KEY_PATH):
        return DEEPSEEK_KEY_PATH
    return None


def trigger(c: Dict[str, Any]) -> Optional[str]:
    """The escalation reason for this claim, or None (= never call the arbiter).
    Clean supported-full rows stay un-escalated by design: the 0-false-support
    record says the judge needs no help there."""
    if c.get("verdict") not in ("supported", "unsupported"):
        return None
    if str(c.get("reason") or "").startswith("source_file_missing"):
        return None
    if c.get("owner_flag"):
        return None                   # the author already ruled on this claim
    if c.get("verdict") == "unsupported":
        return "unsupported"
    if c.get("partial_support"):
        return "partial_support"
    if (c.get("covering") or {}).get("uncovered"):
        return "uncovered_components"
    if any(e and e.get("sentence") and not e.get("supported")
           for e in (c.get("evidences") or [])):
        return "conflict_candidate"
    return None


# ---------- prompt assembly (grader-style; same shapes as loop_round) ----------

def _shown_block(c: Dict[str, Any]) -> str:
    """Everything the viewer displays as evidence for this claim."""
    lines, seen = [], set()
    for e in (c.get("evidences") or []):
        s = (e.get("sentence") or "").strip()
        if s and s not in seen:
            seen.add(s)
            tag = "judged supporting" if e.get("supported") else "judged NOT supporting"
            lines.append(f'- [{e.get("source_title", "?")}] ({tag}) "{s}"')
    cov = c.get("covering") or {}
    for ce in cov.get("covered", []):
        s = (ce.get("sentence") or "").strip()
        if s and s not in seen:
            seen.add(s)
            lines.append(f'- [{ce.get("source_title", "?")}] (shown as proof of: '
                         f'{ce.get("component", "?")}) "{s}"')
    for sp in (cov.get("spans") or []):
        if sp.get("text"):
            lines.append(f'- Displayed reading view from [{sp.get("source_title", "?")}]: '
                         f'"{sp["text"]}"')
    return "\n".join(lines) or "(none shown)"


def _relevant_section(claim_text: str, sents: List[Dict[str, Any]]) -> str:
    """Full source text if short; else the best contiguous ~20k-word section."""
    texts = [s.get("text", "") for s in sents]
    total = sum(len(t.split()) for t in texts)
    if total <= SECTION_FULL_WORDS:
        return " ".join(texts)
    chunks = matcher._chunk_sents(sents)
    if not chunks:
        return " ".join(texts)[:SECTION_CAP_WORDS * 6]
    lex = matcher._lex_scores(claim_text, texts)
    best = max(range(len(chunks)), key=lambda i: max(lex[j] for j in chunks[i][1]))
    lo = hi = best
    words = len(chunks[best][0].split())
    while words < SECTION_CAP_WORDS and (lo > 0 or hi < len(chunks) - 1):
        if lo > 0:
            lo -= 1; words += len(chunks[lo][0].split())
        if words < SECTION_CAP_WORDS and hi < len(chunks) - 1:
            hi += 1; words += len(chunks[hi][0].split())
    return " ".join(ch[0] for ch in chunks[lo:hi + 1])


def _claim_pids(c: Dict[str, Any]) -> List[str]:
    pids, seen = [], set()
    for pid in (c.get("paper_ids") or []):
        if pid and pid not in seen:
            seen.add(pid); pids.append(pid)
    for e in (c.get("evidences") or []):
        pid = e.get("paper_id")
        if pid and pid not in seen:
            seen.add(pid); pids.append(pid)
    return pids


def _source_blocks(c: Dict[str, Any], sources: Dict[str, Any]) -> str:
    parts = []
    for pid in _claim_pids(c):
        src = sources.get(pid) or {}
        sents = src.get("sentences", []) or []
        title = src.get("title") or pid
        if not sents:
            parts.append(f'From "{title}": (source text unavailable)')
            continue
        parts.append(f'From "{title}":\n"{_relevant_section(c.get("text", ""), sents)}"')
    return "\n\n".join(parts) or "(no source text found)"


# ---------- the verbatim quote gate ----------

def _norm(s: str) -> str:
    for lig, plain in _LIGATURES.items():
        s = s.replace(lig, plain)
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def verify_quotes(quotes: List[str], sources_text_norm: str) -> (list, int):
    """Keep only quotes whose normalized prefix occurs verbatim in the cited
    sources' normalized text. Returns (kept, dropped_count). This gate is
    mandatory for EVERY arbiter model — the printing addendum's one
    hallucinated quote came from the STRONGEST grader."""
    kept, dropped = [], 0
    for q in quotes:
        if not isinstance(q, str) or not q.strip():
            continue
        key = _norm(q)[:60]
        if len(key) >= MIN_QUOTE_NORM_CHARS and key in sources_text_norm:
            kept.append(q.strip())
        else:
            dropped += 1
    return kept, dropped


# ---------- the pass ----------

def _load_prompt() -> str:
    with open(os.path.join(PROJECT_ROOT, "config", "prompts", PROMPT_FILE),
              "r", encoding="utf-8") as f:
        return f.read()


def run(claims: List[Dict[str, Any]], sources: Dict[str, Any], llm,
        workers: int = 4) -> Dict[str, Any]:
    """Annotate each triggered claim in place with

        c["arbiter"] = {model, prompt_sha, trigger, action, missing_subclaim,
                        rewrite_suggestion, proofs, quotes_dropped, conflict, why}

    and return a summary. Unparseable responses leave NO field (retried next
    run — honest gap beats a guessed outcome). Claims already carrying an
    arbiter result from the same model+prompt keep it (incremental reruns)."""
    tpl = _load_prompt()
    sha = hashlib.sha1((llm.model + "\x1f" + tpl).encode("utf-8")).hexdigest()[:12]

    todo, reused = [], 0
    for c in claims:
        if c.get("owner_flag"):
            c.pop("arbiter", None)    # the author's ruling replaces the chip
            continue
        reason = trigger(c)
        if not reason:
            continue
        prior = c.get("arbiter") or {}
        if prior.get("prompt_sha") == sha:
            reused += 1
            continue
        todo.append((c, reason))

    def check(item) -> None:
        c, reason = item
        prompt = (tpl.replace("{TRIGGER}", reason)
                  .replace("{CLAIM}", c.get("text", ""))
                  .replace("{SHOWN}", _shown_block(c))
                  .replace("{CONTEXT}", _source_blocks(c, sources)))
        raw = llm.call(prompt, temperature=0.0, max_output_tokens=3000)
        j = extract_json(raw)
        if not isinstance(j, dict) or j.get("action") not in (
                "supported", "add_citation_or_rewrite", "wrong_or_insufficient_evidence"):
            logger.warning(f"Arbiter response for {c.get('id')} unparseable — "
                           f"left unannotated (retried next run)")
            return
        src_norm = _norm(" ".join(
            s.get("text", "") for pid in _claim_pids(c)
            for s in (sources.get(pid) or {}).get("sentences", []) or []))
        proofs, dropped = verify_quotes(j.get("proof_sentences") or [], src_norm)
        conflict = j.get("conflict")
        if isinstance(conflict, dict) and conflict.get("sentence"):
            c_kept, c_drop = verify_quotes([conflict["sentence"]], src_norm)
            conflict = ({"sentence": c_kept[0], "why": conflict.get("why", "")}
                        if c_kept else None)
            dropped += c_drop
        else:
            conflict = None
        c["arbiter"] = {
            "model": llm.model,
            "prompt_sha": sha,
            "trigger": reason,
            "action": j["action"],
            "missing_subclaim": (j.get("missing_subclaim") or "").strip(),
            "rewrite_suggestion": (j.get("rewrite_suggestion") or "").strip(),
            "proofs": proofs[:MAX_PROOFS_SHOWN],
            "quotes_dropped": dropped,
            "conflict": conflict,
            "why": (j.get("why") or "").strip(),
        }

    parallel_map(check, todo, workers=workers)

    actions = Counter((c.get("arbiter") or {}).get("action")
                      for c, _ in todo if c.get("arbiter"))
    proof_ids = [c["id"] for c, _ in todo
                 if c.get("verdict") == "unsupported"
                 and (c.get("arbiter") or {}).get("action") == "wrong_or_insufficient_evidence"
                 and (c.get("arbiter") or {}).get("proofs")]
    conflict_ids = [c["id"] for c, _ in todo if (c.get("arbiter") or {}).get("conflict")]
    return {"checked": len(todo), "reused": reused, "actions": dict(actions),
            "proof_may_exist": proof_ids, "conflicts": conflict_ids}


def resolve_ambers(claims: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Amber resolution (owner ruling 2026-07-14): the display-layer mirror of
    rescue(). A supported claim flagged NOT PROVEN AS WRITTEN
    (proof_state == "partial") whose arbiter — reading the large source
    context — ruled action == "supported" AND returned at least one
    gate-verified verbatim proof quote gets the amber cleared:

        c["proof_state"] = "arbiter_resolved"
        c["covering"]["arbiter_resolution"] = {model, proofs, why}

    Both provable actions resolve: "supported" (the shown evidence already
    holds) and "wrong_or_insufficient_evidence" (the SHOWN evidence is wrong
    but the source proves the claim — the arbiter fetched the verified proof;
    live case t5/Eskelson, where the arbiter returned the exact missing
    sentences under this action). "add_citation_or_rewrite" = the source does
    NOT prove it → the amber holds (live case t1/Eisenstein: the containment
    framing genuinely isn't in the source).

    DISPLAY ONLY — the verdict field was already "supported" and never moves.
    Real gaps stay amber and now mean more: a second model with the whole
    source also failed to produce verified proof. No LLM calls — pure
    post-processing of run()'s annotations."""
    eligible, resolved, held = 0, [], []
    for c in claims:
        if c.get("verdict") != "supported" or c.get("proof_state") not in (
                "partial", "arbiter_resolved"):
            continue
        ab = c.get("arbiter") or {}
        if not ab.get("action"):
            continue
        eligible += 1
        if ab["action"] in ("supported", "wrong_or_insufficient_evidence") \
                and ab.get("proofs"):
            c["proof_state"] = "arbiter_resolved"
            cov = c.setdefault("covering", {})
            cov["arbiter_resolution"] = {"model": ab.get("model"),
                                         "proofs": ab["proofs"],
                                         "why": ab.get("why") or ""}
            resolved.append(c["id"])
        else:
            # A previous run's resolution must not outlive an arbiter that no
            # longer confirms it (e.g. edited claim text on an incremental run).
            if c.get("proof_state") == "arbiter_resolved":
                c["proof_state"] = "partial"
                (c.get("covering") or {}).pop("arbiter_resolution", None)
            held.append(c["id"])
    return {"eligible": eligible, "resolved": sorted(resolved),
            "held": sorted(held)}


# ---------------------------------------------------------------------------
# Arbiter rescue (owner ask 2026-07-12, "solve t21"): the verdict-path half of
# the proof-may-exist chip. The arbiter itself still NEVER decides a verdict —
# it only FETCHES evidence: for an unsupported claim where the arbiter found
# gate-verified proof quotes (action wrong_or_insufficient_evidence + proofs),
# the quotes' source windows are re-judged by the PRIMARY judge with the
# standard combined prompt. Only a UNANIMOUS positive flips the verdict
# (mirrors matcher._component_rescue — flipping unsupported->supported
# manufactures the worst FP class, so the bar is all-votes). The flip is
# recorded as method="arbiter_rescue"; the arbiter field stays on the claim
# with rescued=true so the card explains its own history.

RESCUE_WINDOW_SPAN = 2   # sentences of context either side of a located proof


def _locate_window(proof: str, src: Dict[str, Any], span: int = RESCUE_WINDOW_SPAN) -> Optional[str]:
    """The proof sentence's neighborhood in the source, or None when the quote
    can't be pinned to a sentence (fall back handled by the caller)."""
    sents = (src or {}).get("sentences") or []
    p = _norm(proof)
    for i, s in enumerate(sents):
        sn = _norm(s.get("text", ""))
        if len(sn) >= MIN_QUOTE_NORM_CHARS and (sn in p or sn[:60] in p or p[:60] in sn):
            lo, hi = max(0, i - span), min(len(sents), i + span + 1)
            return " ".join(x.get("text", "") for x in sents[lo:hi])
    return None


def rescue(claims: List[Dict[str, Any]], sources: Dict[str, Dict], llm,
           workers: int = 4) -> Dict[str, Any]:
    """Attempt the primary-judge rescue on every proof-may-exist claim.
    `llm` is the PRIMARY judge client (the one that owns verdicts), never the
    arbiter's. Mutates flipped claims in place; returns
    {"attempted", "flipped": [ids], "held": [ids]}."""
    combined_prompt = matcher._load_prompt("pt_combined_judgment_prompt.txt")
    todo = [c for c in claims
            if c.get("verdict") == "unsupported"
            and not c.get("owner_flag")
            and not str(c.get("reason") or "").startswith("source_file_missing")
            and (c.get("arbiter") or {}).get("action") == "wrong_or_insufficient_evidence"
            and (c.get("arbiter") or {}).get("proofs")
            and (c.get("arbiter") or {}).get("rescued") is None]
    flipped, held = [], []

    def attempt(c: Dict[str, Any]) -> None:
        arb = c["arbiter"]
        # Subject-entity guard (matcher._subject_tokens): a source that never
        # names the claim's subject was barred from proving the claim on the
        # fulltext path — the rescue must not re-buy the same positive from
        # verbatim-but-subjectless quotes (waleedmajid's team-score windows
        # pass the quote gate and could win a ±2-sentence window judgment).
        guarded = set((c.get("subject_guard") or {}).get("missing_from") or [])
        windows, evs = [], []
        for proof in arb["proofs"]:
            for pid in _claim_pids(c):
                if pid in guarded:
                    continue
                src = sources.get(pid) or {}
                src_norm = _norm(" ".join(s.get("text", "")
                                          for s in src.get("sentences") or []))
                if _norm(proof)[:60] not in src_norm:
                    continue
                title = src.get("title") or pid
                w = _locate_window(proof, src) or proof
                if w not in [x[1] for x in windows]:
                    windows.append((title, w))
                evs.append({"paper_id": pid, "source_title": title,
                            "sentence": proof, "supported": True,
                            "via": "arbiter_rescue"})
                break
        if not windows:
            arb["rescued"] = False
            held.append(c["id"])
            return
        ok, reason, votes = matcher._combined_judge(c["text"], windows, llm,
                                                    combined_prompt,
                                                    early_break=False)
        if ok and votes.endswith("-0"):
            c["verdict"] = "supported"
            c["method"] = "arbiter_rescue"
            c["reason"] = (f"{reason} (evidence located by the arbiter, verified "
                           f"verbatim against the source, re-judged by the "
                           f"primary judge {votes})")
            covered = {e["paper_id"] for e in evs}
            c["evidences"] = evs + [e for e in (c.get("evidences") or [])
                                    if e.get("paper_id") not in covered]
            c.pop("citation_scope", None)   # tag was bought for the old verdict
            arb["rescued"] = True
            flipped.append(c["id"])
        else:
            arb["rescued"] = False
            held.append(c["id"])

    parallel_map(attempt, todo, workers=workers)
    return {"attempted": len(todo), "flipped": sorted(flipped),
            "held": sorted(held)}
