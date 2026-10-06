#!/usr/bin/env python3
"""Task #76 offline replay: how much would a broader chunk-selection rule read,
and would it ever LOSE a chunk that held a winning proof sentence?

Background (task #76, 2026-09-02): paper1 gate row t13 flipped supported ->
unsupported after the PDF-reader swap because the chunk holding its proof
sentence ("Access to the most capable models is throttled by default ...")
fell from cosine rank 6 to rank 8 when the chunk boundaries shifted by ~14
sentences, while the lexical rescue keeps only the top-2 lexical chunks and
that chunk sits at lexical rank 3 in both runs. The proof SENTENCE itself is
lexical rank 3 of ~1,200 sentences but cosine rank 162.

For every logged full-text sweep (analysis.json evidences with via=llm_fulltext
in any data/ run that still has its source_claims + embeddings caches) this
script rebuilds chunks, the claim's cosine row and lexical scores exactly as
matcher.py does, then asks for each candidate policy:
  * how many chunks would be read (= extraction calls, the cost driver)
  * is the chunk holding the logged winning sentence still read
  * on UNSUPPORTED sweeps, does the policy read >= 1 chunk base did not
    (the only population where a false rejection can be repaired)
  * for the t13 runs, is the chunk holding the known proof sentence read
No LLM calls; SPECTER runs locally on CPU.
"""
import json, glob, os, sys, collections, time
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)
from modules.papertrail import matcher
from modules.papertrail.embeddings import embed
import numpy as np

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'replay_results.json')
T13_PROOF = 'throttled by default, in part to save compute for American customers'
TOP = matcher.EXTRACT_TOP_CHUNKS

def norm(t):
    return " ".join((t or "").lower().split())

def cmax_lmax(chunks, row, lex):
    return ([max(row[j] for j in c[1]) for c in chunks],
            [max(lex[j] for j in c[1]) for c in chunks])

