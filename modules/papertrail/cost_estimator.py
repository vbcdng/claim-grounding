"""
Pre-run cost estimate for verify_my_text.py (ROADMAP item 4's estimator half).

Predicts LLM call counts, token volumes, and a $ range BEFORE any money is
spent — no LLM/API calls anywhere in this module. Also surfaces free pre-flight
warnings (missing source files, sources with no extractable text) since it has
to read everything anyway.

Pricing comes from the "Option B" markdown table in docs/MODEL_OPTIONS.md —
that file is the single source of truth (per ROADMAP item 4); numbers are not
duplicated here. A model missing from the table still gets call/token counts,
just no $ figure.

Estimation model (mirrors the real pipeline's call structure):
- decomposition: ceil(words/1200) calls per UNCACHED source
  (source_decomposer._CHUNK_WORD_TARGET); cached sources cost nothing.
- judgment: per cited (claim x source), ~2 short calls on average (up to TOPK=3;
  near-verbatim matches skip the LLM entirely).
- full-text fallback: triggered when cosine candidates fail judgment; sends the
  source's ENTIRE text. Assumed for FALLBACK_FRACTION of cited claim-source
  pairs — this dominates the upper bound.
The result is a rough range, not a quote — the point is avoiding a surprise
bill (ROADMAP: "precision isn't the point").
"""

import os
import re
import math
import logging
from typing import Dict, List, Optional

from . import source_decomposer

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODEL_OPTIONS_PATH = os.path.join(PROJECT_ROOT, "docs", "MODEL_OPTIONS.md")

# Constants calibrated against the paper1 run (2026-07-02, flash-lite): 56 sources /
# ~726k words / 44 cited claims -> 1,654 actual calls vs 1,003 estimated. The two
# misses: fallback fired for ~60% of judged pairs (not 30%), and decomposition
# emitted ~30k claims (~37/chunk -> ~1000 output tokens/chunk, not 400).
TOKENS_PER_WORD = 1.4          # English prose via LLM tokenizers, rough
CHUNK_WORDS = source_decomposer._CHUNK_WORD_TARGET
PROMPT_TOKENS = 300            # extraction/judgment prompt templates (~200 words)
DECOMP_OUT_TOKENS = 1000       # claims JSON per chunk (calibrated: was 400)
JUDGE_CALLS_PER_PAIR = 2       # of up to TOPK=3; verbatim matches skip the LLM
JUDGE_IN_TOKENS = 700          # prompt + claim + 3-sentence window
JUDGE_OUT_TOKENS = 80
FALLBACK_FRACTION = 0.6        # cited pairs hitting full-text extraction (calibrated: was 0.3)
FALLBACK_OUT_TOKENS = 1000
RESCUE_FRACTION = 0.4          # multi-sentence cited claims whose whole-claim verdict fails
                               # and triggers the tail rescue (paper1: 19 of 33 eligible)
RANGE_BAND = 2.0               # report [point/band, point*band]
CONFIRM_THRESHOLD_USD = 1.0    # real runs above this ask for confirmation

# Worst-case ceiling for the conditional add-on passes (own-split,
# partial-check, covering display). They run only on cited claims that END UP
# judged supported, so their real size isn't knowable pre-run — the ceiling
# assumes every cited claim comes back supported (owner ask 2026-07-12: put a
# price on the caveats instead of leaving them unquantified).
OWN_IN_TOKENS = 400            # own-split: 1 tiny classify call per uncited claim
OWN_OUT_TOKENS = 30
ARBITER_IN_TOKENS = 30_000     # --arbiter: shown evidence + ~20k-word source section
ARBITER_OUT_TOKENS = 600
PARTIAL_CALLS_WORST = 5        # partial-check 3-round ladder + component hunt, judge-shaped
COVER_IN_TOKENS = 1700         # covering call + pick-verify audit, per claim
COVER_OUT_TOKENS = 250


# ---------- pricing (parsed from docs/MODEL_OPTIONS.md) ----------

_PRICE_ROW_RE = re.compile(
    r"^\|[^|]*\|\s*`([^`]+)`\s*\|\s*\**~?\$([\d.]+)\**\s*\|\s*\**~?\$([\d.]+)\**\s*\|",
    re.MULTILINE)


