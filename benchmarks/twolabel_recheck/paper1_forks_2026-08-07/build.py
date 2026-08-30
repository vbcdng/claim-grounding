#!/usr/bin/env python3
"""Build the paper1 centrality-fork two-label batch (task #11, round 2).

Targets: the 10 Shape-1 fork rows from docs/OPUS_GRADER_FULL_2026-07-17.md
(Fable gold vs Opus grader split on the provability axis, all paper1).
Sentinels: 4 axis-AGREED paper1_hard rows (2 provable + 2 not_provable),
picked by deterministic md5 order, so the retest checks the prompt does not
skew easy rows.

Extract per row = union (deduped, stable order) of:
  1. the tool's evidence sentences (analysis.json evidences[].sentence)
  2. covering-audit proof sentences (analysis.json covering.covered[].sentence)
  3. the Opus grader's verbatim-gate-verified proof quotes
so every provable component's proof is IN the extract — the vote then
isolates the fork question (does an unproven interpretive part sink the
claim?) instead of measuring extract thinness.
"""
import hashlib
import json
import os

ROOT = "/home/moje/Documents/python_projects/claim-grounding"
OUT = os.path.dirname(os.path.abspath(__file__))

TARGETS = ["t6", "t25", "t35", "t37", "t43", "t44", "t47", "t49", "t65", "t68"]

analysis = json.load(open(f"{ROOT}/data/paper1_verification/analysis.json"))
claims = {tc["id"]: tc for tc in analysis["text_claims"]}
graded = {}
for line in open(f"{ROOT}/benchmarks/gold_labels/opus_pass/paper1_hard_2026-07-17_graded.jsonl"):
    g = json.loads(line)
    graded[g["claim_id"]] = g


def md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def build_extract(cid):
    c = claims[cid]
    g = graded[cid]
    sents = []

    def add(s):
        s = (s or "").strip()
        if s and s not in sents:
            sents.append(s)

    for e in (c.get("evidences") or ([c["evidence"]] if c.get("evidence") else [])):
        add(e.get("sentence"))
    cov = c.get("covering") or {}
    for item in cov.get("covered", []):
        add(item.get("sentence"))
    for q in g.get("grader_proofs_verified", []):
        add(q)
    return "\n".join(sents)


# sentinel pick: axis-agreed rows, deterministic md5 order, 2 per axis
agreed_p, agreed_np = [], []
for cid, g in graded.items():
    if cid in TARGETS or g["axis_agreement"] != "agree":
        continue
    if g["fable_axis"] == "provable" and g["grader_axis"] == "provable":
        agreed_p.append(cid)
    elif g["fable_axis"] == "not_provable" and g["grader_axis"] == "not_provable":
        agreed_np.append(cid)
sent_p = sorted(agreed_p, key=md5)[:2]
sent_np = sorted(agreed_np, key=md5)[:2]
sentinels = sent_p + sent_np
print("sentinel candidates provable:", sorted(agreed_p), "-> picked", sent_p)
print("sentinel candidates not_provable:", sorted(agreed_np), "-> picked", sent_np)

all_ids = TARGETS + sentinels
rows = []
for cid in all_ids:
    rows.append({
        "row_id": f"paper1_{cid}",
        "claim": claims[cid]["text"].strip(),
        "extract": build_extract(cid),
    })

rows.sort(key=lambda r: md5(r["row_id"]))
half = (len(rows) + 1) // 2
batches = [rows[:half], rows[half:]]
for i, b in enumerate(batches, 1):
    json.dump(b, open(f"{OUT}/batch_{i}.json", "w"), indent=1, ensure_ascii=False)
    print(f"batch_{i}: {len(b)} rows:", [r['row_id'] for r in b])

manifest = {
    "date": "2026-08-07",
    "design": "task #11 round 2: two-label recheck on the paper1 centrality/rule-A fork rows (docs/OPUS_GRADER_FULL_2026-07-17.md Shape 1). Prompt = round-1b prompt verbatim (incl. the two precision rules), only the file path differs. 3 Fable + 3 Opus fresh blind voters per batch. Fable-safe content (AI-governance paper), no medical screening needed.",
    "targets": {cid: {
        "fable_verdict": graded[cid]["fable_verdict"],
        "grader_action": graded[cid]["grader_action"],
        "fable_axis": graded[cid]["fable_axis"],
        "grader_axis": graded[cid]["grader_axis"],
        "pipeline_verdict": graded[cid]["pipeline_verdict"],
    } for cid in TARGETS},
    "sentinels": {cid: {
        "fable_verdict": graded[cid]["fable_verdict"],
        "grader_action": graded[cid]["grader_action"],
        "axis": graded[cid]["fable_axis"],
    } for cid in sentinels},
    "extract_recipe": "union of tool evidence sentences + covering-audit proof sentences + Opus grader verbatim-verified proofs, deduped, order stable",
    "batches": {f"batch_{i}": [r["row_id"] for r in b] for i, b in enumerate(batches, 1)},
}
json.dump(manifest, open(f"{OUT}/manifest.json", "w"), indent=1, ensure_ascii=False)
lens = sorted((len(r["extract"]), r["row_id"]) for r in rows)
print("extract chars min/max:", lens[0], lens[-1])
