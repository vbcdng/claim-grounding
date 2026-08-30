#!/usr/bin/env python3
"""
Retrieval probe: BM25 + SPECTER (+ small-model bake-off) candidate-retrieval
recall, measured against the 3 hand-audited papers.

Spec: IDEAS.md "TODO -- Prototype BM25+SPECTER hybrid candidate retrieval +
re-run the recall probe" (2026-07-06 entry). Recipe: for every ground-truth
SUPPORTED claim, locate its gold evidence sentence (the exact sentence text
logged in analysis.json's per-claim `evidences`, for the cited source that
evidence resolved against), then compute that sentence's RANK among the
cited source's full sentence list under each retrieval method. Metric:
top-3 / top-6 recall (gold sentence rank <= N) + rank distribution (median,
etc.), per paper and pooled.

This is a read-only probe: it never touches matcher.py or any run directory.
It only reads existing analysis.json / source_claims / embeddings caches from
finished runs, and writes its OWN encoding caches under
benchmarks/retrieval_probe/cache/.

OFFLINE / $0: zero LLM calls. Local sentence-embedding-model inference
(SPECTER, already used by the tool; plus three small bake-off models,
network-downloaded once and then disk-cached) is not an LLM call.

Usage:
  venv/bin/python benchmarks/retrieval_probe/probe.py
  venv/bin/python benchmarks/retrieval_probe/probe.py --bake-off
  venv/bin/python benchmarks/retrieval_probe/probe.py --bake-off --dump-worst 5
  venv/bin/python benchmarks/retrieval_probe/probe.py --json-out results.json
"""

import argparse
import difflib
import hashlib
import json
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from modules.papertrail.matcher import _norm, _loose_text  # noqa: E402  (reuse the tool's own text-normalization convention, read-only import)

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "cache")

# ---------------------------------------------------------------------------
# Paper registry: ground truth + the finished run dir picked for each paper.
# Picked as "most recent complete run dir" (analysis.json + embeddings/*.sents.npz
# + source_claims/*.json all present) matching the paper's canonical name. All
# three are the 2026-07-12 "_verification" runs -- the most recent complete runs
# for all three hand-audited papers as of 2026-07-17 (see report doc for the
# directory listing that led to this pick).
# ---------------------------------------------------------------------------
PAPERS = {
    "paper1": {
        "ground_truth": os.path.join(ROOT, "benchmarks/paper1_ground_truth.json"),
        "run_dir": os.path.join(ROOT, "data/paper1_verification"),
    },
    "bentonite": {
        "ground_truth": os.path.join(ROOT, "benchmarks/bentonite_ground_truth.json"),
        "run_dir": os.path.join(ROOT, "data/bentonite_verification"),
    },
    "chimpanzee": {
        "ground_truth": os.path.join(ROOT, "benchmarks/chimpanzee_ground_truth.json"),
        "run_dir": os.path.join(ROOT, "data/chimp_verification"),
    },
}

SPECTER_MODEL = "sentence-transformers/allenai-specter"

# Small-model bake-off registry. e5 needs the "query: " / "passage: " prefix
# convention (asymmetric encoder); the others are symmetric, no prefix.
BAKEOFF_MODELS = {
    "bge-small": {"name": "BAAI/bge-small-en-v1.5", "query_prefix": "", "passage_prefix": ""},
    "e5-small": {"name": "intfloat/e5-small-v2", "query_prefix": "query: ", "passage_prefix": "passage: "},
    "gte-small": {"name": "thenlper/gte-small", "query_prefix": "", "passage_prefix": ""},
}

RRF_K = 60  # per IDEAS.md spec ("k~=60")


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Probe item collection
# ---------------------------------------------------------------------------

def collect_items(paper_key, cfg, skipped, unresolved):
    """Build probe items for one paper: one item per (claim, cited source) pair
    where the ground truth expects 'supported' and the tool's evidence for that
    source is itself marked supported=true (i.e. a locatable gold sentence)."""
    gt = load_json(cfg["ground_truth"])
    analysis = load_json(os.path.join(cfg["run_dir"], "analysis.json"))
    by_id = {c["id"]: c for c in analysis["text_claims"]}
    items = []
    for c in gt["claims"]:
        if c.get("expect") != "supported":
            continue
        cid = c["id"]
        tc = by_id.get(cid)
        if tc is None:
            skipped.append((paper_key, cid, "gt id not found in analysis.json text_claims"))
            continue
        if tc.get("verdict") != "supported":
            skipped.append((paper_key, cid, f"tool verdict='{tc.get('verdict')}' in this run (not supported)"))
            continue
        evs = [e for e in (tc.get("evidences") or []) if e.get("supported")]
        if not evs:
            skipped.append((paper_key, cid, "verdict=supported but no evidence entry has supported=true"))
            continue
        for e in evs:
            items.append({
                "paper": paper_key,
                "claim_id": cid,
                "claim_text": tc["text"],
                "pid": e["paper_id"],
                "source_title": e.get("source_title", ""),
                "gold_sentence": e["sentence"],
            })
    return items


