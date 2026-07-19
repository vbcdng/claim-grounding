"""Evidence independence — detect correlated sources cited as if independent (A1).

FLF's starkest named assessment gap (docs/submission/EPISTACK_LANDSCAPE.md §7 #1): when a
claim cites N sources, the reader hears "N independent confirmations" — but the
sources may share authors, restate one underlying study, cite each other for the
very claim, or near-duplicate content. No shipped tool detects this (Rootclaim
does it manually). Full design + schema: docs/ASSESSMENT_DESIGN.md.

METHOD (cheap first, matcher's shape): free local signals (author tails parsed
from source titles) -> disk-cached Semantic Scholar metadata (canonical author
IDs, direct-citation edges, bibliographic coupling) -> optional LLM confirm over
already-flagged pairs only. Default run: $0 LLM.

STRONG/WEAK POLICY (the partial-check lesson — weak signals must not drive
counts): every flagged pair carries strength strong|weak; effective independent-
source counts and evidence clusters use STRONG pairs only; weak pairs are
surfaced as questions, never folded into arithmetic. Missing metadata is
"unknown", never a flag. S2 failures are never cached (retried next run);
matched/no_match answers are.

OUTPUT (additive, read-only on the run): independence.json next to
analysis.json. Never flips a verdict — a nudge, never a veto. Crux v2 (crux.py)
consumes per_claim effective counts as a fragility input. Viewer wiring is
queue #3, not this slot.

MERGE NOTE: the S2 reference-list fetch below duplicates ~30 lines of streamB's
paper_search.neighbors(); unify at the queue-#4 merge (same endpoint, same
no-caching-failures rule — kept separate only for slot file-disjointness).
"""
import hashlib
import json
import logging
import os
import re
import time
from itertools import combinations
from typing import Any, Callable, Dict, List, Optional, Tuple

from .llm_client import extract_json
from .semantic_scholar_api import (find_paper_by_title,
                                   load_semantic_scholar_api_key,
                                   _titles_match)

logger = logging.getLogger("papertrail.independence")

PROMPT_FILE = "pt_independence_confirm_prompt.txt"

# Thresholds from docs/ASSESSMENT_DESIGN.md — coupled ref-list / shared-content
# fractions below the weak floor are recorded but are NOT a relation.
BIB_COUPLING_STRONG = 0.5
BIB_COUPLING_WEAK = 0.2
CONTENT_OVERLAP_STRONG = 0.30
CONTENT_OVERLAP_WEAK = 0.10

SIGNAL_NAMES = ("shared_authors", "direct_citation", "bib_coupling",
                "content_overlap")

# Words a "Surname," regex can catch in freeform title tails that are not names.
_NOT_SURNAMES = {"and", "others", "et", "al", "the", "in", "of", "on", "for"}


# --------------------------------------------------------------------------
# Local signals: author surnames from the "<Title> — <Authors>" tail
# --------------------------------------------------------------------------

def split_title_tail(title: str) -> Tuple[str, str]:
    """('clean title', 'author tail') — tail is '' when there is no dash
    separator (bentonite-style filename titles). Splits on em/en dash only;
    hyphens are common inside real titles."""
    parts = re.split(r"[—–]", title or "")
    if len(parts) < 2:
        return (title or "").strip(), ""
    return parts[0].strip(), parts[-1].strip()


def parse_author_tail(title: str) -> List[str]:
    """Lowercased surnames from a '— Sastry, G. and others' style tail.
    Org tails ('— Stanford HAI') have no 'Surname,' shape and yield []."""
    _, tail = split_title_tail(title)
    surnames = []
    for m in re.finditer(r"([A-Z][\w'\-]+)\s*,", tail):
        s = m.group(1).lower()
        if s not in _NOT_SURNAMES:
            surnames.append(s)
    return sorted(set(surnames))


# --------------------------------------------------------------------------
# S2 enrichment (injectable + disk-cached; failures never cached)
# --------------------------------------------------------------------------

def _default_s2_lookup(title: str) -> Dict[str, Any]:
    """{'status': matched|no_match|search_failed, 'paper': dict|None}."""
    paper, status = find_paper_by_title(title)
    return {"status": status, "paper": paper}


