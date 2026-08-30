"""What would re-emitting the co-citation markers do to the claims the tool
actually sees? (Task #17(b), 2026-08-01.)

The Citation-Integrity converter deletes the paper's OTHER citations, so on a
multi-cited row the tool is asked to prove a whole statement from one of the
several papers that back it. The proposed repair was to keep those citations as
markers and let the tool's own marker splitting narrow each claim to the clause
its citation covers — free, because a marker with no source becomes an uncited
"own" claim and costs no API call.

This runs the PRODUCTION segmenter over both versions of every row and reports
what changes. It decided the question and the answer was no: 5 rows lose the
citation under test altogether and most of the rest narrow to fragments like
"and the US". Findings written up in docs/CI_FAILURE_ANALYSIS_2026-08-01.md
§4. Kept so the measurement can be repeated rather than re-argued.

No API calls, no network. Run: venv/bin/python3 benchmarks/ci_sibling_marker_probe.py
"""
import sys, os, json, re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
import citation_integrity_bench as ci
from modules.papertrail import text_decomposer as td

BATCH = os.path.join(ROOT, "data", "citation_integrity", "batch_dev_pilot100",
                     "ci_ground_truth.json")
if not os.path.exists(BATCH):
    sys.exit(f"no converted batch at {BATCH} — see citation_integrity_bench.py")
gt = json.load(open(BATCH, encoding="utf-8"))["claims"]

def proposed(raw, key):
    """Same rewrite as _claim_text, but co-citations become their own markers
    instead of being deleted."""
    n = [0]
    def sib(m):
        n[0] += 1
        return f"[[{key}_co{n[0]}]]"
    t = ci._OTHER_GROUP.sub(sib, raw)
    t = ci._OTHER_PAREN.sub(sib, t)
    t = ci._OTHER_BARE.sub(sib, t)
    t = ci._CIT_GROUP.sub(f"[[{key}]]", t)
    t = ci._CIT_BARE.sub(f"[[{key}]]", t)
    t = ci._WS.sub(" ", t).strip()
    t = re.sub(r"\s+([,;.])", r"\1", t)
    t = re.sub(r"\(\s*\)|\[\s*\]", "", t).strip()
    if t.endswith(f"[[{key}]]"):
        t += "."
    return t

def claim_for(text, key):
    """The claim carrying `key` after the production segmenter, and the total
    number of claims the paragraph produced."""
    claims = td.extract_claims(text)
    mine = [c for c in claims if key in (c.get("markers") or [])]
    return (mine[0]["text"] if mine else None), len(claims)

rows = []
for key, g in sorted(gt.items()):
    cc, _ = ci._row_co_citation(g)
    raw = g["annotated_span"]
    # baseline rebuilt from the SAME base as the proposal, so the only
    # difference between them is the sibling markers
    cur = ci._claim_text({"citing_paragraph": raw,
                          "citation_context": [{"text": raw, "start": 0,
                                                "end": len(raw)}]}, key, "span")[0]
    prop = proposed(raw, key)
    if cur is None:
        continue
    cur_c, cur_n = claim_for(cur, key)
    prop_c, prop_n = claim_for(prop, key)
    rows.append({"key": key, "cls": cc["class"], "label": g["label"],
                 "cur": cur_c, "prop": prop_c, "cur_n": cur_n, "prop_n": prop_n,
                 "cur_w": len((cur_c or "").split()),
                 "prop_w": len((prop_c or "").split())})

from collections import Counter
print(f"{'class':18s} {'rows':>4s} {'claim text changed':>18s} {'narrowed':>9s} "
      f"{'lost the marker':>16s} {'extra own claims':>17s}")
for cls in ["single", "shared_spot", "siblings_in_span", "both"]:
    sub = [r for r in rows if r["cls"] == cls]
    if not sub: continue
    changed = sum(1 for r in sub if r["cur"] != r["prop"])
    narrowed = sum(1 for r in sub if r["prop"] and r["cur"] and r["prop_w"] < r["cur_w"])
    lost = sum(1 for r in sub if r["prop"] is None)
    extra = sum(r["prop_n"] - r["cur_n"] for r in sub)
    print(f"{cls:18s} {len(sub):4d} {changed:18d} {narrowed:9d} {lost:16d} {extra:17d}")

print("\nrows where the claim under test actually narrows:")
for r in rows:
    if r["prop"] and r["cur"] and r["prop_w"] < r["cur_w"]:
        print(f"\n  {r['key']}  [{r['cls']}, {r['label']}]  {r['cur_w']} -> {r['prop_w']} words")
        print(f"    now : {r['cur'][:200]}")
        print(f"    then: {r['prop'][:200]}")

bad = [r for r in rows if r["prop"] is None]
if bad:
    print(f"\nROWS THAT LOSE THE MARKER ENTIRELY ({len(bad)}):")
    for r in bad: print(f"  {r['key']} [{r['cls']}]")

shrunk = [r for r in rows if r["prop"] and r["cur"] and r["prop_w"] < 5]
if shrunk:
    print(f"\nROWS WHOSE CLAIM BECOMES A FRAGMENT (<5 words) ({len(shrunk)}):")
    for r in shrunk: print(f"  {r['key']} [{r['cls']}]: {r['prop']!r}")
