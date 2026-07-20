"""Argument map — infer the support/attack structure BETWEEN claims (Stream A).

The ingestion layer tells us, per claim, "is this supported by its cited source?"
(verdict). This module adds the *structure* layer FLF asks for: how the author's
claims relate to EACH OTHER — which claim supports, elaborates, or attacks which.
Output is a small directed graph, an Argdown export (git-diffable, opens in Argdown
tools), and a JSON payload the viewer renders. "A basic real feature," not a debate
platform (owner, 2026-07-03).

INPUT  (read-only; never mutates the run): a finished analysis dict. Nodes are the
author's `text_claims` in document order. Source/omitted claims are NOT nodes in v1
(keep the graph the author's argument, not the literature).

OUTPUT (additive): build_map() returns the argument_map.json payload; write_map()
writes argument_map.json + argument_map.argdown next to analysis.json. Neither
touches analysis.json or any verdict.

METHOD: ONE LLM pass over the numbered claim list (not O(n^2) pairwise) returns the
edge list; the model never sees ids, only 1-based numbers, which we map back. Edges
are cached on disk keyed by (node ids+text, model, prompt_sha) so re-runs are free.

FEASIBILITY NOTE (owner asked to record this): "proper" epistack argmap — formal
inference structure, defeasible reasoning, correlated-evidence detection (see
docs/submission/EPISTACK_LANDSCAPE.md §4b/§4c) — is a large research problem. THIS is the
tractable slice: LLM-inferred support/attack/elaborates edges over already-decomposed
claims, rendered durably.

NON-BLOCKING: experimental Track (branch `streamA`). Adds artifacts + a viewer
section; never changes a verdict, matcher, or existing prompt. Drop order if Fable
time runs short: snowball -> origin-trace -> crux -> argmap (this is protected last).
"""
import hashlib
import json
import logging
import os
import re
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Optional, Tuple

from .llm_client import extract_json

logger = logging.getLogger("papertrail.argument_map")

PROMPT_FILE = "pt_argmap_edges_prompt.txt"
VARIANTS_PROMPT_FILE = "pt_argmap_variants_prompt.txt"
EDGE_TYPES = ("support", "attack", "elaborates")
SUPPORT_LIKE = ("support", "elaborates")        # count toward "reason FOR"
NODE_ROLES = ("thesis", "premise", "sub", "aside")
DEFAULT_CONFIDENCE = 0.6


def _load_prompt(name: str) -> str:
    """Mirror of matcher._load_prompt — kept local so this module needn't import
    matcher (and its heavy embedding deps) just to read a prompt file."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "config", "prompts", name), "r", encoding="utf-8") as f:
        return f.read()


def _prompt_sha(prompt: str) -> str:
    return hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:8]


def _oneline(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _nodes_from(analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Author's claims, document order, that have an id and real text."""
    out = []
    for c in analysis.get("text_claims", []):
        if c.get("id") and _oneline(c.get("text", "")):
            out.append(c)
    return out


def _influence_counts(ids: set, edges: List[Dict[str, Any]]) -> Tuple[Dict[str, int], Dict[str, int]]:
    """(in_degree, out_degree) over ALL edge types (support, attack, elaborates).
    'Influence' = the from-claim does argumentative work on the to-claim."""
    indeg = {i: 0 for i in ids}
    outdeg = {i: 0 for i in ids}
    for e in edges:
        f, t = e.get("from"), e.get("to")
        if f in outdeg:
            outdeg[f] += 1
        if t in indeg:
            indeg[t] += 1
    return indeg, outdeg


def _claims_block(nodes: List[Dict[str, Any]]) -> str:
    return "\n".join(f"{i}. {_oneline(n['text'])}" for i, n in enumerate(nodes, 1))


