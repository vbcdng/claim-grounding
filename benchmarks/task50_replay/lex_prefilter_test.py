#!/usr/bin/env python3
"""Task #50 half two, question 3 feasibility: could a word-overlap pre-filter
skip (part, source) probes without ever skipping a probe that proved a winner?

Winner pairs  = (component, proving source) from every flipped rescue block
                across all runs on disk (the rule must keep 100% of these).
Also collected: found parts from NON-flip blocks (they feed the card display).
Waste pairs   = components from split calls in the task50ctl gate logs that
                matched no recorded block (rescue found nothing) x every
                source in that run (upper bound of what a skip could save).

For each pair: n shared content tokens with the source raw text, and the
share of the part's tokens present. No LLM calls; pdftotext for PDFs.
"""
import json, glob, os, re, subprocess, sys
sys.path.insert(0, os.getcwd())
from modules.papertrail.matcher import _LEX_TOKEN_RE, _canon_tok

CACHE = {}
def source_text(path):
    if path in CACHE: return CACHE[path]
    t = ""
    try:
        if path.lower().endswith(".pdf"):
            t = subprocess.run(["pdftotext", path, "-"], capture_output=True,
                               text=True, timeout=120).stdout
        else:
            t = open(path, errors="replace").read()
    except Exception as e:
        print("  !! cannot read", path, e)
    CACHE[path] = t
    return t

def toks(s):
    return {_canon_tok(t) for t in _LEX_TOKEN_RE.findall(s.lower())}

def overlap(part, srctoks):
    pt = toks(part)
    if not pt: return (0, 0.0)
    inter = pt & srctoks
    return (len(inter), len(inter) / len(pt))

roots = ["data", "/home/moje/Documents/python_projects/claim-grounding/data"]
paths = sorted(set(os.path.realpath(p) for r in roots
                   for p in glob.glob(os.path.join(r, "*", "analysis.json"))))

winner_rows, display_rows = [], []
for p in paths:
    try: a = json.load(open(p))
    except Exception: continue
    tc = a.get("text_claims")
    if not isinstance(tc, list) or not tc or not isinstance(tc[0], dict): continue
    sdir = (a.get("metadata") or {}).get("sources_dir") or ""
    fmap = {s["paper_id"]: s.get("filename") for s in (a.get("sources") or [])
            if isinstance(s, dict)}
    for c in tc:
        cc = c.get("component_check")
        if not isinstance(cc, dict): continue
        for e in (cc.get("evidence") or []):
            comp, pid = e.get("component"), e.get("paper_id")
            fn = fmap.get(pid)
            if not comp or not fn: continue
            fp = os.path.join(sdir, fn)
            if not os.path.exists(fp): continue
            row = (os.path.basename(os.path.dirname(p)), comp, fp)
            (winner_rows if cc.get("rescued") else display_rows).append(row)

def extract_json_loose(t):
    m = re.search(r"\{.*\}", t or "", re.S)
    if not m: return None
    try: return json.loads(m.group(0))
    except Exception: return None

waste_rows = []
for d in sorted(glob.glob("data/gate_task50ctl_*")):
    lp = d + "/llm_calls.jsonl"
    if not os.path.exists(lp): continue
    a = json.load(open(d + "/analysis.json"))
    sdir = a["metadata"].get("sources_dir") or ""
    files = [os.path.join(sdir, s["filename"]) for s in a.get("sources") or []]
    blocks = [set((c["component_check"].get("found") or [])
                  + (c["component_check"].get("missing") or []))
              for c in a["text_claims"] if isinstance(c.get("component_check"), dict)]
    for line in open(lp):
        c = json.loads(line)
        if c.get("purpose") != "component_check": continue
        o = extract_json_loose(c.get("response_text"))
        comps = (o or {}).get("components")
        if not isinstance(comps, list): continue
        comps = [str(x).strip() for x in comps if str(x).strip()][:4]
        if set(comps) in blocks: continue          # matched a block => not waste
        for comp in comps:
            for fp in files:
                if os.path.exists(fp):
                    waste_rows.append((os.path.basename(d), comp, fp))

def stats(rows, name, show_low=0):
    res = []
    for run, comp, fp in rows:
        st = toks(source_text(fp))
        n, frac = overlap(comp, st)
        res.append((n, frac, run, comp[:60], os.path.basename(fp)[:40]))
    res.sort()
    print(f"\n== {name}: {len(res)} (part, source) pairs ==")
    if not res: return
    import statistics
    ns = [r[0] for r in res]; fs = [r[1] for r in res]
    print(f" shared-token count: min={min(ns)} median={statistics.median(ns)} max={max(ns)}")
    print(f" share of part tokens present: min={min(fs):.2f} median={statistics.median(fs):.2f}")
    for r in res[:show_low]:
        print(f"  low: n={r[0]} frac={r[1]:.2f} [{r[2]}] {r[3]!r} <- {r[4]}")
    for thr_n in (1, 3, 5):
        print(f" pairs with < {thr_n} shared tokens: {sum(1 for x in ns if x < thr_n)}")
    for thr_f in (0.3, 0.5, 0.7):
        print(f" pairs with < {thr_f:.0%} of part tokens present: {sum(1 for x in fs if x < thr_f)}")

stats(winner_rows, "WINNERS (flip-proving parts; rule must keep ALL)", show_low=6)
stats(display_rows, "DISPLAY (found parts on non-flip cards)", show_low=4)
stats(waste_rows, "WASTE candidates (nothing-found attempts x all run sources)", show_low=0)
