#!/usr/bin/env python3
"""Compose the FROZEN WiCE anchor benchmark (2026-07-19).

Combines, per unique WiCE claim: the WiCE human label, every Fable/Opus
grader verdict, and the owner's ruling where one exists. Tiers:

  A         owner ruled the claim -> final label = owner's (owner outranks)
  B         every model grade agrees with WiCE (binary) -> WiCE label
  B0        no grader ever reviewed this row -> WiCE label, unreviewed
  B_flag    some model dissents but not all -> WiCE label stands (2-v-1),
            dissent recorded
  C         all graders dissent from WiCE and no owner ruling -> EXCLUDED
            from scoring (listed, not deleted)
  X         contaminated: a code/prompt change was derived from this row
            during development -> EXCLUDED from held-out scoring

FREEZE RULE (owner, 2026-07-19): this file is held out from ALL future
tuning. Never edit labels from a tuning session; owner verdicts are
benchmark-only (memory: owner-verdicts-benchmark-only).

Run from repo root. Writes benchmarks/wice_anchor/wice_anchor_2026-07-19.jsonl
and prints the tier summary.
"""
import json, glob, os
from collections import defaultdict

# Contamination audit 2026-07-19 (evidence pointers in README.md).
# VERDICT-PATH: a change to judging inputs/logic was derived from the row ->
# tier X, excluded from held-out scoring.
CONTAMINATED = {
    'waleedmajid': 'subject-entity guard written from this false-support (SUBJECT_GUARD.md)',
    'wildskin': 'non-leading entity extension to the subject guard (_claim_entity_sets)',
    'flagofmilwaukee': 'P3 date-context fix motivator (datestamp sentence)',
    'travisbarker': 'P3 date-context motivator set',
    'universityofessex': 'P3 date-context motivator set',
    'jessicachastain': 'URL-path date rule (/YYYY/MM/DD/) derived from this row',
    'johnrfox': 'P3 narrative-date false-positive guard',
    'itainthalfhotmum': 'entity-run tokenizer fix (trailing punctuation), commit 9a86336',
    'styletaylorswiftsong': 'byline-attribution fix (_doc_author, commit ad0490a) — all 3 claims of this source excluded',
    'gmbtradeunion': 'possessive/apostrophe normalization fix (NIGHT_LOG 7/12)',
}
# DISPLAY-ONLY: a display fix (evidence collapse, badge wording) was derived —
# verdict path untouched, so the row stays SCORED but carries the flag.
DISPLAY_FIX = {
    'ninashipperlee': 'P2 symmetric-display fix (primary motivator)',
    'bonairpresbyterianchurch': 'P2 symmetric-display fix (collapse row)',
    'cokezoo': 'round-8 badge rename (owner ruling row)',
    'averymurraychristmas': 'round-8 badge rename (owner ruling row)',
}

M3 = {'supported': 'sup', 'partial': 'partial', 'unsupported': 'unsup',
      'not_rulable': 'nr', 'own_interpretation': 'unsup',
      'true_claim_weak_evidence': 'partial', 'partial_by_shown_evidence': 'partial',
      'partial_weak': 'partial', 'supported_minor_caveat': 'sup', 'own_argument': 'unsup',
      'wrong_or_insufficient_evidence': 'unsup', 'add_citation_or_rewrite': 'unsup',
      'partially_supported': 'partial', 'not_supported': 'unsup'}
BIN = {'sup': 'S', 'partial': 'N', 'unsup': 'N', 'nr': None}

RUNS = {
    'newsys_wice_dev1': 'benchmarks/wice_runs/dev1',
    'newsys_wice_dev2': 'benchmarks/wice_runs/dev2_pilot',
    'newsys_wice_dev3': 'benchmarks/wice_runs/dev3',
    'newsys_wice_dev4': 'benchmarks/wice_runs/dev4',
    'newsys_wice_dev5': 'benchmarks/wice_runs/dev5',
    'newsys_wice_train1': 'benchmarks/wice_runs/train1',
    'newsys_wice_train2': 'benchmarks/wice_runs/train2',
    # extra verification runs of the dev2_pilot batch (same claims, own runs)
    'first_check_run': 'benchmarks/wice_runs/dev2_pilot',
    'nightB_wice_final': 'benchmarks/wice_runs/dev2_pilot',
}
ANALYSIS = {'first_check_run': 'data/first_check_run/analysis.json',
            'nightB_wice_final': 'data/nightB_wice_final/analysis.json'}