def infer_edges(claims: List[Dict[str, Any]], llm) -> Optional[List[Dict[str, Any]]]:
    """One LLM pass over the numbered claim list -> validated edge list with ids.

    The model sees only 1-based numbers; we map them back to claim ids here and
    drop anything malformed (out-of-range number, self-loop, unknown type,
    duplicate). Returns [{from, to, type, confidence, reason}] with from/to as
    claim IDS. Returns None (NOT []) when the call fails or nothing parses —
    "the model found no relationships" and "the call failed" must stay
    distinguishable so a failure is never cached as an empty map.
    """
    if not claims:
        return []
    prompt = _load_prompt(PROMPT_FILE)
    # Output must scale with input: paper1's 81 claims produced a ~4.6k-token
    # edge list, so a fixed 2048 cap truncated the JSON and the whole map
    # silently degraded to 0 edges (A5 audit, 2026-07-06). ~57 tok/claim
    # measured; 128/claim gives >2x headroom.
    raw = llm.call(prompt.replace("{CLAIMS}", _claims_block(claims)),
                   temperature=0.0,
                   max_output_tokens=max(2048, 128 * len(claims)))
    obj = extract_json(raw)
    if isinstance(obj, dict):
        obj = obj.get("edges", [])
    if not isinstance(obj, list):
        logger.warning("argmap: no edge list parsed from LLM response")
        return None

    n = len(claims)
    seen = set()
    edges: List[Dict[str, Any]] = []
    for e in obj:
        if not isinstance(e, dict):
            continue
        try:
            fi = int(e.get("from"))
            ti = int(e.get("to"))
        except (TypeError, ValueError):
            continue
        if not (1 <= fi <= n and 1 <= ti <= n) or fi == ti:
            continue
        etype = str(e.get("type", "")).lower().strip()
        if etype not in EDGE_TYPES:
            continue
        fid, tid = claims[fi - 1]["id"], claims[ti - 1]["id"]
        key = (fid, tid, etype)
        if key in seen:
            continue
        seen.add(key)
        try:
            conf = float(e.get("confidence", DEFAULT_CONFIDENCE))
        except (TypeError, ValueError):
            conf = DEFAULT_CONFIDENCE
        conf = max(0.0, min(1.0, conf))
        edges.append({"from": fid, "to": tid, "type": etype,
                      "confidence": round(conf, 3),
                      "reason": _oneline(str(e.get("reason", "")))[:200]})
    return edges


def classify_roles(claims: List[Dict[str, Any]],
                   edges: List[Dict[str, Any]]) -> Dict[str, str]:
    """Assign each claim id a NODE_ROLE from the influence topology:
      thesis  — influenced by others, influences nothing (a conclusion)
      premise — influences others, nothing influences it (an input)
      sub     — both (an intermediate step)
      aside   — neither (unconnected: a standalone remark)
    A rough visual aid, not a verdict."""
    ids = {c["id"] for c in claims}
    indeg, outdeg = _influence_counts(ids, edges)
    roles = {}
    for c in claims:
        i, o = indeg[c["id"]], outdeg[c["id"]]
        if i > 0 and o == 0:
            roles[c["id"]] = "thesis"
        elif o > 0 and i == 0:
            roles[c["id"]] = "premise"
        elif i > 0 and o > 0:
            roles[c["id"]] = "sub"
        else:
            roles[c["id"]] = "aside"
    return roles


def _thesis_ids(claims: List[Dict[str, Any]], edges: List[Dict[str, Any]],
                roles: Dict[str, str]) -> List[str]:
    thesis = [c["id"] for c in claims if roles.get(c["id"]) == "thesis"]
    if thesis:
        return thesis
    # Fallback for cyclic/degenerate graphs: the most-influenced node(s).
    ids = {c["id"] for c in claims}
    indeg, _ = _influence_counts(ids, edges)
    best = max(indeg.values(), default=0)
    return [c["id"] for c in claims if best > 0 and indeg[c["id"]] == best]


def _title(node_id: str) -> str:
    """Argdown statement title: alnum/_/- only, must start with a letter."""
    t = re.sub(r"[^0-9A-Za-z_-]", "-", str(node_id)).strip("-") or "c"
    return t if t[0].isalpha() else "c" + t


def to_argdown(payload: Dict[str, Any]) -> str:
    """Render the map as Argdown source (pure string transform, no LLM).
    Each claim with incoming edges is printed with its supporters (<+) and
    attackers/counterpoints (<-) nested one level; isolated claims are listed
    as standalone statements so nothing is lost."""
    by_id = {n["id"]: n for n in payload.get("nodes", [])}
    incoming: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    has_edge = set()
    for e in payload.get("edges", []):
        incoming[e["to"]].append(e)
        has_edge.add(e["from"])
        has_edge.add(e["to"])

    lines: List[str] = ["// Argument map (generated). <+ supports, <- attacks.", ""]
    thesis = list(payload.get("thesis_ids", []))
    # Roots first (thesis), then any other claim that has supporters/attackers.
    ordered = thesis + [nid for nid in incoming if nid not in thesis]
    for nid in ordered:
        n = by_id.get(nid)
        if not n:
            continue
        lines.append(f"[{_title(nid)}]: {_oneline(n['text'])}")
        for e in incoming.get(nid, []):
            src = by_id.get(e["from"])
            if not src:
                continue
            op = "<+" if e["type"] in SUPPORT_LIKE else "<-"
            lines.append(f"    {op} [{_title(e['from'])}]: {_oneline(src['text'])}")
        lines.append("")

    isolated = [n for n in payload.get("nodes", []) if n["id"] not in has_edge]
    if isolated:
        lines.append("// Unconnected claims")
        for n in isolated:
            lines.append(f"[{_title(n['id'])}]: {_oneline(n['text'])}")
        lines.append("")
    return "\n".join(lines)


