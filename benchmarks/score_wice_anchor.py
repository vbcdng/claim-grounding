#!/usr/bin/env python3
"""Score the tool against the FROZEN WiCE anchor (benchmarks/wice_anchor/).

Dual report per layer (strict verdict bucket + arbiter-adjudicated bucket):
  raw       vs the original WiCE labels (comparable to the literature)
  corrected vs the anchor's final_label (tier-A owner rulings override WiCE;
            tiers C/X excluded)
Never edit the anchor from a tuning session (frozen 2026-07-19, owner rule).
Run from repo root; no LLM calls.
"""
import json, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from wice_bench import _tool_bucket, _adjudicated_bucket   # same mapping as the published score

RUN_DIRS = {'newsys_wice_dev1': 'benchmarks/wice_runs/dev1',
            'newsys_wice_dev2': 'benchmarks/wice_runs/dev2_pilot',
            'newsys_wice_dev3': 'benchmarks/wice_runs/dev3',
            'newsys_wice_dev4': 'benchmarks/wice_runs/dev4',
            'newsys_wice_dev5': 'benchmarks/wice_runs/dev5',
            'newsys_wice_train1': 'benchmarks/wice_runs/train1',
            'newsys_wice_train2': 'benchmarks/wice_runs/train2'}

W3 = {'supported': 'supported', 'partially_supported': 'partially_supported',
      'not_supported': 'not_supported',
      # owner vocab -> 3-way
      'partial': 'partially_supported', 'unsupported': 'not_supported',
      'sup': 'supported'}

def main():
    anchor = {}
    for l in open('benchmarks/wice_anchor/wice_anchor_2026-07-19.jsonl'):
        r = json.loads(l)
        anchor[r['wice_id']] = r   # slugs are not unique; wice_id is

    runs = {}
    for run, d in RUN_DIRS.items():
        a = json.load(open(f'{d}/analysis.json'))
        runs[run] = {str(c['id']): c for c in a['text_claims']}

    def tool_claim(r):
        # join by (run, claim_id) — slugs are NOT unique (two claims can cite
        # the same article); use the primary batch run recorded in the anchor
        for ref in r['runs']:
            run, cid = ref.split(':')
            if run in runs and cid in runs[run]:
                return runs[run][cid]
        return None

    stats = {k: [0, 0] for k in ('raw_strict', 'raw_adj', 'cor_strict', 'cor_adj')}
    nr_excluded = 0
    for r in anchor.values():
        c = tool_claim(r)
        if c is None: continue
        tb = _tool_bucket(c)
        ab, _ = _adjudicated_bucket(c)
        wl = r['wice_label']
        stats['raw_strict'][1] += 1; stats['raw_strict'][0] += tb == wl
        stats['raw_adj'][1] += 1;    stats['raw_adj'][0] += ab == wl
        if r['scoring'] != 'include': continue
        fl = W3.get(r['final_label'])
        if fl is None:               # owner not_rulable -> excluded from corrected
            nr_excluded += 1; continue
        stats['cor_strict'][1] += 1; stats['cor_strict'][0] += tb == fl
        stats['cor_adj'][1] += 1;    stats['cor_adj'][0] += ab == fl

    print('WiCE anchor scoring (7 primary batch runs, 3-way buckets)')
    for name, label in (('raw_strict', 'RAW vs WiCE labels, strict'),
                        ('raw_adj', 'RAW vs WiCE labels, adjudicated'),
                        ('cor_strict', 'CORRECTED vs anchor final, strict'),
                        ('cor_adj', 'CORRECTED vs anchor final, adjudicated')):
        y, n = stats[name]
        print(f'  {label:38} {y}/{n}  ({100*y/n:.0f}%)')
    if nr_excluded:
        print(f'  (owner not-rulable rows excluded from corrected: {nr_excluded})')

if __name__ == '__main__':
    main()