def _default_s2_refs(paper_id: str, max_retries: int = 3) -> Dict[str, Any]:
    """One paper's reference list: {'status': ok|failed, 'refs': [...]|None}.
    Each ref: {paperId, title, doi}."""
    import random

    import requests
    url = f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}/references"
    params = {"fields": "paperId,title,externalIds", "limit": 1000}
    headers = {}
    key = load_semantic_scholar_api_key()
    if key:
        headers["x-api-key"] = key
    for attempt in range(max_retries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=30)
            if r.status_code == 429 and attempt < max_retries - 1:
                wait = (2 ** attempt) * 5 + random.uniform(0, 1)
                logger.warning("S2 refs rate limit (429); waiting %.1fs", wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            refs = []
            # S2 sends an explicit "data": null on some matched papers —
            # .get(..., []) doesn't catch that (paper1 live run, 2026-07-06).
            for item in r.json().get("data") or []:
                cited = item.get("citedPaper") or {}
                ext = cited.get("externalIds") or {}
                refs.append({"paperId": cited.get("paperId"),
                             "title": cited.get("title"),
                             "doi": ext.get("DOI")})
            return {"status": "ok", "refs": refs}
        except Exception as e:
            logger.warning("S2 refs fetch failed for %s: %s", paper_id, e)
            return {"status": "failed", "refs": None}
    return {"status": "failed", "refs": None}


def _cache_path(cache_dir: Optional[str], kind: str, key: str) -> Optional[str]:
    if not cache_dir:
        return None
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return os.path.join(cache_dir, f"{kind}_{h}.json")


def _with_cache(path: Optional[str], compute: Callable[[], Dict[str, Any]],
                cacheable: Callable[[Dict[str, Any]], bool]) -> Dict[str, Any]:
    """Disk-cache one lookup. Determinate answers (matched / no_match / ok) are
    cached; transient failures are not, so the next run retries them."""
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("independence cache read failed (%s); refetching", e)
    result = compute()
    if path and cacheable(result):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
    return result


def _enrich_source(src: Dict[str, Any], cache_dir: Optional[str],
                   s2_lookup: Callable, s2_refs: Callable) -> Optional[Dict[str, Any]]:
    """One source's S2 record {paper_id, authors, year, n_refs, refs} or None
    (absent from S2 / lookup failed — 'unknown', never a flag)."""
    clean_title, _ = split_title_tail(src.get("title") or "")
    if not clean_title:
        return None
    lookup = _with_cache(
        _cache_path(cache_dir, "s2lookup", clean_title.lower()),
        lambda: s2_lookup(clean_title),
        lambda r: r.get("status") in ("matched", "no_match"))
    paper = lookup.get("paper")
    if lookup.get("status") != "matched" or not paper:
        return None
    pid = paper.get("paperId")
    refs = None
    if pid:
        got = _with_cache(
            _cache_path(cache_dir, "s2refs", pid),
            lambda: s2_refs(pid),
            lambda r: r.get("status") == "ok")
        if got.get("status") == "ok":
            refs = got.get("refs")
    return {"paper_id": pid,
            "authors": [{"id": a.get("authorId"), "name": a.get("name")}
                        for a in (paper.get("authors") or [])],
            "year": paper.get("year"),
            "doi": (paper.get("externalIds") or {}).get("DOI"),
            "n_refs": len(refs) if refs is not None else None,
            "refs": refs}


# --------------------------------------------------------------------------
# Pairwise signals — each returns None (unknown) or {'level': ..., details}
# --------------------------------------------------------------------------

def _sig_shared_authors(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Strong: S2 author-ID intersection. Weak: local surname match, only when
    S2 can't rule (a spurious surname match is suppressed when both papers have
    S2 author lists that are disjoint)."""
    sa, sb = a.get("s2") or {}, b.get("s2") or {}
    ids_a = {x["id"]: x["name"] for x in sa.get("authors", []) if x.get("id")}
    ids_b = {x["id"]: x["name"] for x in sb.get("authors", []) if x.get("id")}
    if ids_a and ids_b:
        common = sorted(set(ids_a) & set(ids_b))
        if common:
            return {"level": "strong", "ids": common,
                    "names": sorted({ids_a[i] for i in common})}
        return None  # S2 says disjoint — don't fall back to surnames
    common_names = sorted(set(a.get("authors_local", [])) &
                          set(b.get("authors_local", [])))
    if common_names:
        return {"level": "weak", "surnames": common_names}
    return None


def _refs_contain(refs: List[Dict[str, Any]], s2: Dict[str, Any],
                  clean_title: str) -> bool:
    pid, doi = s2.get("paper_id"), s2.get("doi")
    for r in refs:
        if pid and r.get("paperId") == pid:
            return True
        if doi and r.get("doi") and r["doi"].lower() == doi.lower():
            return True
        if clean_title and _titles_match(clean_title, r.get("title")):
            return True
    return False


def _sig_direct_citation(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One source's S2 reference list contains the other (paperId/DOI/fuzzy
    title). Always strong when found; None when neither ref list is known."""
    sa, sb = a.get("s2") or {}, b.get("s2") or {}
    refs_a, refs_b = sa.get("refs"), sb.get("refs")
    title_a, _ = split_title_tail(a.get("title") or "")
    title_b, _ = split_title_tail(b.get("title") or "")
    directions = []
    if refs_a and sb and _refs_contain(refs_a, sb, title_b):
        directions.append("a_cites_b")
    if refs_b and sa and _refs_contain(refs_b, sa, title_a):
        directions.append("b_cites_a")
    if directions:
        return {"level": "strong", "directions": directions}
    if refs_a is None and refs_b is None:
        return None
    return None


def _ref_id_set(refs: List[Dict[str, Any]]) -> set:
    ids = set()
    for r in refs:
        if r.get("paperId"):
            ids.add(("pid", r["paperId"]))
        elif r.get("doi"):
            ids.add(("doi", r["doi"].lower()))
    return ids


def _sig_bib_coupling(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Shared-references ratio over the smaller reference list. Recorded
    whenever both lists are known; a relation only at level weak/strong."""
    refs_a = (a.get("s2") or {}).get("refs")
    refs_b = (b.get("s2") or {}).get("refs")
    if refs_a is None or refs_b is None:
        return None
    ids_a, ids_b = _ref_id_set(refs_a), _ref_id_set(refs_b)
    smaller = min(len(ids_a), len(ids_b))
    if smaller == 0:
        return None
    shared = len(ids_a & ids_b)
    ratio = shared / smaller
    level = ("strong" if ratio >= BIB_COUPLING_STRONG
             else "weak" if ratio >= BIB_COUPLING_WEAK else "none")
    return {"level": level, "ratio": round(ratio, 3), "shared": shared}


def _sig_content_overlap(dedup: Optional[Dict[str, Any]], key_a: str,
                         key_b: str) -> Optional[Dict[str, Any]]:
    """From slot A4's dedup.json (clusters of cross-source duplicate claims):
    fraction of the smaller source's clustered claims that share a cluster with
    the other source. None until A4 lands or when the payload has no clusters."""
    if not dedup:
        return None
    clusters = dedup.get("clusters")
    if not isinstance(clusters, list):
        return None

    def _key_of(item):
        if isinstance(item, dict):
            return item.get("source") or item.get("source_key") or item.get("key")
        if isinstance(item, (list, tuple)) and item:
            return item[0]
        return None

    total = {key_a: 0, key_b: 0}
    shared = {key_a: 0, key_b: 0}
    seen_any = False
    for cluster in clusters:
        if not isinstance(cluster, list):
            continue
        keys_here = [k for k in (_key_of(i) for i in cluster) if k]
        na = keys_here.count(key_a)
        nb = keys_here.count(key_b)
        if na:
            total[key_a] += na
            seen_any = True
        if nb:
            total[key_b] += nb
            seen_any = True
        if na and nb:
            shared[key_a] += na
            shared[key_b] += nb
    if not seen_any or not total[key_a] or not total[key_b]:
        return None
    # Denominator = each source's TOTAL claim count (dedup.json's n_claims), not
    # the number of claims that happened to land in a cluster. Normalizing by the
    # clustered count saturates the ratio to ~1.0 whenever a source contributes
    # only a handful of claims to clusters — a source with 3 clustered claims all
    # shared with B scores 3/3 "strong" even if it has 100 claims (A4 flagged this
    # at merge). Fall back to the clustered count only when n_claims is absent
    # (older dedup.json without the field).
    n_claims = dedup.get("n_claims")
    n_claims = n_claims if isinstance(n_claims, dict) else {}
    denom = {k: (n_claims.get(k) or total[k]) for k in (key_a, key_b)}
    smaller = key_a if denom[key_a] <= denom[key_b] else key_b
    if not denom[smaller]:
        return None
    ratio = min(shared[smaller] / denom[smaller], 1.0)
    level = ("strong" if ratio >= CONTENT_OVERLAP_STRONG
             else "weak" if ratio >= CONTENT_OVERLAP_WEAK else "none")
    return {"level": level, "ratio": round(ratio, 3)}


def _pair_verdict(signals: Dict[str, Optional[Dict[str, Any]]]) -> Tuple[List[str], Optional[str]]:
    """(relations, strength) — relations are the signals at weak+; strength is
    the max level across them (None when nothing flags)."""
    relations, strength = [], None
    for name in SIGNAL_NAMES:
        sig = signals.get(name)
        if not sig or sig.get("level") not in ("strong", "weak"):
            continue
        relations.append(name)
        if sig["level"] == "strong":
            strength = "strong"
        elif strength is None:
            strength = "weak"
    return relations, strength


def _pair_why(a: Dict[str, Any], b: Dict[str, Any],
              signals: Dict[str, Any], relations: List[str]) -> str:
    bits = []
    for name in relations:
        sig = signals[name]
        if name == "shared_authors":
            who = ", ".join(sig.get("names") or sig.get("surnames") or [])
            bits.append(f"shared author(s): {who}" if sig["level"] == "strong"
                        else f"same surname(s): {who} — same team?")
        elif name == "direct_citation":
            d = sig["directions"][0]
            first, second = (a, b) if d == "a_cites_b" else (b, a)
            bits.append(f"'{split_title_tail(first.get('title') or '')[0][:60]}' "
                        f"cites '{split_title_tail(second.get('title') or '')[0][:60]}'")
        elif name == "bib_coupling":
            bits.append(f"{int(sig['ratio'] * 100)}% shared references")
        elif name == "content_overlap":
            bits.append(f"{int(sig['ratio'] * 100)}% duplicated content")
    return "; ".join(bits).capitalize() if bits else ""


# --------------------------------------------------------------------------
# LLM confirm (tier 2, optional, fail-open; may downgrade, never invents)
# --------------------------------------------------------------------------

def _load_prompt() -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "config", "prompts", PROMPT_FILE),
              "r", encoding="utf-8") as f:
        return f.read()


def _describe_source(s: Dict[str, Any]) -> str:
    s2 = s.get("s2") or {}
    authors = (", ".join(a["name"] for a in s2.get("authors", []) if a.get("name"))
               or ", ".join(s.get("authors_local", [])) or "unknown authors")
    year = s2.get("year") or "year unknown"
    return f"{split_title_tail(s.get('title') or '')[0]} ({authors}; {year})"


def confirm_pair(pair: Dict[str, Any], by_key: Dict[str, Dict[str, Any]],
                 llm, prompt_template: Optional[str] = None) -> Dict[str, Any]:
    """One LLM pass over a flagged pair. An explicit 'independent' verdict
    downgrades strong->weak (so it leaves the arithmetic); a parse failure
    keeps the heuristic flag with llm=null (fail-open). prompt_template lets
    the caller load the prompt file once for the whole batch."""
    prompt = ((prompt_template or _load_prompt())
              .replace("{PAPER_A}", _describe_source(by_key[pair["a"]]))
              .replace("{PAPER_B}", _describe_source(by_key[pair["b"]]))
              .replace("{SIGNALS}", pair.get("why") or ", ".join(pair["relations"])))
    pair = dict(pair)
    try:
        raw = llm.call(prompt, temperature=0.0, max_output_tokens=512)
        obj = extract_json(raw)
    except Exception as e:
        logger.warning("independence confirm failed for (%s,%s): %s",
                       pair["a"], pair["b"], e)
        obj = None
    if not isinstance(obj, dict) or "independent" not in obj:
        pair["llm"] = None
        return pair
    pair["llm"] = {"relation": str(obj.get("relation", ""))[:40],
                   "independent": bool(obj["independent"]),
                   "why": re.sub(r"\s+", " ", str(obj.get("why", "")))[:200]}
    if pair["llm"]["independent"] and pair["strength"] == "strong":
        pair["strength"] = "weak"
    return pair


# --------------------------------------------------------------------------
# Aggregation: strong-pair clusters + per-claim effective counts
# --------------------------------------------------------------------------

def _clusters(keys: List[str], pairs: List[Dict[str, Any]]) -> List[List[str]]:
    """Connected components over STRONG pairs; singletons included."""
    parent = {k: k for k in keys}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for p in pairs:
        if p["strength"] == "strong" and p["a"] in parent and p["b"] in parent:
            ra, rb = find(p["a"]), find(p["b"])
            if ra != rb:
                parent[rb] = ra
    groups: Dict[str, List[str]] = {}
    for k in keys:
        groups.setdefault(find(k), []).append(k)
    return sorted((sorted(g) for g in groups.values()),
                  key=lambda g: (-len(g), g[0]))


def _cited_keys(analysis: Dict[str, Any]) -> Dict[str, List[str]]:
    """claim id -> cited citation KEYS. Claims store paper_ids (sha1 of the
    source filename) while everything in this module — pairs, clusters,
    sources — is keyed by citation key, so the per-claim arithmetic must
    translate via the analysis 'sources' table. (Identity fallback: an id
    with no table entry passes through, so key-shaped fixtures still work.)"""
    key_of = {s.get("paper_id"): s.get("key")
              for s in analysis.get("sources", []) if s.get("key")}
    out = {}
    for c in analysis.get("text_claims", []):
        cited = [key_of.get(pid, pid) for pid in (c.get("paper_ids") or [])]
        out[c["id"]] = list(dict.fromkeys(k for k in cited if k))
    return out


def _per_claim(analysis: Dict[str, Any], pairs: List[Dict[str, Any]],
               clusters: List[List[str]]) -> Dict[str, Any]:
    cluster_of = {k: i for i, g in enumerate(clusters) for k in g}
    flagged = {}
    for p in pairs:
        flagged[frozenset((p["a"], p["b"]))] = p
    out = {}
    for cid, cited in _cited_keys(analysis).items():
        if len(cited) < 2:
            continue
        eff = len({cluster_of[k] for k in cited if k in cluster_of})
        cpairs = [sorted(fs) for fs in
                  (frozenset(pr) for pr in combinations(sorted(cited), 2))
                  if fs in flagged]
        out[cid] = {"cited": len(cited),
                    "effective": eff or len(cited),
                    "flagged_pairs": sorted(cpairs)}
    return out


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------

def assess_independence(analysis: Dict[str, Any], s2_enrich: bool = True,
                        llm=None, cache_dir: Optional[str] = None,
                        dedup: Optional[Dict[str, Any]] = None,
                        s2_lookup: Optional[Callable] = None,
                        s2_refs: Optional[Callable] = None) -> Dict[str, Any]:
    """Build the independence.json payload from a finished analysis dict.
    Read-only on the run; never touches a verdict. See module docstring."""
    s2_lookup = s2_lookup or _default_s2_lookup
    s2_refs = s2_refs or _default_s2_refs

    sources = []
    for src in analysis.get("sources", []):
        key = src.get("key")
        if not key:
            continue
        rec = {"key": key, "title": src.get("title") or "",
               "authors_local": parse_author_tail(src.get("title") or ""),
               "s2": None}
        if s2_enrich:
            rec["s2"] = _enrich_source(src, cache_dir, s2_lookup, s2_refs)
        sources.append(rec)
    by_key = {s["key"]: s for s in sources}

    pairs = []
    n_weak = 0
    for a, b in combinations(sources, 2):
        signals = {
            "shared_authors": _sig_shared_authors(a, b),
            "direct_citation": _sig_direct_citation(a, b),
            "bib_coupling": _sig_bib_coupling(a, b),
            "content_overlap": _sig_content_overlap(dedup, a["key"], b["key"]),
        }
        relations, strength = _pair_verdict(signals)
        if not relations:
            continue
        pairs.append({"a": a["key"], "b": b["key"], "relations": relations,
                      "strength": strength, "signals": signals,
                      "why": _pair_why(a, b, signals, relations), "llm": None})

    model = prompt_sha = None
    if llm and pairs:
        co_cited = set()
        for cited in _cited_keys(analysis).values():
            if len(cited) >= 2:
                co_cited.update(frozenset(p) for p in combinations(cited, 2))
        # Load the prompt once and confirm the co-cited pairs in parallel —
        # they are independent calls (was: strictly serial, one file read each).
        prompt_template = _load_prompt()
        todo = [p for p in pairs if frozenset((p["a"], p["b"])) in co_cited]
        if todo:
            from .llm_client import parallel_map
            confirmed = parallel_map(
                lambda p: confirm_pair(p, by_key, llm, prompt_template),
                todo, workers=4)
            replaced = {id(p): c for p, c in zip(todo, confirmed)}
            pairs = [replaced.get(id(p), p) for p in pairs]
        model = getattr(llm, "model", None)
        prompt_sha = hashlib.sha1(prompt_template.encode("utf-8")).hexdigest()[:8]

    n_weak = sum(1 for p in pairs if p["strength"] == "weak")
    clusters = _clusters([s["key"] for s in sources], pairs)
    per_claim = _per_claim(analysis, pairs, clusters)

    method = "local" + ("+s2" if s2_enrich else "")
    if dedup:
        method += "+dedup"
    if llm:
        method += "+llm"
    slim_sources = []
    for s in sources:
        s2 = s["s2"]
        slim_sources.append({
            "key": s["key"], "title": s["title"],
            "authors_local": s["authors_local"],
            "s2": None if not s2 else {k: v for k, v in s2.items() if k != "refs"}})
    return {"sources": slim_sources, "pairs": pairs, "clusters": clusters,
            "per_claim": per_claim,
            "summary": {"n_sources": len(sources), "n_clusters": len(clusters),
                        "n_weak_pairs": n_weak},
            "method": method, "model": model, "prompt_sha": prompt_sha}


def write_independence(payload: Dict[str, Any], output_dir: str) -> None:
    """Write independence.json into output_dir."""
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "independence.json"), "w",
              encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI: assess a finished run dir. $0 LLM always; --no-s2 for fully-offline."""
    import argparse
    parser = argparse.ArgumentParser(
        description="Evidence-independence assessment over a finished run "
                    "(writes independence.json; never changes verdicts).")
    parser.add_argument("run_dir", help="output dir of a finished run "
                                        "(contains analysis.json)")
    parser.add_argument("--no-s2", action="store_true",
                        help="skip Semantic Scholar enrichment (fully offline)")
    args = parser.parse_args(argv)

    path = os.path.join(args.run_dir, "analysis.json")
    if not os.path.exists(path):
        print(f"No analysis.json in {args.run_dir}")
        return 1
    with open(path, "r", encoding="utf-8") as f:
        analysis = json.load(f)
    dedup = None
    dpath = os.path.join(args.run_dir, "dedup.json")
    if os.path.exists(dpath):
        with open(dpath, "r", encoding="utf-8") as f:
            dedup = json.load(f)
    payload = assess_independence(
        analysis, s2_enrich=not args.no_s2, dedup=dedup,
        cache_dir=os.path.join(args.run_dir, "independence_cache"))
    write_independence(payload, args.run_dir)
    s = payload["summary"]
    print(f"independence.json written: {s['n_sources']} sources -> "
          f"{s['n_clusters']} independent clusters, "
          f"{len(payload['pairs'])} flagged pair(s) ({s['n_weak_pairs']} weak), "
          f"{len(payload['per_claim'])} multi-citation claim(s) annotated")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