def _cache_key(nodes: List[Dict[str, Any]], model: str, psha: str) -> str:
    h = hashlib.sha1()
    for n in nodes:
        h.update(n["id"].encode("utf-8"))
        h.update(b"\t")
        h.update(_oneline(n["text"]).encode("utf-8"))
        h.update(b"\n")
    h.update(f"{model}\t{psha}".encode("utf-8"))
    return h.hexdigest()[:16]


def build_map(analysis: Dict[str, Any], llm,
              cache_dir: Optional[str] = None) -> Dict[str, Any]:
    """Infer the claim->claim edge set from a finished analysis dict and return
    the argument_map.json payload (nodes, edges, thesis_ids, model, prompt_sha).

    Read-only on `analysis`. Edges are reused from `cache_dir` when the node set,
    model, and prompt are unchanged (zero LLM calls on a hit). Edge inference is
    ONE batched call, so there is no fan-out to cap or parallelize (the former
    `workers` parameter was a documented no-op — removed 2026-07-06).
    """
    nodes = _nodes_from(analysis)
    prompt = _load_prompt(PROMPT_FILE)
    psha = _prompt_sha(prompt)
    model = getattr(llm, "model", None)

    edges: Optional[List[Dict[str, Any]]] = None
    cache_file = None
    if cache_dir and model and nodes:
        cache_file = os.path.join(cache_dir, f"argmap_{_cache_key(nodes, model, psha)}.json")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    edges = json.load(f)
                logger.info("argmap: reused %d cached edges", len(edges))
            except Exception as e:
                logger.warning("argmap: cache read failed (%s); recomputing", e)
                edges = None

    if edges is None:
        edges = infer_edges(nodes, llm)
        if edges is None:
            # LLM call failed / unparseable. Proceed with an empty edge set for
            # THIS build, but never cache it — the next run must retry, not
            # silently inherit "no relationships" from a transient failure.
            logger.warning("argmap: edge inference failed; result NOT cached")
            edges = []
        elif cache_file:
            os.makedirs(cache_dir, exist_ok=True)
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(edges, f, indent=2, ensure_ascii=False)

    roles = classify_roles(nodes, edges)
    thesis_ids = _thesis_ids(nodes, edges, roles)
    return {
        "nodes": [{"id": n["id"], "text": _oneline(n["text"]),
                   "verdict": n.get("verdict"), "role": roles[n["id"]]}
                  for n in nodes],
        "edges": edges,
        "thesis_ids": thesis_ids,
        "model": model,
        "prompt_sha": psha,
    }


def write_map(payload: Dict[str, Any], output_dir: str) -> None:
    """Write argument_map.json + argument_map.argdown into output_dir."""
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "argument_map.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    with open(os.path.join(output_dir, "argument_map.argdown"), "w", encoding="utf-8") as f:
        f.write(to_argdown(payload))


# ---------------------------------------------------------------------------
# Claim variants (slot A2) — "similar-but-not-identical" claims grouped.
#
# The same assertion restated (intro thesis vs. conclusion thesis) or a hedged
# vs. strong form should read as ONE claim with variants, not N independent
# nodes. Tiered like the matcher: free lexical similarity → optional injected
# embeddings cosine → optional single batched LLM confirm. Strong/weak policy
# (docs/ARGMAP_FEASIBILITY.md gap 2): only STRONG pairs form groups; weak pairs
# are surfaced as questions, never merged. Output is additive — the graph and
# its edges are never rewritten here.
# ---------------------------------------------------------------------------

VARIANT_LEX_STRONG = 0.90
VARIANT_LEX_WEAK = 0.75
# Cosine is retrieval-only: it can propose a WEAK candidate, never a strong one.
# Real-data finding (paper1, 2026-07-05): SPECTER's same-document floor is
# ~0.85+, and an "applies to X" / "does NOT apply to X" pair scored 0.949 —
# cosine cannot be trusted to merge. Only near-verbatim lexical similarity or
# an explicit LLM verdict makes a pair strong.
VARIANT_COS_WEAK = 0.93
_VARIANT_RELATIONS = ("restatement", "hedged_variant", "different_claim")
_NEG_TOKENS = {"not", "no", "never", "cannot", "nor", "neither", "without"}