def locate_gold_index(gold_sentence, sent_texts, sent_texts_norm, sent_texts_loose):
    """Find the gold sentence's index in a source's sentence list. Tries exact,
    then whitespace/case-normalized, then punctuation-insensitive ('loose'),
    then a fuzzy best-match fallback. Returns (index_or_None, match_kind)."""
    if gold_sentence in sent_texts:
        return sent_texts.index(gold_sentence), "exact"
    ng = _norm(gold_sentence)
    if ng in sent_texts_norm:
        return sent_texts_norm.index(ng), "normalized"
    lg = _loose_text(gold_sentence)
    if lg and lg in sent_texts_loose:
        return sent_texts_loose.index(lg), "loose"
    # Fuzzy fallback -- only reached for the rare non-membership case.
    best_i, best_r = -1, 0.0
    for i, t in enumerate(sent_texts_loose):
        if not t:
            continue
        # Cheap length prefilter before the expensive SequenceMatcher call.
        if abs(len(t) - len(lg)) > max(20, 0.6 * len(lg)):
            continue
        r = difflib.SequenceMatcher(None, lg, t).ratio()
        if r > best_r:
            best_r, best_i = r, i
    if best_r >= 0.85:
        return best_i, f"fuzzy:{best_r:.2f}"
    return None, "not_found"


# ---------------------------------------------------------------------------
# BM25 (pure python, inverted-index Okapi BM25 -- no new dependency)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text):
    return _TOKEN_RE.findall((text or "").lower())