def kept_cos_plus_lex(chunks, row, lex, lex_floor, top=TOP, cos_rank_src=None):
    n = len(chunks)
    if n <= top:
        return set(range(n))
    cm, lm = cmax_lmax(chunks, row, lex)
    ranked = cos_rank_src if cos_rank_src is not None else sorted(range(n), key=lambda i: -cm[i])
    keep = set(ranked[:top])
    lex_ranked = sorted(range(n), key=lambda i: -lm[i])
    lex_keep = max(lex_floor, min(8, n // 40))
    for i in lex_ranked[:lex_keep]:
        if lm[i] > 0:
            keep.add(i)
    return keep

def kept_fused(chunks, row, lex, lex_floor, top=TOP):
    n = len(chunks)
    if n <= top:
        return set(range(n))
    cm, lm = cmax_lmax(chunks, row, lex)
    fused = matcher._rrf(cm, lm)
    ranked = sorted(range(n), key=lambda i: -fused[i])
    return kept_cos_plus_lex(chunks, row, lex, lex_floor, top, cos_rank_src=ranked)

def kept_lrel(chunks, row, lex, rel):
    """base + any chunk whose best lexical sentence scores >= rel * the best
    lexical sentence in the document (a relative floor instead of a count)."""
    n = len(chunks)
    keep = kept_cos_plus_lex(chunks, row, lex, matcher.EXTRACT_LEX_CHUNKS)
    if n <= TOP:
        return keep
    cm, lm = cmax_lmax(chunks, row, lex)
    best = max(lm) if lm else 0
    if best > 0:
        for i in range(n):
            if lm[i] >= rel * best:
                keep.add(i)
    return keep

POLICIES = {
    'base':       lambda c, r, l: kept_cos_plus_lex(c, r, l, 2),
    'lex3':       lambda c, r, l: kept_cos_plus_lex(c, r, l, 3),
    'lex4':       lambda c, r, l: kept_cos_plus_lex(c, r, l, 4),
    'top7':       lambda c, r, l: kept_cos_plus_lex(c, r, l, 2, top=7),
    'fused6':     lambda c, r, l: kept_fused(c, r, l, 2),
    'fused6lex3': lambda c, r, l: kept_fused(c, r, l, 3),
    'lrel90':     lambda c, r, l: kept_lrel(c, r, l, 0.90),
    'lrel80':     lambda c, r, l: kept_lrel(c, r, l, 0.80),
}

def main():
    t0 = time.time()
    run_dirs = sorted({os.path.dirname(f) for f in glob.glob(ROOT + '/data/**/analysis.json', recursive=True)
                       if os.path.isdir(os.path.join(os.path.dirname(f), 'source_claims'))
                       and os.path.isdir(os.path.join(os.path.dirname(f), 'embeddings'))})
    print(f"{len(run_dirs)} run dirs with caches", flush=True)
    stats = {p: collections.Counter() for p in POLICIES}
    t13 = []
    sweeps = wins = unmapped = long_sweeps = 0
    lost_examples = collections.defaultdict(list)
    for rd in run_dirs:
        try:
            a = json.load(open(os.path.join(rd, 'analysis.json')))
        except Exception:
            continue
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
        entries = []
        for c in a.get('text_claims', []):
            for e in (c.get('evidences') or []):
                if isinstance(e, dict) and e.get('via') == 'llm_fulltext' and e.get('paper_id') in caches:
                    entries.append((c.get('id'), c.get('text') or '', e['paper_id'],
                                    bool(e.get('supported')), e.get('sentence') or ''))
        if not entries:
            continue
        claim_texts = sorted({t for _, t, _, _, _ in entries})
        vecs = np.asarray([np.asarray(v, dtype=np.float32) for v in embed(claim_texts)])
        vmap = {t: vecs[i] for i, t in enumerate(claim_texts)}
        model_cache = {}
        for cid, claim, pid, supported, sent in entries:
            d, zp = caches[pid]
            sents = d.get('sentences') or []
            if not sents:
                continue
            if pid not in model_cache:
                z = np.load(zp)
                sv = z['vecs']
                model_cache[pid] = None if len(sv) != len(sents) else sv / (np.linalg.norm(sv, axis=1, keepdims=True) + 1e-9)
            sn = model_cache[pid]
            if sn is None:
                continue
            cv = vmap[claim]; cv = cv / (np.linalg.norm(cv) + 1e-9)
            row = (sn @ cv).tolist()
            texts = [s.get('text', '') for s in sents]
            lex = matcher._lex_scores(claim, texts)
            chunks = matcher._chunk_sents(sents)
            if not chunks:
                continue
            sweeps += 1
            is_long = len(chunks) > TOP
            long_sweeps += is_long
            win_chunk = None
            if supported and sent:
                ns = norm(sent)
                wj = next((j for j, t in enumerate(texts) if ns and (ns in norm(t) or norm(t) in ns)), None)
                if wj is None:
                    unmapped += 1
                else:
                    win_chunk = next(i for i, (_, idxs) in enumerate(chunks) if wj in idxs)
                    wins += 1
            proof_chunk = None
            if os.path.basename(rd) in ('gate_task61publish_paper1', 'gate_task69ctrl_paper1', 'gate_task69gate_paper1') and cid == 't13':
                pj = next((j for j, t in enumerate(texts) if T13_PROOF in t), None)
                if pj is not None:
                    proof_chunk = next(i for i, (_, idxs) in enumerate(chunks) if pj in idxs)
            base = POLICIES['base'](chunks, row, lex)
            for p, fn in POLICIES.items():
                k = fn(chunks, row, lex)
                st = stats[p]
                st['chunks_read'] += len(k)
                st['sweeps'] += 1
                if k != base:
                    st['sweeps_changed'] += 1
                    st['chunks_added'] += len(k - base)
                    st['chunks_dropped'] += len(base - k)
                if not supported and (k - base):
                    st['unsupported_sweeps_with_new_chunk'] += 1
                if not supported:
                    st['unsupported_sweeps'] += 1
                if win_chunk is not None:
                    st['winners'] += 1
                    if win_chunk in k:
                        st['winners_kept'] += 1
                    else:
                        st['winners_lost'] += 1
                        if len(lost_examples[p]) < 12:
                            lost_examples[p].append({'run': os.path.basename(rd), 'claim': cid, 'sentence': sent[:120]})
                if proof_chunk is not None:
                    t13.append({'run': os.path.basename(rd), 'policy': p, 'proof_chunk_read': proof_chunk in k,
                                'n_chunks': len(chunks), 'read': sorted(k)})
    res = {'run_dirs': len(run_dirs), 'sweeps': sweeps, 'long_sweeps_over_6_chunks': long_sweeps,
           'supported_mapped_winners': wins, 'supported_unmapped': unmapped,
           'policies': {p: dict(s) for p, s in stats.items()},
           'winners_lost_examples': dict(lost_examples), 't13': t13,
           'seconds': round(time.time() - t0)}
    json.dump(res, open(OUT, 'w'), indent=1)
    print(json.dumps({k: v for k, v in res.items() if k not in ('t13', 'winners_lost_examples')}, indent=1))
    for r in t13:
        print('t13', r['run'], r['policy'], 'proof chunk read:', r['proof_chunk_read'], 'chunks read:', len(r['read']), '/', r['n_chunks'])
    print('REPLAY DONE', flush=True)

if __name__ == '__main__':
    main()
