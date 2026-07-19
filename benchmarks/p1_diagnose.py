#!/usr/bin/env python3
"""
P1 diagnostic: for WiCE rows where proof exists (label supported/partial),
localize WHERE the pipeline lost the gold sentence:

  SHOWN            gold sentence is on the card (evidences or covering)
  JUDGE_MISS       gold was among the judged candidates (per-source top-K
                   window, or the doc is small enough that extraction read it)
                   but the verdict/amber still says unproven
  RETRIEVAL_MISS   gold exists but never entered the judged candidate set
  COVER_POOL_MISS  (amber rows) gold not in the covering candidate pool
  MAP_FAIL         WiCE gold line can't be matched to any pipeline sentence
                   (ingestion/splitting mismatch)

Offline: no LLM calls. Re-encodes the batch's claims once (SPECTER, CPU);
source-sentence vectors come from the run's embedding cache.

usage: p1_diagnose.py --project data/first_check --run data/first_check_run \
                      [--analysis data/first_check_run/analysis_prev.json]
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.papertrail import embeddings  # noqa: E402
from modules.papertrail.matcher import (  # noqa: E402
    OFFTOPIC, TOPK, COVER_CANDS_PER_SOURCE, COVER_MAX_CANDS,
    _norm, _unusable_evidence, _lex_scores, _rrf, _chunk_sents,
    EXTRACT_TOP_CHUNKS,
)


def _toks(s):
    return set(_norm(s).split())


def map_gold(gold_text, sent_texts):
    """WiCE gold line -> pipeline sentence indices (splitting may differ)."""
    g = _norm(gold_text)
    gt_toks = _toks(gold_text)
    hits = []
    for j, t in enumerate(sent_texts):
        n = _norm(t)
        if not n:
            continue
        if n in g or g in n:
            hits.append(j)
            continue
        st = _toks(t)
        if st and gt_toks:
            inter = len(st & gt_toks)
            if inter / min(len(st), len(gt_toks)) >= 0.7:
                hits.append(j)
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--analysis", default=None)
    ap.add_argument("--only", default=None, help="comma-separated source keys")
    args = ap.parse_args()

    gt = json.load(open(os.path.join(args.project, "wice_ground_truth.json")))["claims"]
    analysis = json.load(open(args.analysis or os.path.join(args.run, "analysis.json")))

    # pipeline sentence index per source key (from the run's source_claims cache)
    src_by_key = {}
    for f in glob.glob(os.path.join(args.run, "source_claims", "*.json")):
        d = json.load(open(f))
        src_by_key[d.get("key") or d.get("title")] = d

    only = set(args.only.split(",")) if args.only else None
    rows = []
    for c in analysis.get("text_claims", []):
        mk = (c.get("markers") or [None])[0]
        if mk not in gt or (only and mk not in only):
            continue
        g = gt[mk]
        if g["label"] == "not_supported" or not g.get("supporting_sentences"):
            continue  # no proof exists -> nothing to find
        rows.append((mk, g, c))

    counts = {}
    for mk, g, c in rows:
        src = src_by_key.get(mk)
        if not src:
            print(f"== {mk}: NO SOURCE CACHE (key mismatch?)")
            continue
        sents = src.get("sentences") or []
        texts = [s.get("text", "") for s in sents]
        gold_lines = open(os.path.join(args.project, "sources", f"{mk}.txt"),
                          encoding="utf-8").read().split("\n")
        gold_idx = sorted({i for grp in g["supporting_sentences"] for i in grp})
        gold_sents = {}
        for i in gold_idx:
            if i < len(gold_lines):
                gold_sents[i] = map_gold(gold_lines[i], texts)

        cache = os.path.join(args.run, "embeddings", f"{src.get('file_hash')}.sents.npz")
        row = embeddings.cosine_matrix(
            [c["text"]], texts,
            b_cache_file=cache if os.path.exists(cache) else None)[0]

        # per-source judge path: cosine ranking -> top-K over threshold
        ranked = [j for j in sorted(range(len(row)), key=lambda k: row[k], reverse=True)
                  if not _unusable_evidence(texts[j])]
        topk = [j for j in ranked[:TOPK] if row[j] >= OFFTOPIC]
        # covering candidate pool (hybrid RRF)
        fused = _rrf(list(row), _lex_scores(c["text"], texts))
        pool = [j for j in sorted(range(len(texts)), key=lambda k: -fused[k])
                if not _unusable_evidence(texts[j])][:COVER_CANDS_PER_SOURCE]
        # how much of the doc extraction reads
        n_chunks = len(list(_chunk_sents(sents)))
        extraction_reads_all = n_chunks <= EXTRACT_TOP_CHUNKS

        shown = {_norm(e.get("sentence", "")) for e in (c.get("evidences") or [])}
        shown |= {_norm(p.get("sentence", ""))
                  for p in ((c.get("covering") or {}).get("covered") or [])}

        tool = c.get("verdict")
        amber = (c.get("covering") or {}).get("uncovered") or []
        print(f"\n== {mk}  wice={g['label']}  tool={tool}"
              f"{' amber=' + str(amber) if amber else ''}"
              f"  ({len(sents)} sents, {n_chunks} chunks)")
        verdicts = []
        for i, hits in gold_sents.items():
            line = gold_lines[i][:90]
            if not hits:
                verdicts.append("MAP_FAIL")
                print(f"  gold[{i}] MAP_FAIL          | {line}")
                continue
            best = max(hits, key=lambda j: row[j])
            in_shown = any(_norm(texts[j]) in shown for j in hits)
            in_topk = any(j in topk for j in hits)
            in_pool = any(j in pool for j in hits)
            rank = ranked.index(best) + 1 if best in ranked else -1
            if in_shown:
                v = "SHOWN"
            elif in_topk or extraction_reads_all:
                v = "JUDGE_MISS"
            elif in_pool:
                v = "POOL_ONLY"   # covering saw it, per-source judge did not
            else:
                v = "RETRIEVAL_MISS"
            verdicts.append(v)
            print(f"  gold[{i}] {v:15s} cos={row[best]:.3f} rank={rank:3d}"
                  f" topk={'Y' if in_topk else 'n'} pool={'Y' if in_pool else 'n'}"
                  f" | {line}")
        # row-level: best outcome across the gold set (any gold shown = shown)
        for pref in ("SHOWN", "JUDGE_MISS", "POOL_ONLY", "RETRIEVAL_MISS", "MAP_FAIL"):
            if pref in verdicts:
                counts[pref] = counts.get(pref, 0) + 1
                break

    print("\n== row-level summary (best gold outcome per claim):")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {k:15s} {v}")


if __name__ == "__main__":
    main()
