#!/usr/bin/env python3
"""Task #50 half-one offline replay: for every logged full-text-fallback sweep,
rebuild the chunking + relevance scores exactly as matcher.py does and test
candidate chunk-selection policies. No LLM calls; SPECTER runs locally on CPU.

Per supported sweep we find the chunk holding the winning sentence and ask, for
each policy: would that chunk still have been read?  Per ALL sweeps we count
extraction calls under each policy (= chunks kept, the cost driver).

Policies:
  base   — today's behavior: all chunks if <= 6, else top-6 cosine + lex rescue
  top3   — long docs only: top-3 cosine + lex rescue (short docs unchanged)
  floorX — ALL docs: keep chunk if max-sent cosine >= X or lex overlap > 0;
           always keep top-2 by cosine and top-1 by lex (never empty)
"""
import json, glob, os, sys, collections
sys.path.insert(0, '/home/moje/Documents/python_projects/claim-grounding')
from modules.papertrail import matcher
from modules.papertrail.embeddings import embed
import numpy as np

ROOT = '/home/moje/Documents/python_projects/claim-grounding'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'replay_results.json')

def norm(t):
    return " ".join((t or "").lower().split())

def kept_base(chunks, row, lex):
    n = len(chunks)
    if n <= matcher.EXTRACT_TOP_CHUNKS:
        return set(range(n))
    ranked = sorted(range(n), key=lambda i: -max(row[j] for j in chunks[i][1]))
    keep = set(ranked[:matcher.EXTRACT_TOP_CHUNKS])
    lex_ranked = sorted(range(n), key=lambda i: -max(lex[j] for j in chunks[i][1]))
    lex_keep = max(matcher.EXTRACT_LEX_CHUNKS, min(8, n // 40))
    for i in lex_ranked[:lex_keep]:
        if max(lex[j] for j in chunks[i][1]) > 0:
            keep.add(i)
    return keep

def kept_topk(chunks, row, lex, k):
    n = len(chunks)
    if n <= k:
        return set(range(n))
    ranked = sorted(range(n), key=lambda i: -max(row[j] for j in chunks[i][1]))
    keep = set(ranked[:k])
    lex_ranked = sorted(range(n), key=lambda i: -max(lex[j] for j in chunks[i][1]))
    lex_keep = max(matcher.EXTRACT_LEX_CHUNKS, min(8, n // 40))
    for i in lex_ranked[:lex_keep]:
        if max(lex[j] for j in chunks[i][1]) > 0:
            keep.add(i)
    return keep

def kept_floor(chunks, row, lex, floor):
    n = len(chunks)
    cos_ranked = sorted(range(n), key=lambda i: -max(row[j] for j in chunks[i][1]))
    lex_ranked = sorted(range(n), key=lambda i: -max(lex[j] for j in chunks[i][1]))
    keep = set(cos_ranked[:2])
    if max(lex[j] for j in chunks[lex_ranked[0]][1]) > 0:
        keep.add(lex_ranked[0])
    for i in range(n):
        if max(row[j] for j in chunks[i][1]) >= floor or max(lex[j] for j in chunks[i][1]) > 0:
            keep.add(i)
    # respect today's long-doc cap so a floor never READS MORE than base does
    return keep & kept_base(chunks, row, lex) if n > matcher.EXTRACT_TOP_CHUNKS else keep

POLICIES = {
    'base':    lambda c, r, l: kept_base(c, r, l),
    'top3':    lambda c, r, l: kept_topk(c, r, l, 3),
    'floor45': lambda c, r, l: kept_floor(c, r, l, 0.45),
    'floor50': lambda c, r, l: kept_floor(c, r, l, 0.50),
    'floor55': lambda c, r, l: kept_floor(c, r, l, 0.55),
}

def main():
    run_dirs = sorted({os.path.dirname(f) for f in glob.glob(ROOT + '/data/**/analysis.json', recursive=True)
                       if os.path.isdir(os.path.join(os.path.dirname(f), 'source_claims'))
                       and os.path.isdir(os.path.join(os.path.dirname(f), 'embeddings'))})
    print(f"{len(run_dirs)} run dirs with caches", flush=True)

    sweeps = 0; wins = 0; unmapped = 0
    model_cache = {}
    feat_out = open(os.path.join(os.path.dirname(OUT), 'chunk_features.jsonl'), 'w')

    for rd in run_dirs:
        try:
            a = json.load(open(os.path.join(rd, 'analysis.json')))
        except Exception:
            continue
        # source caches by paper_id
        caches = {}
        for cf in glob.glob(os.path.join(rd, 'source_claims', '*.json')):
            try:
                d = json.load(open(cf))
            except Exception:
                continue
            h = os.path.basename(cf).replace('.json', '')
            zp = os.path.join(rd, 'embeddings', h + '.sents.npz')
            if d.get('paper_id') and os.path.exists(zp):
                caches[d['paper_id']] = (d, zp)
        entries = []   # (claim_text, paper_id, supported, sentence_text)
        for c in a.get('text_claims', []):
            for e in (c.get('evidences') or []):
                if isinstance(e, dict) and e.get('via') == 'llm_fulltext' and e.get('paper_id') in caches:
                    entries.append((c.get('text') or '', e['paper_id'],
                                    bool(e.get('supported')), e.get('sentence') or ''))
        if not entries:
            continue
        claim_texts = sorted({t for t, _, _, _ in entries})
        vecs = np.asarray([np.asarray(v, dtype=np.float32) for v in embed(claim_texts)])
        vmap = {t: vecs[i] for i, t in enumerate(claim_texts)}

        for claim, pid, supported, sent in entries:
            d, zp = caches[pid]
            sents = d.get('sentences') or []
            if not sents:
                continue
            key = (rd, pid)
            if key not in model_cache:
                z = np.load(zp)
                sv = z['vecs']
                if len(sv) != len(sents):
                    model_cache[key] = None
                else:
                    sn = sv / (np.linalg.norm(sv, axis=1, keepdims=True) + 1e-9)
                    model_cache[key] = sn
            sn = model_cache[key]
            if sn is None:
                continue
            cv = vmap[claim]
            cv = cv / (np.linalg.norm(cv) + 1e-9)
            row = (sn @ cv).tolist()
            texts = [s.get('text', '') for s in sents]
            lex = matcher._lex_scores(claim, texts)
            chunks = matcher._chunk_sents(sents)
            if not chunks:
                continue
            sweeps += 1
            win_chunk = None
            if supported and sent:
                ns = norm(sent)
                wj = next((j for j, t in enumerate(texts) if ns and (ns in norm(t) or norm(t) in ns)), None)
                if wj is None:
                    unmapped += 1
                else:
                    win_chunk = next(i for i, (_, idxs) in enumerate(chunks) if wj in idxs)
                    wins += 1
            # dump per-chunk features for the BASE-kept set (removal candidates)
            bkeep = kept_base(chunks, row, lex)
            kept_list = sorted(bkeep)
            cmax = {i: max(row[j] for j in chunks[i][1]) for i in kept_list}
            lmax = {i: max(lex[j] for j in chunks[i][1]) for i in kept_list}
            cos_rank = {i: r for r, i in enumerate(sorted(kept_list, key=lambda i: -cmax[i]))}
            lex_rank = {i: r for r, i in enumerate(sorted(kept_list, key=lambda i: -lmax[i]))}
            best_l = max(lmax.values()) if lmax else 0.0
            for i in kept_list:
                feat_out.write(json.dumps({
                    'sweep': sweeps, 'run': os.path.basename(rd), 'pid': pid,
                    'cmax': round(cmax[i], 4), 'lmax': round(lmax[i], 3),
                    'lrel': round(lmax[i] / best_l, 3) if best_l > 0 else 0.0,
                    'cos_rank': cos_rank[i], 'lex_rank': lex_rank[i],
                    'n_kept': len(kept_list),
                    'winner': int(win_chunk == i)}) + '\n')
    feat_out.close()
    print(json.dumps({'run_dirs': len(run_dirs), 'sweeps': sweeps,
                      'supported_mapped': wins, 'supported_unmapped': unmapped}, indent=1))
    print('features ->', os.path.join(os.path.dirname(OUT), 'chunk_features.jsonl'), flush=True)

if __name__ == '__main__':
    main()
