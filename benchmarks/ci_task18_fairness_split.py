"""Task #18: split every arm's wrong-red-card count by whether the row asks a FAIR
question. Pure analysis, $0, no LLM calls, writes nothing. Fable-safe: prints ids,
labels, citation COUNTS, verdicts and methods only — never row or source text.

Why this exists. On 2026-08-11 the one verdict difference between the merged wording
and round 3 turned out to be pilot100/cidev0020 — a sentence whose author cited FOUR
papers, of which the benchmark hands the tool exactly one. The tool cannot answer that
correctly, so neither its red card nor its green card means anything, yet round 3's
scoreboard credited clearing it as a win. The project's standing rule says to split
rows by fairness BEFORE drawing conclusions, so this applies that rule to the whole
scoreboard rather than to the single row that happened to be noticed.

"Fair" here = the citing sentence cited exactly one source, so the folder contains all
the evidence the author actually relied on. "Unfair" = it cited several and the
converter kept one (task #32 repairs this).

Usage: venv/bin/python3 benchmarks/ci_task18_fairness_split.py
"""
import json, os, sys

ROOT = "/home/moje/Documents/python_projects/claim-grounding"
sys.path.insert(0, os.path.join(ROOT, "benchmarks"))
sys.path.insert(0, ROOT)
from citation_integrity_bench import MAJOR, _collapse, _row_co_citation
from wice_bench import _tool_bucket
from ci_blind_compare import sides as blind_sides

BASE = f"{ROOT}/data/citation_integrity"
gt = json.load(open(f"{BASE}/batch_dev_pilot100/ci_ground_truth.json"))["claims"]
blind = json.load(open(f"{BASE}/c0_blind_sonnet.json"))["votes"]

ARMS = [
    ("baseline", "batch_dev_pilot100_run_gemma_0802"),
    ("round 1", "batch_dev_pilot100_run_task18_r1"),
    ("round 2", "batch_dev_pilot100_run_task18_r2"),
    ("round 3", "batch_dev_pilot100_run_task18_r3"),
    ("merged", "batch_dev_pilot100_run_task18_r4merged"),
]


def canonical_single_cited():
    """The benchmark's OWN fairness field, which is authoritative.

    `_row_co_citation` reads the ground truth's `co_citation` block, or recomputes it
    from the stored annotated span when the row predates that field. Its docstring
    notes the recomputation can only UNDERSTATE how multi-cited a row was, never
    overstate it — so a row it calls multi-cited definitely is one.

    Checked against the sibling-citation records: they agree on 48 of the 50 accurate
    rows, and the two exceptions (cidev0038, cidev0085) are multi-cited here but
    absent from those records, so deriving fairness from the records alone would have
    counted them as fair. Neither is wrongly red in any arm, so the numbers below do
    not change — but the canonical field is what this script uses.
    """
    return {k: _row_co_citation(g)[0]["is_single_cited"] for k, g in gt.items()}


def citation_counts():
    """rid count per pilot100 row, from the sibling-citation records (for display)."""
    out = {}
    for fname in ("sibling_recovery.json", "sibling_repair_report.json"):
        p = f"{BASE}/{fname}"
        if not os.path.exists(p):
            continue
        def walk(o):
            if isinstance(o, dict):
                k = o.get("key", "")
                if k.startswith("cidev") and o.get("batch") == "pilot100":
                    n = o.get("citations") or len(o.get("rids") or [])
                    if n:
                        out.setdefault(k, n)
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(json.load(open(p)))
    return out


CITES = citation_counts()
SINGLE = canonical_single_cited()


def fair(key):
    """True when the row's sentence cited exactly one source."""
    return SINGLE.get(key, True)


def index(dirname):
    p = f"{BASE}/{dirname}/analysis.json"
    if not os.path.exists(p):
        return None
    by_key = {}
    for c in json.load(open(p)).get("text_claims", []):
        for k in c.get("markers") or []:
            by_key.setdefault(k, c)
    return by_key


acc_rows = sorted(k for k, g in gt.items() if g["label"] == "ACCURATE")
major_rows = sorted(k for k, g in gt.items() if g["label"] in MAJOR)

