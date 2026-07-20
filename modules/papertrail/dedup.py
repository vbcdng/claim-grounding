"""
Cross-source claim dedup (slot A4): find the same claim asserted by multiple
cited sources, over the decomposition artifacts every run already has.

Consumers: evidence_independence.py reads the resulting dedup.json (its
"content_overlap" signal counts cluster co-membership per source pair), and the
viewer's corroboration view (queue #3) lists claims asserted by 2+ sources.

Architecture (design: docs/DEDUP_DESIGN.md). Cross-source populations are
~10^3-10^4 claims, so — unlike argument_map.find_variants, which is small enough
to run lexical over all pairs — cosine similarity does the *blocking*: it
proposes candidate pairs and nothing else. The bentonite calibration (2026-07-05)
showed why it can never merge: a "pseudo-FIRST-order model" ~ "pseudo-SECOND-
order model" pair — different claims — scored 0.983, near the very top of the
whole corpus. A pair becomes strong (merged) only via near-verbatim lexical
similarity (negation-guarded) or an explicit LLM verdict; weak pairs are
surfaced as questions, never clustered.
"""

import json
import logging
import os
import re
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

PROMPT_FILE = "pt_dedup_confirm_prompt.txt"

# Retrieval floor for candidate pairs. Bentonite calibration: 0.95 -> 172
# candidates over 1.13M cross-source pairs; 0.93 -> 889, dominated by
# same-topic-different-claim chatter in a single-domain corpus.
COS_FLOOR = 0.95
# Per-claim cap on retained candidates (highest-cosine first) — bounds the
# candidate set on corpora denser than the calibration one.
TOP_K = 3
# Near-verbatim lexical ratio => strong (same bar as find_variants).
LEX_STRONG = 0.90
# Pairs per batched LLM confirm call.
CONFIRM_CHUNK = 40

_RELATIONS = ("restatement", "hedged_variant", "different_claim")
_NEG_TOKENS = {"not", "no", "never", "cannot", "nor", "neither", "without"}
_ORDINALS = {"first", "second", "third", "fourth", "fifth", "zeroth",
             "half", "double", "triple"}


def _load_prompt(name: str) -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "config", "prompts", name), "r", encoding="utf-8") as f:
        return f.read()


def _oneline(text: str) -> str:
    return " ".join((text or "").split())


def _lex_sim(a: str, b: str) -> float:
    return SequenceMatcher(None, _oneline(a).lower(), _oneline(b).lower()).ratio()


def _has_negation(text: str) -> bool:
    for tok in _oneline(text).lower().replace(",", " ").replace(".", " ").split():
        if tok in _NEG_TOKENS or tok.endswith("n't"):
            return True
    return False


def _negation_mismatch(a: str, b: str) -> bool:
    return _has_negation(a) != _has_negation(b)


def _key_tokens(text: str) -> List[str]:
    """Numeric tokens + ordinal words — the single-token switches that make two
    near-identical sentences DIFFERENT claims ('pseudo-first-order' vs 'pseudo-
    second-order' scores 0.92 lexically; '...for 2024' vs '...for 2025' ~0.98)."""
    toks = re.findall(r"[a-z0-9.]+", _oneline(text).lower())
    return sorted(t for t in toks
                  if any(ch.isdigit() for ch in t) or t in _ORDINALS)


def _numeric_mismatch(a: str, b: str) -> bool:
    return _key_tokens(a) != _key_tokens(b)


def _normalize_vecs(vecs) -> "Any":
    """To a row-L2-normalized float32 numpy array (torch tensors accepted)."""
    import numpy as np
    if hasattr(vecs, "cpu"):
        vecs = vecs.cpu().numpy()
    arr = np.asarray(vecs, dtype="float32")
    if arr.ndim != 2:
        arr = arr.reshape(len(arr), -1)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return arr / norms


def _candidate_pairs(keys: List[str], claims_by_key: Dict[str, List[Dict]],
                     vecs_by_key: Dict[str, Any], cos_floor: float,
                     top_k: int) -> List[Dict[str, Any]]:
    """Cosine blocking: all cross-source pairs >= cos_floor, then a per-claim
    cap of top_k retained candidates (highest cosine first, both ends)."""
    import numpy as np
    raw = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            ka, kb = keys[i], keys[j]
            sim = vecs_by_key[ka] @ vecs_by_key[kb].T
            for a_idx, b_idx in np.argwhere(sim >= cos_floor):
                raw.append((float(sim[a_idx][b_idx]), ka, int(a_idx), kb, int(b_idx)))
    raw.sort(key=lambda r: -r[0])

    kept, per_claim = [], {}
    for cos, ka, a_idx, kb, b_idx in raw:
        a, b = claims_by_key[ka][a_idx], claims_by_key[kb][b_idx]
        if per_claim.get(a["id"], 0) >= top_k or per_claim.get(b["id"], 0) >= top_k:
            continue
        per_claim[a["id"]] = per_claim.get(a["id"], 0) + 1
        per_claim[b["id"]] = per_claim.get(b["id"], 0) + 1
        kept.append({"a": a["id"], "b": b["id"], "source_a": ka, "source_b": kb,
                     "cos": round(cos, 4)})
    return kept