def _lex_sim(a: str, b: str) -> float:
    """Order-sensitive normalized similarity of two claim texts."""
    return SequenceMatcher(None, _oneline(a).lower(), _oneline(b).lower()).ratio()


def _has_negation(text: str) -> bool:
    words = [w.strip(".,;:!?\"'()") for w in _oneline(text).lower().split()]
    return any(w in _NEG_TOKENS or w.endswith("n't") for w in words)


def _negation_mismatch(a: str, b: str) -> bool:
    """True when exactly one of the two texts is negated — 'X applies' vs
    'X does not apply' can be near-verbatim yet opposite; such a pair may be
    a question (weak) but never an automatic merge (strong)."""
    return _has_negation(a) != _has_negation(b)


def _cosine(u: List[float], v: List[float]) -> float:
    dot = sum(x * y for x, y in zip(u, v))
    nu = sum(x * x for x in u) ** 0.5
    nv = sum(x * x for x in v) ** 0.5
    if nu == 0 or nv == 0:
        return 0.0
    return dot / (nu * nv)


def _variant_confirm(pairs: List[Dict[str, Any]], by_id: Dict[str, Dict[str, Any]],
                     llm) -> Optional[Dict[int, Dict[str, Any]]]:
    """One batched LLM call over all candidate pairs. Returns {pair_index:
    {relation, why}} or None on parse failure (caller fails open). Unlike the
    independence confirm (downgrade-only), this pass may upgrade weak→strong:
    "are these the same claim?" IS the judgment being delegated, not a guess
    about facts in the world."""
    block = "\n".join(
        f"{i}.\nA: {_oneline(by_id[p['a']]['text'])}\nB: {_oneline(by_id[p['b']]['text'])}"
        for i, p in enumerate(pairs, 1))
    prompt = _load_prompt(VARIANTS_PROMPT_FILE).replace("{PAIRS}", block)
    # Same scaling rule as infer_edges: one verdict per pair (~30 tok each),
    # so a fixed cap truncates on pair-heavy papers and the confirm fails open.
    raw = llm.call(prompt, temperature=0.0,
                   max_output_tokens=max(2048, 64 * len(pairs)))
    obj = extract_json(raw)
    if isinstance(obj, dict):
        obj = obj.get("pairs", [])
    if not isinstance(obj, list) or not obj:
        logger.warning("variants: unparseable confirm response; keeping heuristics")
        return None
    verdicts: Dict[int, Dict[str, Any]] = {}
    for item in obj:
        if not isinstance(item, dict):
            continue
        try:
            n = int(item.get("n"))
        except (TypeError, ValueError):
            continue
        rel = str(item.get("relation", "")).lower().strip()
        if 1 <= n <= len(pairs) and rel in _VARIANT_RELATIONS:
            verdicts[n] = {"relation": rel,
                           "why": _oneline(str(item.get("why", "")))[:200]}
    return verdicts or None


