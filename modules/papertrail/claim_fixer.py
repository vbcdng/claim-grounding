"""
--fix-claim implementation (ROADMAP item 3; owner-decided shape: static viewer +
copyable CLI command — the viewer shows the command, running it lands here).

Rewrites ONE claim so it is supported by what its cited sources actually say,
using the finished run's cached decomposition + embeddings — no full re-run.
Per fix: fresh gated evidence extraction for the claim (~6-12 small calls), one
rewrite call, then a majority-vote re-judgment of the rewritten claim against
the same passages. The verified suggestion is stored on the claim as
`fix_suggestion` and rendered by the viewer; the writer applies it by hand
(the tool never edits the article).
"""

import os
import logging
from typing import Dict, Any, Optional

from . import matcher, embeddings

logger = logging.getLogger(__name__)


def _passages_for(tc: Dict[str, Any], sources: Dict[str, Dict], llm,
                  emb_cache_dir: Optional[str]) -> list:
    """Fresh evidence extraction per cited source (same production path as the
    run's fallback stage). Returns ["From <title>: <sentences>", ...]."""
    extract_prompt = matcher._load_prompt("pt_extract_evidence_prompt.txt")
    combined_prompt = matcher._load_prompt("pt_combined_judgment_prompt.txt")
    passages = []
    for pid in tc.get("paper_ids", []):
        src = sources.get(pid)
        if src is None:
            continue
        sents = src.get("sentences", []) or []
        s_texts = [s.get("text", "") for s in sents]
        row = None
        if emb_cache_dir and s_texts:
            cache_file = os.path.join(emb_cache_dir, f"{pid}.sents.npz")
            try:
                from sentence_transformers import util
                s_vecs = embeddings.embed_cached(s_texts, cache_file)
                row = util.cos_sim(embeddings.embed([tc["text"]]), s_vecs)[0].tolist()
            except Exception as e:
                logger.warning(f"cosine row unavailable for {pid[:8]} ({e}); "
                               "extraction will read the whole source")
        e = matcher._extract_evidence(tc["text"], pid, src, llm,
                                      extract_prompt, combined_prompt, row=row)
        text = (e.get("window") or e.get("sentence") or "").strip()
        if text:
            title = src.get("title") or pid[:8]
            passages.append(f"From {title}: {text}")
    if not passages:
        # thin sources — fall back to the run's recorded closest sentences
        for e in tc.get("evidences", []) or []:
            if e.get("sentence"):
                passages.append(f"From {e.get('source_title') or ''}: {e['sentence']}")
    return passages


def fix_claim(analysis: Dict[str, Any], sources: Dict[str, Dict], llm,
              claim_id: str, emb_cache_dir: Optional[str] = None) -> Dict[str, Any]:
    """Attach a verified `fix_suggestion` to the claim with id `claim_id` in
    `analysis` (mutated in place) and return the suggestion dict."""
    tc = next((c for c in analysis.get("text_claims", []) if c.get("id") == claim_id), None)
    if tc is None:
        ids = [c.get("id") for c in analysis.get("text_claims", [])][:10]
        raise ValueError(f"no claim '{claim_id}' in the analysis (ids look like: {ids}...)")
    if not tc.get("paper_ids"):
        raise ValueError(f"claim {claim_id} cites no resolvable source — there is "
                         "nothing to ground a rewrite in (add a citation first)")
    if tc.get("verdict") == "supported":
        logger.info(f"{claim_id} is already supported — generating a suggestion anyway")

    passages = _passages_for(tc, sources, llm, emb_cache_dir)
    if not passages:
        raise ValueError(f"no usable source passages found for {claim_id} "
                         "(sources empty/unreadable?)")
    joined = "\n\n".join(passages)

    rewrite_prompt = matcher._load_prompt("pt_rewrite_claim_prompt.txt")
    obj = llm.call_json(rewrite_prompt.replace("{CLAIM}", tc["text"])
                        .replace("{PASSAGES}", joined),
                        temperature=0.2, max_output_tokens=2048)
    if not isinstance(obj, dict) or not (obj.get("rewritten") or "").strip():
        raise RuntimeError("the rewrite call returned no usable JSON — try again")
    rewritten = obj["rewritten"].strip()

    # Verify the rewrite the same way the pipeline judges claims (majority vote).
    judge_prompt = matcher._load_prompt("pt_combined_judgment_prompt.txt")
    supported, reason, _votes = matcher._vote_support(
        llm, judge_prompt.replace("{CLAIM}", rewritten).replace("{PASSAGE}", joined))

    tc["fix_suggestion"] = {
        "text": rewritten,
        "changes": (obj.get("changes") or "").strip(),
        "verified_supported": supported,
        "verify_reason": reason,
        "passages": passages,
    }
    return tc["fix_suggestion"]
