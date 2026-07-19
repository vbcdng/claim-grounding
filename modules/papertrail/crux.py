"""Crux finder — which claim, if it flipped, most changes the conclusion (Stream A).

Given the argument graph from argument_map.py, surface the load-bearing claims: the
ones whose truth-value most determines whether the thesis stands. FLF's "surface
cruxes" ask, scoped to "a real basic feature" (owner). A nudge for the reader —
never a verdict.

METHOD (cheap first): v1 scores each claim by its structural leverage on the thesis
— shorter path to a thesis, being contested, and connectedness all raise the score —
with ZERO LLM calls (topology only). An optional single LLM pass over the top-k
candidates confirms "if this were false, does the conclusion still hold?", setting
`why` and pruning false positives (`confirm_with_llm=True`, ~1 call). Default is
free.

V2 (slot A1, still zero new calls): pass the finished `analysis` dict (and,
optionally, the independence.json payload from evidence_independence.py) and each
claim's leverage is multiplied by its evidential FRAGILITY — how contestable its
footing already is per the run's own verdicts: unsupported > uncited fact >
partial-support / second-opinion-disputed > all-cited-sources-correlated >
single-source > multi-independent. A claim that is load-bearing AND weakly
evidenced is exactly FRI's crux. Omitting `analysis` gives v1 behavior unchanged.
Full rationale + the fragility table: docs/ASSESSMENT_DESIGN.md.

INPUT (read-only): the argument_map.json payload from argument_map.build_map();
optionally the analysis dict + independence payload.
OUTPUT (additive): crux.json next to analysis.json. Never flips a verdict.

SCOPE / gap note: "proper" crux-finding (counterfactual reasoning over a formal
model, correlated-evidence discounting — docs/submission/EPISTACK_LANDSCAPE.md §4c) is hard;
this is the tractable slice: leverage-on-the-thesis ranking over the inferred graph.

NON-BLOCKING: experimental Track `streamA`; depends on argument_map.py; dropped
before argmap if Fable time runs short (order: snowball -> origin-trace -> crux ->
argmap).
"""
import json
import logging
import os
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Tuple

from . import argument_map
from .llm_client import extract_json

logger = logging.getLogger("papertrail.crux")

PROMPT_FILE = "pt_crux_confirm_prompt.txt"


def _node_stats(node_id: str, argmap: Dict[str, Any]) -> Dict[str, Any]:
    """Structural facts about one node: does it reach a thesis (and in how many
    hops, over influence edges), is it contested (attacked), and its degree."""
    thesis = set(argmap.get("thesis_ids", []))
    edges = argmap.get("edges", [])

    out_adj: Dict[str, List[str]] = defaultdict(list)
    degree = 0
    incoming_attack = False
    for e in edges:
        out_adj[e["from"]].append(e["to"])
        if e["from"] == node_id or e["to"] == node_id:
            degree += 1
        if e["to"] == node_id and e["type"] == "attack":
            incoming_attack = True

    # Shortest directed hop count from node to any thesis over influence edges.
    dist = None
    if node_id in thesis:
        dist = 0
    else:
        seen = {node_id}
        q = deque([(node_id, 0)])
        while q:
            cur, d = q.popleft()
            for nxt in out_adj.get(cur, []):
                if nxt in seen:
                    continue
                if nxt in thesis:
                    dist = d + 1
                    q.clear()
                    break
                seen.add(nxt)
                q.append((nxt, d + 1))
            if dist is not None:
                break
    return {"reaches": dist is not None, "dist": dist,
            "contested": incoming_attack, "degree": degree}


def leverage_score(node_id: str, argmap: Dict[str, Any]) -> float:
    """Raw structural leverage of one node on the thesis (unnormalized).
    find_cruxes() normalizes these to 0..1 across the candidate set."""
    s = _node_stats(node_id, argmap)
    raw = 0.0
    if s["reaches"] and s["dist"]:
        raw += 1.0 / s["dist"]
    if s["contested"]:
        raw += 0.5
    raw += 0.1 * min(s["degree"], 5) / 5.0
    return raw


def _why(stats: Dict[str, Any]) -> str:
    if stats["reaches"] and stats["dist"]:
        base = f"reaches the thesis in {stats['dist']} step(s)"
    else:
        base = "connected to the argument but has no path to a thesis"
    if stats["contested"]:
        base += ", and is itself contested"
    return "High leverage: " + base


def _fragility(claim: Dict[str, Any],
               indep_entry: Optional[Dict[str, Any]]) -> Tuple[float, str]:
    """(fragility 0..1, one-phrase reason) for one analysis claim — computed
    entirely from the run's own verdicts and flags, no new calls. The table
    lives in docs/ASSESSMENT_DESIGN.md; keep the two in sync."""
    verdict = claim.get("verdict")
    if verdict == "unsupported":
        return 1.0, "its verdict is unsupported"
    if verdict == "own":
        kind = (claim.get("own_kind") or {}).get("kind")
        if kind == "fact":
            return 0.85, "an uncited factual assertion"
        return 0.5, "an uncited claim (nothing was checked)"
    if verdict == "supported":
        if claim.get("partial_support"):
            return 0.7, "flagged for possible partial support"
        if (claim.get("second_opinion") or {}).get("agrees") is False:
            return 0.7, "a second opinion disagreed with the verdict"
        cited = len(claim.get("paper_ids") or [])
        if indep_entry and cited >= 2 and indep_entry.get("effective", cited) == 1:
            return 0.6, "its cited sources may not be independent"
        if cited >= 2:
            return 0.25, ""
        return 0.45, "rests on a single source"
    return 0.5, ""


