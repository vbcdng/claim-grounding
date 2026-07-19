#!/usr/bin/env python3
"""Aggregate all gold labels (today's + the corpus sweep) into corpus-wide stats.
Deterministic, no LLM. Run from repo root after the labeling wave completes."""
import json, glob, os
from collections import Counter, defaultdict

def load(p):
    out=[]
    for l in open(p):
        l=l.strip()
        if not l: continue
        try: out.append(json.loads(l))
        except: pass
    return out

# Re-grade passes over the same rows (second/third graders) — not first-pass
# gold labels; counting them would double-count claims. Quarantined rows live
# in excluded/ (outside the glob) with an excluded_reason field.
REGRADES = {'k3_disagreements_2026-07-18.jsonl', 'v3_subset_2026-07-18.jsonl'}
files = sorted(glob.glob('benchmarks/gold_labels/*.jsonl') +
               glob.glob('benchmarks/gold_labels/corpus/*.jsonl'))
files = [f for f in files if os.path.basename(f) not in REGRADES]
labels=[]
per_file={}
for f in files:
    rows=load(f)
    per_file[os.path.basename(f)]=len(rows)
    labels.extend(rows)

# normalize: drop rows without a fable verdict (e.g. the mutation-outcomes file
# uses a different schema), lowercase agreement for consistent counting
labels=[l for l in labels if l.get('fable_verdict')]
for l in labels:
    if isinstance(l.get('agreement'),str): l['agreement']=l['agreement'].lower()
    # owner re-grade outranks the model label (rule 2026-07-18)
    l['eff_verdict']=l.get('owner_verdict') or l['fable_verdict']
owner_rows=[l for l in labels if l.get('owner_verdict')]
flips=[l for l in owner_rows if l['owner_verdict']!=l['fable_verdict']]

print(f"=== {len(labels)} labels across {len(files)} files ===")
for f,n in per_file.items(): print(f"  {n:4} {f}")

fv=Counter(l.get('fable_verdict','?') for l in labels)
ev=Counter(l.get('eff_verdict','?') for l in labels)
ag=Counter(l.get('agreement','?') for l in labels)
conf=Counter(l.get('confidence','?') for l in labels)
print("\nby fable_verdict:", dict(fv))
print("by EFFECTIVE verdict (owner overrides):", dict(ev))
print("by agreement:", dict(ag))
print("by confidence:", dict(conf))
print(f"\nowner-graded rows: {len(owner_rows)} ({len(flips)} differ from the fable label)")
for l in flips:
    print(f"  FLIP {l.get('run','?'):50} {l.get('claim_id'):4} fable={l['fable_verdict']:12} -> owner={l['owner_verdict']}")

# Finding-1 reproduction: pipeline unsupported but final label partial/supported (under-credit)
undercredit=[l for l in labels if str(l.get('pipeline_verdict','')).startswith('unsupported')
             and l.get('eff_verdict') in ('partial','supported')]
# over-support flags on supported verdicts
oversupport=[l for l in labels if l.get('agreement')=='flag'
             and str(l.get('pipeline_verdict','')).startswith('supported')]
# verdict-field false-supports: pipeline verdict field was supported (with or
# without a warning flag) while the final label is unsupported
fs_pipe=lambda l: str(l.get('pipeline_verdict','')).startswith('supported') or l.get('pipeline_verdict')=='partial'
false_support=[l for l in labels if fs_pipe(l) and l.get('eff_verdict')=='unsupported']
disagree=[l for l in labels if l.get('agreement')=='DISAGREE']
print(f"\nFinding-1 (pipeline-unsupported -> final partial/supported): {len(undercredit)}")
print(f"Over-support flags on supported verdicts: {len(oversupport)}")
print(f"False-support rows (pipeline supported-ish, final unsupported): {len(false_support)}")
for l in false_support:
    print(f"  FS {l.get('run','?'):50} {l.get('claim_id'):4} pipeline={l.get('pipeline_verdict'):20} by={'owner' if l.get('owner_verdict') else 'fable'}")
print(f"Explicit DISAGREE labels: {len(disagree)}")

byrun=defaultdict(Counter)
for l in labels: byrun[l.get('run','?')][l.get('fable_verdict','?')]+=1
print("\nper-run fable_verdict:")
for run,c in sorted(byrun.items()): print(f"  {run:45} {dict(c)}")

json.dump({"n":len(labels),"by_fable_verdict":dict(fv),
           "by_effective_verdict":dict(ev),"by_agreement":dict(ag),
           "owner_graded":len(owner_rows),"owner_flips":len(flips),
           "undercredit":len(undercredit),"oversupport":len(oversupport),
           "false_support_rows":len(false_support),
           "disagree":len(disagree)},
          open('benchmarks/gold_labels/corpus_summary.json','w'),indent=2)
print("\nwrote benchmarks/gold_labels/corpus_summary.json")
