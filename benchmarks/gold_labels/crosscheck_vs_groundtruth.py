#!/usr/bin/env python3
"""Three-way cross-check: Fable gold label vs pipeline verdict vs human ground truth.
Deterministic, no LLM. Run from repo root."""
import json, sys

def gt(path):
    return {c['id']: c for c in json.load(open(path))['claims']}

def load_jsonl(p):
    return [json.loads(l) for l in open(p) if l.strip()]

PAIRS = [
    ('benchmarks/gold_labels/paper1_hard_2026-07-17.jsonl', 'benchmarks/paper1_ground_truth.json', 'PAPER1'),
    ('benchmarks/gold_labels/bentonite_hard_2026-07-17.jsonl', 'benchmarks/bentonite_ground_truth.json', 'BENTONITE'),
]

for labpath, gtpath, name in PAIRS:
    labels = load_jsonl(labpath); gtmap = gt(gtpath)
    print(f'============== {name} ==============')
    print(f"{'id':5} {'pipeline':16} {'fable':20} {'human':12} note")
    dval = dovc = 0
    for L in labels:
        cid = L['claim_id']; g = gtmap.get(cid)
        he = g['expect'] if g else '(not in GT)'
        note = ''
        if L['agreement'] == 'DISAGREE':
            if g and he != 'unsupported': note = 'FINDING1 VALIDATED'; dval += 1
            elif g and he == 'unsupported': note = 'FABLE OVER-CREDITS (human=unsupported)'; dovc += 1
        print(f"{cid:5} {L['pipeline_verdict'][:16]:16} {L['fable_verdict'][:20]:20} {he[:12]:12} {note}")
    print(f'-> DISAGREE: {dval} validated by human as not-unsupported, {dovc} where human agrees pipeline\n')