class BM25:
    """Classic Okapi BM25 over one source's sentence list."""

    def __init__(self, docs_tokens, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.N = len(docs_tokens)
        self.doc_lens = [len(d) for d in docs_tokens]
        self.avgdl = (sum(self.doc_lens) / self.N) if self.N else 0.0
        postings = defaultdict(list)
        df = Counter()
        for i, doc in enumerate(docs_tokens):
            tf = Counter(doc)
            for t, f in tf.items():
                postings[t].append((i, f))
                df[t] += 1
        self.postings = postings
        self.idf = {t: math.log(1 + (self.N - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def scores(self, query_tokens):
        out = [0.0] * self.N
        for t in set(query_tokens):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i, f in self.postings[t]:
                dl = self.doc_lens[i] or 1
                denom = f + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                out[i] += idf * f * (self.k1 + 1) / denom
        return out


# ---------------------------------------------------------------------------
# Embedding models: SPECTER via the run dir's own cached sentence vectors
# (exact reproduction of what the tool used), small bake-off models via a
# fresh disk cache local to this probe.
# ---------------------------------------------------------------------------

_ST_MODEL_CACHE = {}
_encode_time_totals = defaultdict(float)  # model_name -> total wall-clock encode seconds (cache misses only)


def get_st_model(model_name):
    if model_name not in _ST_MODEL_CACHE:
        from sentence_transformers import SentenceTransformer
        try:
            m = SentenceTransformer(model_name, local_files_only=True)
        except Exception:
            m = SentenceTransformer(model_name)
        _ST_MODEL_CACHE[model_name] = m
    return _ST_MODEL_CACHE[model_name]


def _cache_key(model_name, prefix, texts):
    h = hashlib.sha1()
    h.update(model_name.encode("utf-8"))
    h.update(b"\x1e")
    h.update(prefix.encode("utf-8"))
    for t in texts:
        h.update(b"\x1f")
        h.update(t.encode("utf-8", "ignore"))
    return h.hexdigest()


def encode_cached(model_name, texts, cache_path, prefix=""):
    """Encode texts with model_name (optionally prefixed, for e5), cached to
    cache_path as float16 .npz keyed by content hash. Returns (float32 ndarray,
    wall_clock_seconds_spent_encoding -- 0.0 on a cache hit)."""
    import numpy as np

    key = _cache_key(model_name, prefix, texts)
    if cache_path and os.path.exists(cache_path):
        try:
            with np.load(cache_path, allow_pickle=False) as z:
                if str(z["key"]) == key:
                    return z["vecs"].astype("float32"), 0.0
        except Exception:
            pass
    model = get_st_model(model_name)
    prefixed = [prefix + t for t in texts] if prefix else list(texts)
    t0 = time.time()
    vecs = model.encode(prefixed, show_progress_bar=False, convert_to_numpy=True, batch_size=64)
    dt = time.time() - t0
    _encode_time_totals[model_name] += dt
    if cache_path:
        os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
        tmp = f"{cache_path}.{os.getpid()}.tmp.npz"
        np.savez(tmp, key=key, vecs=vecs.astype("float16"))
        os.replace(tmp, cache_path)
    return vecs.astype("float32"), dt


def load_specter_sent_vecs(run_dir, pid):
    """Load the run's own cached SPECTER sentence vectors verbatim (exact
    reproduction of what matcher.py computed for that run)."""
    import numpy as np
    path = os.path.join(run_dir, "embeddings", f"{pid}.sents.npz")
    with np.load(path, allow_pickle=False) as z:
        return z["vecs"].astype("float32")


def cos_sim_matrix(a, b):
    """a: (n,d), b: (m,d) -> (n,m) cosine similarity, numpy only (no torch
    dependency needed for this simple case)."""
    import numpy as np
    an = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-12)
    bn = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-12)
    return an @ bn.T


# ---------------------------------------------------------------------------
# Rank / fusion helpers
# ---------------------------------------------------------------------------

def ranks_from_scores(scores):
    """1-indexed rank per element from descending score order (rank 1 = best).
    Ties broken by original index (stable) -- irrelevant to recall metrics."""
    order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    r = [0] * len(scores)
    for pos, i in enumerate(order):
        r[i] = pos + 1
    return r


def rrf_fuse(scores_a, scores_b, k=RRF_K):
    ra, rb = ranks_from_scores(scores_a), ranks_from_scores(scores_b)
    return [1.0 / (k + ra[i]) + 1.0 / (k + rb[i]) for i in range(len(scores_a))]


def maxnorm_fuse(scores_a, scores_b):
    """CombMAX: min-max normalize each score list to [0,1], fused = max of the two."""
    def mm(vals):
        lo, hi = min(vals), max(vals)
        if hi - lo < 1e-12:
            return [0.0] * len(vals)
        return [(v - lo) / (hi - lo) for v in vals]
    na, nb = mm(scores_a), mm(scores_b)
    return [max(na[i], nb[i]) for i in range(len(scores_a))]


def zsum_fuse(scores_a, scores_b):
    """CombSUM of z-scored (mean 0, std 1) score lists."""
    def z(vals):
        n = len(vals)
        mean = sum(vals) / n if n else 0.0
        var = sum((v - mean) ** 2 for v in vals) / n if n else 0.0
        std = math.sqrt(var) or 1.0
        return [(v - mean) / std for v in vals]
    za, zb = z(scores_a), z(scores_b)
    return [za[i] + zb[i] for i in range(len(scores_a))]


# ---------------------------------------------------------------------------
# Per-source index (lazily built, cached across items sharing a source)
# ---------------------------------------------------------------------------

class SourceIndex:
    def __init__(self, paper_key, run_dir, pid):
        self.paper_key = paper_key
        self.run_dir = run_dir
        self.pid = pid
        src = load_json(os.path.join(run_dir, "source_claims", f"{pid}.json"))
        self.sentences = [s.get("text", "") for s in src["sentences"]]
        self.norm = [_norm(t) for t in self.sentences]
        self.loose = [_loose_text(t) for t in self.sentences]
        self.n = len(self.sentences)
        self._bm25 = None
        self._specter_vecs = None
        self._bakeoff_vecs = {}  # model_key -> (n,d) float32
        self._bm25_scores_cache = {}  # gold claim_text -> scores list (per-query, not cacheable across queries; kept small)

    @property
    def bm25(self):
        if self._bm25 is None:
            self._bm25 = BM25([_tokenize(t) for t in self.sentences])
        return self._bm25

    @property
    def specter_vecs(self):
        if self._specter_vecs is None:
            self._specter_vecs = load_specter_sent_vecs(self.run_dir, self.pid)
            if self._specter_vecs.shape[0] != self.n:
                raise RuntimeError(
                    f"{self.pid}: cached SPECTER vecs ({self._specter_vecs.shape[0]}) "
                    f"!= sentence count ({self.n})")
        return self._specter_vecs

    def bakeoff_vecs(self, model_key):
        if model_key not in self._bakeoff_vecs:
            spec = BAKEOFF_MODELS[model_key]
            cache_path = os.path.join(CACHE_DIR, f"{model_key}__{self.pid}.sents.npz")
            vecs, dt = encode_cached(spec["name"], self.sentences, cache_path, prefix=spec["passage_prefix"])
            self._bakeoff_vecs[model_key] = vecs
        return self._bakeoff_vecs[model_key]


# ---------------------------------------------------------------------------
# Main probe run
# ---------------------------------------------------------------------------

def run_probe(methods, do_bakeoff, dump_worst):
    skipped = []
    unresolved = []
    all_items = []
    for paper_key, cfg in PAPERS.items():
        all_items.extend(collect_items(paper_key, cfg, skipped, unresolved))

    src_cache = {}  # (paper_key, pid) -> SourceIndex

    def get_src(paper_key, pid):
        key = (paper_key, pid)
        if key not in src_cache:
            src_cache[key] = SourceIndex(paper_key, PAPERS[paper_key]["run_dir"], pid)
        return src_cache[key]

    # method_key -> list of per-item result dicts
    results = defaultdict(list)

    # Encode all claim texts per (paper, model) once for the query side.
    bake_query_cache = {}  # (model_key, paper_key) -> {claim_text: vec}

    for item in all_items:
        si = get_src(item["paper"], item["pid"])
        gi, kind = locate_gold_index(item["gold_sentence"], si.sentences, si.norm, si.loose)
        if gi is None:
            unresolved.append((item["paper"], item["claim_id"], item["pid"], item["gold_sentence"][:80]))
            continue
        item = dict(item, gold_idx=gi, match_kind=kind, n_sentences=si.n)

        # --- SPECTER ---
        # Claim-text vectors are cheap (one query at a time, ~36 items total
        # pooled) so no disk cache is used for them; the EXPENSIVE side
        # (thousands of sentences per source) is what's cached via SourceIndex,
        # straight from the run's own embeddings/{pid}.sents.npz.
        specter_vec, _ = encode_cached(SPECTER_MODEL, [item["claim_text"]], None)
        cos_scores = cos_sim_matrix(specter_vec, si.specter_vecs)[0].tolist()
        results["specter"].append(_score_item(item, cos_scores))

        bm25_scores = si.bm25.scores(_tokenize(item["claim_text"]))
        if "bm25" in methods:
            results["bm25"].append(_score_item(item, bm25_scores))

        if "rrf" in methods:
            fused = rrf_fuse(cos_scores, bm25_scores)
            results["specter_bm25_rrf"].append(_score_item(item, fused))
        if "maxnorm" in methods:
            fused = maxnorm_fuse(cos_scores, bm25_scores)
            results["specter_bm25_maxnorm"].append(_score_item(item, fused))
        if "zsum" in methods:
            fused = zsum_fuse(cos_scores, bm25_scores)
            results["specter_bm25_zsum"].append(_score_item(item, fused))

        if do_bakeoff:
            for model_key, spec in BAKEOFF_MODELS.items():
                bq_cache = bake_query_cache.setdefault((model_key, item["paper"]), {})
                if item["claim_text"] not in bq_cache:
                    qvecs, _ = encode_cached(spec["name"], [item["claim_text"]], None, prefix=spec["query_prefix"])
                    bq_cache[item["claim_text"]] = qvecs[0]
                qvec = bq_cache[item["claim_text"]][None, :]
                svecs = si.bakeoff_vecs(model_key)
                bscores = cos_sim_matrix(qvec, svecs)[0].tolist()
                results[model_key].append(_score_item(item, bscores))
                fused = rrf_fuse(bscores, bm25_scores)
                results[f"{model_key}_bm25_rrf"].append(_score_item(item, fused))

    return all_items, results, skipped, unresolved


def _score_item(item, scores):
    ranks = ranks_from_scores(scores)
    rank = ranks[item["gold_idx"]]
    return {
        "paper": item["paper"], "claim_id": item["claim_id"], "pid": item["pid"],
        "source_title": item["source_title"], "n_sentences": item["n_sentences"],
        "match_kind": item["match_kind"], "rank": rank,
    }


def summarize(method_results):
    ranks = [r["rank"] for r in method_results]
    n = len(ranks)
    if n == 0:
        return {"n": 0}
    top3 = sum(1 for r in ranks if r <= 3)
    top6 = sum(1 for r in ranks if r <= 6)
    sr = sorted(ranks)
    median = sr[n // 2] if n % 2 == 1 else (sr[n // 2 - 1] + sr[n // 2]) / 2
    return {
        "n": n, "top3_recall": top3 / n, "top6_recall": top6 / n,
        "top3": top3, "top6": top6, "median_rank": median,
        "max_rank": max(ranks), "ranks": ranks,
    }


def summarize_by_paper(method_results):
    by_paper = defaultdict(list)
    for r in method_results:
        by_paper[r["paper"]].append(r)
    return {p: summarize(rs) for p, rs in by_paper.items()}


def print_report(all_items, results, skipped, unresolved, dump_worst):
    print(f"\nProbe items (ground-truth-supported claim x cited-source-with-supported-evidence pairs): {len(all_items)}")
    print(f"Skipped (ground-truth claim excluded before locating a gold sentence): {len(skipped)}")
    for s in skipped:
        print(f"  - {s[0]}/{s[1]}: {s[2]}")
    print(f"Unresolved (gold sentence text not locatable in the source's sentence list): {len(unresolved)}")
    for u in unresolved:
        print(f"  - {u[0]}/{u[1]} (source {u[2]}): {u[3]!r}")

    print("\n=== Pooled results ===")
    header = f"{'method':28s} {'n':>4s} {'top3':>6s} {'top6':>6s} {'median':>7s} {'max':>5s}"
    print(header)
    for method, rs in results.items():
        s = summarize(rs)
        print(f"{method:28s} {s['n']:>4d} {s['top3']:>3d}/{s['n']:<2d} {s['top6']:>3d}/{s['n']:<2d} "
              f"{s['median_rank']:>7.1f} {s['max_rank']:>5d}")

    print("\n=== Per-paper results ===")
    for method, rs in results.items():
        by_p = summarize_by_paper(rs)
        parts = []
        for p, s in by_p.items():
            parts.append(f"{p}={s['top3']}/{s['n']}(top3) {s['top6']}/{s['n']}(top6) med={s['median_rank']:.0f}")
        print(f"{method:28s} " + " | ".join(parts))

    if dump_worst:
        print(f"\n=== Worst {dump_worst} claims per method (by rank) ===")
        for method, rs in results.items():
            worst = sorted(rs, key=lambda r: -r["rank"])[:dump_worst]
            print(f"-- {method} --")
            for w in worst:
                print(f"   {w['paper']}/{w['claim_id']} src={w['pid'][:10]} rank={w['rank']}/{w['n_sentences']} match={w['match_kind']}")

    if _encode_time_totals:
        print("\n=== Bake-off model encode wall-clock (cache misses only, this process) ===")
        for m, t in _encode_time_totals.items():
            print(f"  {m}: {t:.1f}s")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--methods", default="bm25,rrf,maxnorm,zsum",
                     help="comma list among: bm25,rrf,maxnorm,zsum (specter always runs). default: all")
    ap.add_argument("--bake-off", action="store_true", help="also run the small-model bake-off (downloads models on first use)")
    ap.add_argument("--dump-worst", type=int, default=0, help="print the N worst-ranked claims per method")
    ap.add_argument("--json-out", default=None, help="write full per-item results to this JSON path")
    args = ap.parse_args()

    methods = set(x.strip() for x in args.methods.split(",") if x.strip())
    all_items, results, skipped, unresolved = run_probe(methods, args.bake_off, args.dump_worst)
    print_report(all_items, results, skipped, unresolved, args.dump_worst)

    if args.json_out:
        out = {
            "n_items": len(all_items),
            "skipped": skipped,
            "unresolved": unresolved,
            "results": {m: rs for m, rs in results.items()},
            "summary_pooled": {m: summarize(rs) for m, rs in results.items()},
            "summary_by_paper": {m: summarize_by_paper(rs) for m, rs in results.items()},
            "encode_time_totals": dict(_encode_time_totals),
        }
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        print(f"\nWrote {args.json_out}")


if __name__ == "__main__":
    main()
