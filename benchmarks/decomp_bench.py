"""Source-decomposition quality benchmark (2026-07-07).

Decomposition (source -> atomic claims) is the expensive first-run LLM step AND
it is OFF the verdict-critical path — verdicts match user claims against a
source's RAW SENTENCES, not its decomposed claims. Decomposed claims only feed
the "unused source points" panel, the round-2 partial-check escalation context,
and "wrong source" alternatives. So it's the safest place to try a cheaper model
(e.g. deepseek/deepseek-chat) — this bench measures whether a candidate model's
decomposition is quality-comparable to the current one.

Two modes:
  # 1. Score decompositions already on disk (the current-model BASELINE, $0):
  python3 benchmarks/decomp_bench.py --cache-dir data/eggs_run/source_claims \
      --label flash-lite

  # 2. Decompose a test set FRESH with a candidate model and score it (needs the
  #    model's API key, e.g. DEEPSEEK_API_KEY), then diff vs a reference set:
  python3 benchmarks/decomp_bench.py --decompose data/eggs/sources/blesso2018.txt ... \
      --model deepseek/deepseek-chat --out /tmp/deepseek_decomp \
      --ref-cache-dir data/eggs_run/source_claims

Metrics per source (no ground truth needed):
  n_claims, claims_per_1k_chars  — coverage; catches UNDER-extraction (the
      korinek2023 "8 fragments from 72 pages" failure) and over-extraction.
  frag_rate                      — % claims that are _degenerate or a bare
      journal/reference fragment (junk the extractor should never emit).
  faithful_rate@0.60 / mean_max_cosine — for each claim, max SPECTER cosine to
      ANY source sentence; low = the "claim" isn't really in the source
      (hallucination / over-paraphrase). The core quality signal.
  dup_rate                       — near-duplicate claims (cosine >= 0.95).
Comparison (candidate vs reference), when --ref-cache-dir is given:
  recall  — % of reference claims matched by some candidate claim (cosine>=0.80)
  novel   — % of candidate claims with no reference match (new or hallucinated)
"""
import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import embeddings, matcher

FAITHFUL_TAU = 0.60      # claim must be at least this cosine-close to a source sentence
MATCH_TAU = 0.80         # candidate claim "matches" a reference claim
DUP_TAU = 0.95