def _confirm_chunk(pairs: List[Dict], text_of: Dict[str, str], llm) -> Optional[Dict[int, Dict]]:
    """One batched call over one chunk. {index-in-chunk: verdict} or None on
    parse failure (fail-open: heuristic strengths kept for the chunk)."""
    lines = []
    for n, p in enumerate(pairs, 1):
        lines.append(f"PAIR {n}:\n  A: {_oneline(text_of[p['a']])}\n  B: {_oneline(text_of[p['b']])}")
    prompt = _load_prompt(PROMPT_FILE).replace("{PAIRS}", "\n".join(lines))
    raw = llm.call(prompt, temperature=0.0, max_output_tokens=4096)
    if not raw:
        return None
    from .llm_client import extract_json
    data = extract_json(raw)
    if not isinstance(data, dict) or not isinstance(data.get("pairs"), list):
        return None
    out = {}
    for item in data["pairs"]:
        if not isinstance(item, dict):
            continue
        n, rel = item.get("n"), item.get("relation")
        if isinstance(n, int) and 1 <= n <= len(pairs) and rel in _RELATIONS:
            out[n - 1] = {"relation": rel, "why": _oneline(str(item.get("why") or ""))}
    return out


def find_duplicates(sources: Dict[str, List[Dict]],
                    embed_fn: Callable[[str, List[str]], Sequence] = None,
                    llm=None, cos_floor: float = COS_FLOOR,
                    top_k: int = TOP_K) -> Dict[str, Any]:
    """Cross-source duplicate detection over decomposed source claims.

    sources: {source_key: [{"id", "text"}, ...]} — ids must be globally unique
    (the decomposer's "<paperid8>_c<i>" ids are). embed_fn(source_key, texts)
    returns one vector per text (REQUIRED — cosine blocking is what makes the
    cross-source population tractable). llm: optional confirm pass; verdicts
    may upgrade weak->strong or kill a pair; parse failures fail open.
    """
    if embed_fn is None:
        raise ValueError("find_duplicates requires embed_fn (cosine blocking)")

    claims_by_key: Dict[str, List[Dict]] = {}
    text_of: Dict[str, str] = {}
    source_of: Dict[str, str] = {}
    for key, claims in sources.items():
        kept = []
        for c in claims or []:
            cid, text = (c.get("id") or "").strip(), (c.get("text") or "").strip()
            if not cid or not text:
                continue
            if cid in source_of:
                logger.warning(f"dedup: duplicate claim id {cid} — keeping first")
                continue
            kept.append({"id": cid, "text": text})
            text_of[cid] = text
            source_of[cid] = key
        if kept:
            claims_by_key[key] = kept

    keys = sorted(claims_by_key)
    method = "cosine+lexical"
    # n_claims lets consumers normalize per-source: A1's content_overlap ratio
    # over clustered-claims-only saturates to 1.0 when a source has a single
    # clustered claim (seen on bentonite: 1 shared cluster out of 174 claims
    # read as "strong" overlap). Total counts make an honest denominator possible.
    n_claims = {k: len(claims_by_key[k]) for k in keys}
    empty = {"clusters": [], "pairs": [], "n_weak_pairs": 0, "method": method,
             "n_claims": n_claims,
             "params": {"cos_floor": cos_floor, "top_k": top_k, "lex_strong": LEX_STRONG}}
    if len(keys) < 2:
        return empty

    vecs_by_key = {k: _normalize_vecs(embed_fn(k, [c["text"] for c in claims_by_key[k]]))
                   for k in keys}
    pairs = _candidate_pairs(keys, claims_by_key, vecs_by_key, cos_floor, top_k)

    # Grade candidates: near-verbatim lexical merges at $0 — guarded against
    # negation flips AND number/ordinal switches (either caps the pair at weak;
    # a mismatch on those tokens usually means a DIFFERENT claim, and a false
    # weak is just a surfaced question while a false strong is a wrong merge).
    for p in pairs:
        ta, tb = text_of[p["a"]], text_of[p["b"]]
        p["lex"] = round(_lex_sim(ta, tb), 4)
        p["strength"] = ("strong" if p["lex"] >= LEX_STRONG
                         and not _negation_mismatch(ta, tb)
                         and not _numeric_mismatch(ta, tb) else "weak")
        p["llm"] = None

    if llm is not None and pairs:
        any_parsed, dropped = False, []
        for start in range(0, len(pairs), CONFIRM_CHUNK):
            chunk = pairs[start:start + CONFIRM_CHUNK]
            verdicts = _confirm_chunk(chunk, text_of, llm)
            if verdicts is None:
                logger.warning("dedup: LLM confirm chunk unparseable — failing open")
                continue
            any_parsed = True
            for n, v in verdicts.items():
                p = chunk[n]
                if v["relation"] == "different_claim":
                    dropped.append(id(p))
                else:
                    p["strength"] = "strong"
                p["llm"] = v
        if any_parsed:
            method += "+llm"
            pairs = [p for p in pairs if id(p) not in set(dropped)]

    # Union-find over STRONG pairs only -> clusters; weak pairs stay questions.
    parent: Dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for p in pairs:
        if p["strength"] == "strong":
            ra, rb = find(p["a"]), find(p["b"])
            if ra != rb:
                parent[rb] = ra

    groups: Dict[str, List[str]] = {}
    for cid in parent:
        groups.setdefault(find(cid), []).append(cid)
    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda cid: (source_of[cid], cid))
        clusters.append([{"source": source_of[cid], "id": cid, "text": text_of[cid]}
                         for cid in members])
    clusters.sort(key=lambda c: (-len(c), c[0]["id"]))

    return {"clusters": clusters, "pairs": pairs,
            "n_weak_pairs": sum(1 for p in pairs if p["strength"] == "weak"),
            "method": method, "n_claims": n_claims,
            "params": {"cos_floor": cos_floor, "top_k": top_k, "lex_strong": LEX_STRONG}}