def load_pricing(path: str = MODEL_OPTIONS_PATH) -> Dict[str, Dict[str, float]]:
    """{litellm_string: {'input': $/M, 'output': $/M}} from the Option-B table."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as e:
        logger.warning(f"Could not read pricing table {path}: {e}")
        return {}
    prices = {}
    for m in _PRICE_ROW_RE.finditer(text):
        prices[m.group(1).strip()] = {"input": float(m.group(2)),
                                      "output": float(m.group(3))}
    return prices


# ---------- estimation ----------

def estimate(text_claims: List[Dict], refs_map: Dict[str, str], sources_dir: str,
             cache_dir: str, model: str, paper_id_for, decompose: bool = True) -> Dict:
    """
    Predict the run's LLM work. text_claims/refs_map come from text_decomposer
    (no LLM); paper_id_for is verify_my_text's filename->paper_id function.
    decompose=False (the pipeline default since 2026-07-10) zeroes the source-
    decomposition line — only judging/fallback work remains.
    Returns a dict with call/token counts, a usd range (or None), and warnings.
    """
    # --- which sources will be decomposed (uncached, readable) ---
    cited_keys = {k for tc in text_claims for k in tc.get("markers", [])}
    warnings, sources_meta = [], {}
    for key in sorted(cited_keys):
        filename = refs_map.get(key)
        if not filename:
            warnings.append(f"marker [[{key}]] has no reference mapping")
            continue
        path = os.path.join(sources_dir, filename)
        if not os.path.exists(path):
            warnings.append(f"source file missing: {filename} (cited as [[{key}]])")
            continue
        pid = paper_id_for(filename)
        if pid in sources_meta:
            continue
        words = len(source_decomposer.read_source_file(path).split())
        if words == 0:
            warnings.append(f"source has no extractable text (scanned/broken?): {filename}")
        cache_path = os.path.join(cache_dir, f"{pid}.json")
        cached = False
        if os.path.exists(cache_path):
            try:
                import json
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached = json.load(f).get("file_hash") == source_decomposer.file_hash(path)
            except Exception:
                cached = False
        sources_meta[pid] = {"key": key, "filename": filename, "words": words,
                             "cached": cached}

    uncached = ([s for s in sources_meta.values() if not s["cached"] and s["words"] > 0]
                if decompose else [])
    decomp_calls = sum(math.ceil(s["words"] / CHUNK_WORDS) for s in uncached)
    decomp_in = sum(s["words"] * TOKENS_PER_WORD for s in uncached) \
        + decomp_calls * PROMPT_TOKENS
    decomp_out = decomp_calls * DECOMP_OUT_TOKENS

    # --- judgment + fallback over cited (claim x source) pairs ---
    pair_words = []   # full-text size of each cited pair's source (for fallback)
    for tc in text_claims:
        for key in tc.get("markers", []):
            filename = refs_map.get(key)
            if not filename:
                continue
            pid = paper_id_for(filename)
            meta = sources_meta.get(pid)
            if meta and meta["words"] > 0:
                pair_words.append(meta["words"])

    judge_calls = len(pair_words) * JUDGE_CALLS_PER_PAIR
    judge_in = judge_calls * JUDGE_IN_TOKENS
    judge_out = judge_calls * JUDGE_OUT_TOKENS

    fb_pairs = math.ceil(len(pair_words) * FALLBACK_FRACTION)
    # the biggest sources dominate the fallback cost; assume average pair
    avg_words = (sum(pair_words) / len(pair_words)) if pair_words else 0
    fb_calls = fb_pairs * 2                       # extraction + re-judgment
    fb_in = fb_pairs * (avg_words * TOKENS_PER_WORD + PROMPT_TOKENS) \
        + fb_pairs * JUDGE_IN_TOKENS
    fb_out = fb_pairs * (FALLBACK_OUT_TOKENS + JUDGE_OUT_TOKENS)

    # --- tail rescue: failed multi-sentence cited claims get their tail re-judged
    # (matcher.TAIL_RESCUE_MAX_SUFFIX); each rescue is roughly one extra
    # judgment + fallback pass. Cheap sentence count — this is an estimate.
    multi_cited = sum(1 for tc in text_claims
                      if tc.get("markers")
                      and len([s for s in re.split(r"(?<=[.!?])\s+", tc["text"]) if s]) >= 2)
    rescue_claims = math.ceil(multi_cited * RESCUE_FRACTION)
    rescue_calls = rescue_claims * (JUDGE_CALLS_PER_PAIR + 2)
    rescue_in = rescue_claims * (JUDGE_CALLS_PER_PAIR * JUDGE_IN_TOKENS
                                 + avg_words * TOKENS_PER_WORD + PROMPT_TOKENS
                                 + JUDGE_IN_TOKENS)
    rescue_out = rescue_claims * (JUDGE_CALLS_PER_PAIR * JUDGE_OUT_TOKENS
                                  + FALLBACK_OUT_TOKENS + JUDGE_OUT_TOKENS)

    in_tokens = decomp_in + judge_in + fb_in + rescue_in
    out_tokens = decomp_out + judge_out + fb_out + rescue_out

    prices = load_pricing().get(model)
    usd = None
    if prices:
        point = (in_tokens / 1e6) * prices["input"] + (out_tokens / 1e6) * prices["output"]
        usd = {"point": round(point, 3),
               "low": round(point / RANGE_BAND, 3),
               "high": round(point * RANGE_BAND, 3)}

    uncited = len([tc for tc in text_claims if not tc.get("markers")])
    return {
        "model": model,
        "sources_total": len(sources_meta),
        "sources_cached": sum(1 for s in sources_meta.values() if s["cached"]),
        "sources_to_decompose": len(uncached),
        "decomposition_calls": decomp_calls,
        "judgment_calls": judge_calls,
        "fallback_calls_assumed": fb_calls,
        "rescue_calls_assumed": rescue_calls,
        "total_calls": decomp_calls + judge_calls + fb_calls + rescue_calls,
        "input_tokens": int(in_tokens),
        "output_tokens": int(out_tokens),
        "usd": usd,
        "uncited_claims": uncited,   # cost nothing; listed for transparency
        "warnings": warnings,
    }


def addon_worst_case(model: str, n_own: int = 0, n_partial: int = 0,
                     n_cover: int = 0) -> Optional[float]:
    """$ ceiling for the conditional passes if EVERY cited claim is judged
    supported (their trigger). None when the model has no pricing row."""
    prices = load_pricing().get(model)
    if not prices:
        return None
    in_tok = (n_own * OWN_IN_TOKENS
              + n_partial * PARTIAL_CALLS_WORST * JUDGE_IN_TOKENS
              + n_cover * COVER_IN_TOKENS)
    out_tok = (n_own * OWN_OUT_TOKENS
               + n_partial * PARTIAL_CALLS_WORST * JUDGE_OUT_TOKENS
               + n_cover * COVER_OUT_TOKENS)
    return (in_tok / 1e6) * prices["input"] + (out_tok / 1e6) * prices["output"]


def arbiter_worst_case(model: str, n_flagged: int) -> Optional[float]:
    """$ ceiling for --arbiter if EVERY judged claim gets flagged (typical is
    30-60%). None when the model has no pricing row."""
    prices = load_pricing().get(model)
    if not prices:
        return None
    return (n_flagged * ARBITER_IN_TOKENS / 1e6) * prices["input"] \
        + (n_flagged * ARBITER_OUT_TOKENS / 1e6) * prices["output"]


def format_estimate(est: Dict) -> str:
    lines = [
        f"Cost estimate for model {est['model']} (rough — the point is no surprise bill):",
        f"  sources: {est['sources_total']} cited, {est['sources_cached']} already cached, "
        f"{est['sources_to_decompose']} to decompose",
        f"  LLM calls: ~{est['decomposition_calls']} decomposition + "
        f"~{est['judgment_calls']} judgment + ~{est['fallback_calls_assumed']} full-text "
        f"fallback + ~{est.get('rescue_calls_assumed', 0)} tail-rescue = ~{est['total_calls']}",
        f"  tokens: ~{est['input_tokens']/1e6:.2f}M in / ~{est['output_tokens']/1e6:.2f}M out",
    ]
    if est["usd"]:
        u = est["usd"]
        lines.append(f"  estimated cost: ${u['low']:.2f} – ${u['high']:.2f} "
                     f"(point ${u['point']:.2f})")
    else:
        lines.append(f"  estimated cost: no pricing known for {est['model']} "
                     f"(add it to docs/MODEL_OPTIONS.md's Option-B table)")
    if est["warnings"]:
        lines.append(f"  pre-flight warnings ({len(est['warnings'])}):")
        for w in est["warnings"]:
            lines.append(f"    ⚠ {w}")
    return "\n".join(lines)