def _load_cache(cache_dir, key_filter=None):
    """Return {label: {claims:[...], sentences:[...]}} from a source_claims dir."""
    out = {}
    for f in sorted(glob.glob(os.path.join(cache_dir, "*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if "claims" not in d:
            continue
        label = d.get("key") or d.get("filename") or os.path.basename(f)
        if key_filter and label not in key_filter:
            continue
        out[label] = d
    return out


def score(d):
    claims = [(c.get("text") or "").strip() for c in (d.get("claims") or [])]
    claims = [c for c in claims if c]
    sents = [(s.get("text") or "") for s in (d.get("sentences") or [])]
    chars = d.get("source_text_chars") or sum(len(s) for s in sents) or 1
    m = {"n_claims": len(claims),
         "claims_per_1k_chars": round(len(claims) / (chars / 1000.0), 2),
         "frag_rate": None, "faithful_rate": None, "mean_max_cosine": None,
         "dup_rate": None}
    if not claims:
        return m
    frags = sum(1 for c in claims
                if matcher._degenerate(c) or matcher._is_reference_fragment(c))
    m["frag_rate"] = round(frags / len(claims), 3)
    # faithfulness: max cosine of each claim to any source sentence
    if sents:
        mat = embeddings.cosine_matrix(claims, sents)
        maxes = [max(row) for row in mat]
        m["faithful_rate"] = round(sum(1 for x in maxes if x >= FAITHFUL_TAU) / len(maxes), 3)
        m["mean_max_cosine"] = round(sum(maxes) / len(maxes), 3)
    # duplication: claim-vs-claim cosine
    if len(claims) > 1:
        cc = embeddings.cosine_matrix(claims, claims)
        dup = sum(1 for i in range(len(claims))
                  for j in range(i + 1, len(claims)) if cc[i][j] >= DUP_TAU)
        m["dup_rate"] = round(dup / len(claims), 3)
    return m


def compare(cand_claims, ref_claims):
    cand = [c for c in cand_claims if c.strip()]
    ref = [c for c in ref_claims if c.strip()]
    if not cand or not ref:
        return {"recall": None, "novel": None}
    mat = embeddings.cosine_matrix(ref, cand)                 # rows=ref, cols=cand
    recall = sum(1 for row in mat if max(row) >= MATCH_TAU) / len(ref)
    matched_cand = {j for row in mat for j, v in enumerate(row) if v >= MATCH_TAU}
    novel = 1 - len(matched_cand) / len(cand)
    return {"recall": round(recall, 3), "novel": round(novel, 3)}


def _print_table(rows, cols):
    w = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    print("  ".join(c.ljust(w[c]) for c in cols))
    print("  ".join("-" * w[c] for c in cols))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(w[c]) for c in cols))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", help="score decompositions already in this source_claims dir")
    ap.add_argument("--keys", help="comma-separated key filter (default: all)")
    ap.add_argument("--label", default="model", help="label for the scored set")
    ap.add_argument("--decompose", nargs="*", help="source file paths to decompose FRESH")
    ap.add_argument("--model", help="model for --decompose (litellm string)")
    ap.add_argument("--out", help="cache dir for --decompose output")
    ap.add_argument("--ref-cache-dir", help="reference source_claims dir to diff a fresh run against")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    key_filter = set(args.keys.split(",")) if args.keys else None

    if args.decompose:
        assert args.model and args.out, "--decompose needs --model and --out"
        from modules.papertrail import source_decomposer
        from modules.papertrail.llm_client import LLMClient
        os.makedirs(args.out, exist_ok=True)
        llm = LLMClient(model=args.model)
        made = {}
        for path in args.decompose:
            fn = os.path.basename(path)
            key = os.path.splitext(fn)[0]
            pid = __import__("hashlib").sha1(fn.encode()).hexdigest()
            t0 = time.time()
            d = source_decomposer.decompose_source(path, pid, key, args.out, llm,
                                                   workers=args.concurrency)
            d["_secs"] = round(time.time() - t0, 1)
            made[key] = d
        data = made
    else:
        assert args.cache_dir, "give --cache-dir or --decompose"
        data = _load_cache(args.cache_dir, key_filter)

    ref = _load_cache(args.ref_cache_dir) if args.ref_cache_dir else {}

    rows = []
    for label, d in sorted(data.items()):
        m = score(d)
        r = {"source": label, **m}
        if "_secs" in d:
            r["secs"] = d["_secs"]
        if ref and label in ref:
            r.update(compare([c.get("text", "") for c in d.get("claims", [])],
                             [c.get("text", "") for c in ref[label].get("claims", [])]))
        rows.append(r)

    cols = ["source", "n_claims", "claims_per_1k_chars", "frag_rate",
            "faithful_rate", "mean_max_cosine", "dup_rate"]
    if any("secs" in r for r in rows):
        cols.append("secs")
    if ref:
        cols += ["recall", "novel"]
    print(f"\n=== decomposition quality: {args.label} ===")
    _print_table(rows, cols)
    # aggregate
    def avg(k):
        vals = [r[k] for r in rows if isinstance(r.get(k), (int, float))]
        return round(sum(vals) / len(vals), 3) if vals else None
    print(f"\nmeans — frag_rate {avg('frag_rate')} · faithful_rate {avg('faithful_rate')} "
          f"· mean_max_cosine {avg('mean_max_cosine')} · dup_rate {avg('dup_rate')}"
          + (f" · recall {avg('recall')} · novel {avg('novel')}" if ref else ""))


if __name__ == "__main__":
    main()