def find_variants(nodes: List[Dict[str, Any]],
                  embed_fn: Optional[Callable[[List[str]], List[List[float]]]] = None,
                  llm=None) -> Dict[str, Any]:
    """Detect groups of similar-but-not-identical claims among the map's nodes
    (each needs `id` + `text`; document order assumed). Returns an additive
    payload block: {groups, pairs, method}. Groups come from STRONG pairs only
    (union-find); weak pairs stay in `pairs` as open questions. `embed_fn` maps
    texts -> vectors (SPECTER at wiring time); `llm` enables the batched
    confirm pass, which can upgrade, downgrade, or kill a candidate pair."""
    nodes = [n for n in nodes if n.get("id") and _oneline(n.get("text", ""))]
    method = "lexical"
    vecs: Optional[List[List[float]]] = None
    if embed_fn is not None and nodes:
        vecs = embed_fn([_oneline(n["text"]) for n in nodes])
        method += "+embeddings"

    pairs: List[Dict[str, Any]] = []
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            lex = _lex_sim(nodes[i]["text"], nodes[j]["text"])
            # float() — real embed_fns return numpy/tensor scalars, which
            # round() rejects (found on first real-vector run, 2026-07-06).
            cos = float(_cosine(vecs[i], vecs[j])) if vecs is not None else None
            if (lex >= VARIANT_LEX_STRONG
                    and not _negation_mismatch(nodes[i]["text"], nodes[j]["text"])):
                strength = "strong"
            elif lex >= VARIANT_LEX_WEAK or (cos is not None and cos >= VARIANT_COS_WEAK):
                strength = "weak"
            else:
                continue
            pairs.append({"a": nodes[i]["id"], "b": nodes[j]["id"],
                          "strength": strength, "lex": round(lex, 3),
                          "cos": round(cos, 3) if cos is not None else None,
                          "llm": None})

    if llm is not None and pairs:
        by_id = {n["id"]: n for n in nodes}
        verdicts = _variant_confirm(pairs, by_id, llm)
        if verdicts is not None:
            method += "+llm"
            kept = []
            for i, p in enumerate(pairs, 1):
                v = verdicts.get(i)
                if v is None:
                    kept.append(p)                 # no verdict → fail-open
                    continue
                p["llm"] = v
                if v["relation"] == "different_claim":
                    continue                        # confirmed non-variant → drop
                p["strength"] = "strong"            # restatement / hedged_variant
                kept.append(p)
            pairs = kept

    # Union-find over strong pairs; canonical = earliest in document order.
    order = {n["id"]: i for i, n in enumerate(nodes)}
    parent = {n["id"]: n["id"] for n in nodes}

    def _find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for p in pairs:
        if p["strength"] != "strong":
            continue
        ra, rb = _find(p["a"]), _find(p["b"])
        if ra != rb:                                # earlier claim becomes the root
            if order[ra] <= order[rb]:
                parent[rb] = ra
            else:
                parent[ra] = rb

    members: Dict[str, List[str]] = defaultdict(list)
    for n in nodes:
        members[_find(n["id"])].append(n["id"])
    groups = []
    for root in sorted(members, key=lambda r: order[r]):
        ids = members[root]
        if len(ids) < 2:
            continue
        relations = {}
        for p in pairs:
            if p["strength"] == "strong" and p.get("llm") and _find(p["a"]) == root:
                relations[p["b"]] = p["llm"]["relation"]
        groups.append({"canonical": root,
                       "members": sorted(ids, key=lambda i: order[i]),
                       "relations": relations})

    return {"groups": groups, "pairs": pairs, "method": method,
            "n_weak_pairs": sum(1 for p in pairs if p["strength"] == "weak")}


# ---------------------------------------------------------------------------
# Map diff (slot A2) — evolution over time, pure code, zero LLM.
# ---------------------------------------------------------------------------

def diff_maps(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """Structural diff of two argument_map.json payloads (e.g. the archived
    previous run vs. the current one). Node identity = claim id — stable
    across incremental re-runs via rerun.py matching; edge identity =
    (from, to, type). Returns the argmap_diff.json payload."""
    old_nodes = {n["id"]: n for n in old.get("nodes", [])}
    new_nodes = {n["id"]: n for n in new.get("nodes", [])}
    retexted = [{"id": i,
                 "old": _oneline(old_nodes[i].get("text", "")),
                 "new": _oneline(new_nodes[i].get("text", ""))}
                for i in old_nodes.keys() & new_nodes.keys()
                if _oneline(old_nodes[i].get("text", ""))
                != _oneline(new_nodes[i].get("text", ""))]

    def _ekey(e: Dict[str, Any]) -> Tuple[str, str, str]:
        return (e.get("from"), e.get("to"), e.get("type"))

    old_edges = {_ekey(e): e for e in old.get("edges", [])}
    new_edges = {_ekey(e): e for e in new.get("edges", [])}

    old_thesis = list(old.get("thesis_ids", []))
    new_thesis = list(new.get("thesis_ids", []))
    payload = {
        "nodes_added": sorted(new_nodes.keys() - old_nodes.keys()),
        "nodes_removed": sorted(old_nodes.keys() - new_nodes.keys()),
        "nodes_retexted": sorted(retexted, key=lambda r: r["id"]),
        "edges_added": [new_edges[k] for k in sorted(new_edges.keys() - old_edges.keys())],
        "edges_removed": [old_edges[k] for k in sorted(old_edges.keys() - new_edges.keys())],
        "thesis_changed": ({"old": old_thesis, "new": new_thesis}
                           if set(old_thesis) != set(new_thesis) else None),
    }
    payload["summary"] = {k: len(payload[k]) for k in
                          ("nodes_added", "nodes_removed", "nodes_retexted",
                           "edges_added", "edges_removed")}
    return payload


def write_diff(payload: Dict[str, Any], output_dir: str) -> None:
    """Write argmap_diff.json into output_dir."""
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "argmap_diff.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