def find_cruxes(argmap: Dict[str, Any], llm=None, top_k: int = 5,
                confirm_with_llm: bool = False,
                analysis: Optional[Dict[str, Any]] = None,
                independence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Rank claims by leverage on the thesis; return the crux.json payload.
    Topology-only by default (zero API cost). Pass `analysis` (and optionally
    the independence payload) to weight leverage by evidential fragility — v2,
    still zero new calls. Set confirm_with_llm=True with an `llm` to run one
    confirmation pass over the top-k."""
    thesis = set(argmap.get("thesis_ids", []))
    by_id = {n["id"]: n for n in argmap.get("nodes", [])}
    candidates = [n for n in argmap.get("nodes", []) if n["id"] not in thesis]

    claims_by_id = {c["id"]: c for c in (analysis or {}).get("text_claims", [])
                    if c.get("id")}
    per_claim = (independence or {}).get("per_claim", {})

    raws = {}
    frag_notes = {}
    for n in candidates:
        raw = leverage_score(n["id"], argmap)
        if analysis is not None and n["id"] in claims_by_id:
            frag, note = _fragility(claims_by_id[n["id"]], per_claim.get(n["id"]))
            raw *= 0.4 + 0.6 * frag
            frag_notes[n["id"]] = (round(frag, 3), note)
        raws[n["id"]] = raw
    maxr = max(raws.values(), default=0.0)

    scored = []
    for n in candidates:
        raw = raws[n["id"]]
        if raw <= 0:
            continue
        stats = _node_stats(n["id"], argmap)
        entry = {"id": n["id"], "text": n["text"],
                 "score": round(raw / maxr, 3) if maxr > 0 else 0.0,
                 "why": _why(stats)}
        if n["id"] in frag_notes:
            frag, note = frag_notes[n["id"]]
            entry["fragility"] = frag
            if note:
                entry["why"] += "; " + note
        scored.append(entry)
    scored.sort(key=lambda c: c["score"], reverse=True)
    cruxes = scored[:top_k]

    payload = {"cruxes": cruxes,
               "method": "topology" if analysis is None else "topology+fragility",
               "model": None, "prompt_sha": None}

    if confirm_with_llm and llm and cruxes:
        confirmed = confirm_cruxes(cruxes, argmap, llm)
        payload["cruxes"] = confirmed
        payload["method"] += "+llm"
        payload["model"] = getattr(llm, "model", None)
        prompt = argument_map._load_prompt(PROMPT_FILE)
        payload["prompt_sha"] = argument_map._prompt_sha(prompt)
    return payload


def confirm_cruxes(candidates: List[Dict[str, Any]], argmap: Dict[str, Any],
                   llm) -> List[Dict[str, Any]]:
    """One LLM pass over the top-k: set `why`, drop non-cruxes. On a parse
    failure the candidates pass through unchanged (fail-open — a nudge, never a
    silent deletion)."""
    by_id = {n["id"]: n for n in argmap.get("nodes", [])}
    thesis_txt = "\n".join(f"- {by_id[t]['text']}" for t in argmap.get("thesis_ids", [])
                           if t in by_id) or "(no explicit thesis detected)"
    block = "\n".join(f"{i}. {c['text']}" for i, c in enumerate(candidates, 1))
    prompt = (argument_map._load_prompt(PROMPT_FILE)
              .replace("{THESIS}", thesis_txt).replace("{CANDIDATES}", block))
    raw = llm.call(prompt, temperature=0.0, max_output_tokens=1024)
    obj = extract_json(raw)
    if isinstance(obj, dict):
        obj = obj.get("cruxes", [])
    if not isinstance(obj, list) or not obj:
        logger.warning("crux confirm: unparseable response; keeping topology ranking")
        return candidates

    verdicts = {}
    for item in obj:
        if not isinstance(item, dict):
            continue
        try:
            n = int(item.get("n"))
        except (TypeError, ValueError):
            continue
        if 1 <= n <= len(candidates):
            verdicts[n] = item

    out = []
    for i, c in enumerate(candidates, 1):
        v = verdicts.get(i)
        if v is None:
            out.append(c)                      # no verdict → keep (fail-open)
            continue
        if v.get("is_crux") is False:
            continue                            # confirmed non-crux → drop
        c = dict(c)
        if v.get("why"):
            c["why"] = argument_map._oneline(str(v["why"]))[:200]
        out.append(c)
    return out


def write_cruxes(payload: Dict[str, Any], output_dir: str) -> None:
    """Write crux.json into output_dir."""
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "crux.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
