#!/usr/bin/env python3
"""Sweep skip-rule cutoffs over chunk_features.jsonl (from the replay).
Rule form (matches the matcher implementation):
  SKIP a base-kept chunk unless cmax >= C or lmax >= L,
  but ALWAYS keep cos_rank < 2 and (lex_rank == 0 and lmax > 0).
Report, per (C, L): extraction calls saved and winning chunks lost (must be 0).
"""
import json, sys, os

FEATS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chunk_features.jsonl')
rows = [json.loads(l) for l in open(FEATS)]
total = len(rows)
winners = [r for r in rows if r['winner']]
print(f"{total} base-kept chunks, {len(winners)} winners")

def kept(r, C, L):
    if r['cos_rank'] < 2:
        return True
    if r['lex_rank'] == 0 and r['lmax'] > 0:
        return True
    return r['cmax'] >= C or r['lmax'] >= L

print(f"{'C':>5} {'L':>5} {'saved':>7} {'saved%':>7} {'winners lost':>13}")
best = None
for C in (0.40, 0.45, 0.50, 0.55, 0.60):
    for L in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 99.0):
        skipped = sum(1 for r in rows if not kept(r, C, L))
        lost = [r for r in winners if not kept(r, C, L)]
        print(f"{C:5.2f} {L:5.1f} {skipped:7d} {100*skipped/total:6.1f}% {len(lost):13d}")
        if not lost and (best is None or skipped > best[2]):
            best = (C, L, skipped)
if best:
    print(f"\nbest zero-loss rule: cmax >= {best[0]} or lmax >= {best[1]}  "
          f"-> saves {best[2]} of {total} extraction calls ({100*best[2]/total:.1f}%)")
# margin check for the best rule: closest winner to the skip boundary
if best:
    C, L, _ = best
    margins = sorted(min(r['cmax'] - C if r['cos_rank'] >= 2 else 9, 9) for r in winners)
    close = [r for r in winners if r['cos_rank'] >= 2 and not (r['lex_rank'] == 0 and r['lmax'] > 0)
             and r['cmax'] < C + 0.03 and r['lmax'] < L + 0.5]
    print(f"winners within a hair of the boundary (cmax < C+0.03 and lmax < L+0.5): {len(close)}")
    for r in close[:10]:
        print("  ", {k: r[k] for k in ('run', 'cmax', 'lmax', 'cos_rank', 'lex_rank', 'n_kept')})