print("Rows the answer key calls accurate:", len(acc_rows),
      f"— fair {sum(1 for k in acc_rows if fair(k))},"
      f" multi-cited {sum(1 for k in acc_rows if not fair(k))}")
print("Rows the answer key calls seriously wrong:", len(major_rows),
      f"— fair {sum(1 for k in major_rows if fair(k))},"
      f" multi-cited {sum(1 for k in major_rows if not fair(k))}")
print()
print("Multi-cited accurate rows and how many sources their sentence cited:")
for k in acc_rows:
    if not fair(k):
        n = CITES.get(k)
        n = f"{n} sources cited" if n else "several sources cited (count not in the sibling records)"
        print(f"  {k}: {n}, tool given 1")
print()

hdr = (f"{'arm':<10} {'wrong red: FAIR':>16} {'wrong red: multi':>17} "
       f"{'stricter (also 2nd reader)':>27} {'missed bad, FAIR':>18}")
print(hdr)
print("-" * len(hdr))

rows_out = {}
for name, d in ARMS:
    by_key = index(d)
    if by_key is None:
        print(f"{name:<10} {'(not run yet)':>16}")
        continue
    red = []
    for k in acc_rows:
        c = by_key.get(k)
        if c is None or _collapse(_tool_bucket(c)) == "pass":
            continue
        if c.get("verdict") != "supported":
            red.append(k)
    hard = [k for k in red if blind_sides(blind[k])[0] != "flag"]
    missed = [k for k in major_rows
              if by_key.get(k) is not None
              and _collapse(_tool_bucket(by_key[k])) == "pass"]
    rf = [k for k in red if fair(k)]
    ru = [k for k in red if not fair(k)]
    hf = [k for k in hard if fair(k)]
    mf = [k for k in missed if fair(k)]
    rows_out[name] = dict(red_fair=rf, red_unfair=ru, hard_fair=hf, missed_fair=mf)
    print(f"{name:<10} {len(rf):>16} {len(ru):>17} {len(hf):>27} {len(mf):>18}")

print()
print("Which FAIR accurate rows are wrongly red, per arm (this is the number that matters):")
for name in rows_out:
    print(f"  {name:<10} {rows_out[name]['red_fair']}")
print()
print("Which MULTI-CITED accurate rows are red, per arm (task #32 owns these; not evidence):")
for name in rows_out:
    print(f"  {name:<10} {rows_out[name]['red_unfair']}")

# ---- reconcile with the count published in the loop record ----------------
# The author's way of counting leaves out cidev0031 (they said it is outside their
# field), the six rows where they ruled the red card CORRECT, and cidev0007 (ruling
# of 2026-08-07: its red card is correct behaviour).
AUTHOR_EXCLUDED = {"cidev0031", "cidev0007",
                   "cidev0044", "cidev0046", "cidev0047",
                   "cidev0063", "cidev0074", "cidev0080", "cidev0086"}
print()
print("Counting the author's way (their exclusions applied), split by fairness.")
print("The published figures are 7 -> 5 -> 5 -> 3, so these must reconcile.")
hdr2 = f"{'arm':<10} {'total':>7} {'FAIR':>6} {'multi-cited':>12}   which FAIR rows"
print(hdr2)
print("-" * (len(hdr2) + 20))
for name, d in ARMS:
    by_key = index(d)
    if by_key is None:
        print(f"{name:<10} {'(not run yet)':>7}")
        continue
    red = [k for k in acc_rows
           if by_key.get(k) is not None
           and _collapse(_tool_bucket(by_key[k])) != "pass"
           and by_key[k].get("verdict") != "supported"]
    hard = [k for k in red if blind_sides(blind[k])[0] != "flag"]
    aw = [k for k in hard if k not in AUTHOR_EXCLUDED]
    awf = [k for k in aw if fair(k)]
    awu = [k for k in aw if not fair(k)]
    print(f"{name:<10} {len(aw):>7} {len(awf):>6} {len(awu):>12}   {awf}")