def write_dedup(payload: Dict[str, Any], run_dir: str) -> str:
    path = os.path.join(run_dir, "dedup.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    return path


def load_run_sources(run_dir: str):
    """(sources, pid_of): claims per source key from <run_dir>/source_claims/,
    plus each key's paper_id for locating its embedding cache file."""
    sc_dir = os.path.join(run_dir, "source_claims")
    sources, pid_of, full_texts = {}, {}, {}
    if not os.path.isdir(sc_dir):
        return sources, pid_of, full_texts
    for fn in sorted(os.listdir(sc_dir)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(sc_dir, fn), "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:
            logger.warning(f"dedup: unreadable {fn}: {e}")
            continue
        key = d.get("key") or d.get("paper_id") or fn[:-5]
        sources[key] = d.get("claims") or []
        pid_of[key] = d.get("paper_id") or fn[:-5]
        # The run's .claims.npz cache is keyed by the FULL text list (empties
        # included), exactly as matcher builds it — reproduce that here.
        full_texts[key] = [(c.get("text") or "") for c in (d.get("claims") or [])]
    return sources, pid_of, full_texts


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Cross-source claim dedup over a finished run")
    ap.add_argument("run_dir", help="run output dir (has source_claims/, embeddings/)")
    ap.add_argument("--llm-confirm", action="store_true",
                    help="batched LLM confirm pass on candidate pairs (costs API calls)")
    ap.add_argument("--model", default=None, help="LLM model for --llm-confirm")
    ap.add_argument("--cos-floor", type=float, default=COS_FLOOR)
    ap.add_argument("--top-k", type=int, default=TOP_K)
    args = ap.parse_args(argv)

    from . import embeddings
    emb_dir = os.path.join(args.run_dir, "embeddings")
    sources, pid_of, full_texts = load_run_sources(args.run_dir)
    if len([k for k, v in sources.items() if v]) < 2:
        print("Fewer than 2 sources with claims — nothing to dedup.")
        write_dedup(find_duplicates({k: v for k, v in sources.items()},
                                    embed_fn=lambda k, t: []), args.run_dir)
        return 0

    def embed_fn(key, texts):
        cache = os.path.join(emb_dir, f"{pid_of[key]}.claims.npz")
        vecs = embeddings.embed_cached(full_texts[key], cache)
        rows = [i for i, t in enumerate(full_texts[key]) if t.strip()]
        # find_duplicates drops empty-text claims in the same order.
        assert len(rows) == len(texts)
        return vecs[rows]

    llm = None
    if args.llm_confirm:
        from .llm_client import LLMClient
        llm = LLMClient(model=args.model)

    payload = find_duplicates(sources, embed_fn=embed_fn, llm=llm,
                              cos_floor=args.cos_floor, top_k=args.top_k)
    path = write_dedup(payload, args.run_dir)
    print(f"{len(payload['clusters'])} clusters, {len(payload['pairs'])} candidate pairs "
          f"({payload['n_weak_pairs']} weak questions), method={payload['method']}")
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