def load(p): return [json.loads(l) for l in open(p) if l.strip()]

gold = {}
for p in glob.glob('benchmarks/gold_labels/corpus/*.jsonl'):
    for r in load(p):
        gold[(r.get('run', ''), str(r.get('claim_id', '')))] = r
opus = {}
for p in glob.glob('benchmarks/gold_labels/opus_pass/*_graded.jsonl'):
    if 'k3_' in p: continue
    for r in load(p):
        opus[(r.get('run', ''), str(r.get('claim_id', '')))] = r.get('grader_action')

claims = defaultdict(lambda: {'runs': [], 'fable': [], 'opus': [], 'owner': None,
                              'owner_note': '', 'claim': '', 'slug': '', 'wice_label': ''})
for run, gtdir in RUNS.items():
    ap = ANALYSIS.get(run, f'{gtdir}/analysis.json')
    if not os.path.exists(ap): continue
    a = json.load(open(ap))
    gt = json.load(open(f'{gtdir}/wice_ground_truth.json'))['claims']
    for c in a['text_claims']:
        slug = (c.get('markers') or [None])[0]
        if slug not in gt: continue
        wid = gt[slug]['wice_id']
        e = claims[wid]
        e['slug'] = slug; e['wice_label'] = gt[slug]['label']
        e['claim'] = e['claim'] or c.get('text', '')
        e['runs'].append(f"{run}:{c['id']}")
        g = gold.get((run, str(c['id'])))
        if g:
            if g.get('fable_verdict'): e['fable'].append(g['fable_verdict'])
            if g.get('owner_verdict'):
                e['owner'] = g['owner_verdict']; e['owner_note'] = g.get('owner_note', '')
        o = opus.get((run, str(c['id'])))
        if o: e['opus'].append(o)

out, tiers = [], defaultdict(int)
for wid, e in sorted(claims.items()):
    wb = BIN[M3[e['wice_label']]]
    grades = [M3.get(v) for v in e['fable'] + e['opus'] if M3.get(v)]
    gbins = [BIN[g] for g in grades if BIN[g] is not None]
    agree = [b for b in gbins if b == wb]; dissent = [b for b in gbins if b != wb]
    if e['slug'] in CONTAMINATED:
        tier, final, scoring = 'X', None, 'exclude'
    elif e['owner']:
        tier, final, scoring = 'A', e['owner'], 'include'
    elif not gbins:
        # WiCE label never reviewed by any grader — honest lower sub-tier
        tier, final, scoring = 'B0', e['wice_label'], 'include'
    elif not dissent:
        tier, final, scoring = 'B', e['wice_label'], 'include'
    elif agree:
        tier, final, scoring = 'B_flag', e['wice_label'], 'include'
    else:
        tier, final, scoring = 'C', None, 'exclude'
    tiers[tier] += 1
    out.append({'wice_id': wid, 'slug': e['slug'], 'runs': e['runs'],
                'claim': e['claim'][:500], 'wice_label': e['wice_label'],
                'fable': e['fable'], 'opus': e['opus'],
                'owner_verdict': e['owner'], 'owner_note': e['owner_note'][:300],
                'tier': tier, 'final_label': final, 'scoring': scoring,
                'contamination': CONTAMINATED.get(e['slug']),
                'display_fix_derived': DISPLAY_FIX.get(e['slug']),
                'frozen': '2026-07-19'})

os.makedirs('benchmarks/wice_anchor', exist_ok=True)
with open('benchmarks/wice_anchor/wice_anchor_2026-07-19.jsonl', 'w') as f:
    for r in out:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')
print(f"{len(out)} unique WiCE claims -> benchmarks/wice_anchor/wice_anchor_2026-07-19.jsonl")
print("tiers:", dict(tiers))
print("scored:", sum(1 for r in out if r['scoring'] == 'include'))
for r in out:
    if r['tier'] in ('C', 'X'):
        print(f"  {r['tier']} {r['slug']:26} wice={r['wice_label']:20}",
              r['contamination'] or 'all graders dissent, no owner ruling')
