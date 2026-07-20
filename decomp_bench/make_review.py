#!/usr/bin/env python3
"""Generate human-readable review files from a canonical JSONL.

Outputs (next to the JSONL):
  review_<base>_ALL.txt     every claim, document order  (fast eyeball)
  review_<base>_SAMPLE.txt  fixed-seed stratified sample (careful 15-min pass)
  claims_<base>.txt         bare claim texts, one/line   (for meld tool-vs-tool)
  document_<doc>.txt        with --doc KEY: that doc's sentences one/line
                            (for meld document-vs-claims coverage view)

Prints ready-to-paste meld commands. Same line format in ALL and SAMPLE, so
merge_review.py handles both.
"""
import argparse
import json
import os
import random


def load(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def fmt_line(r):
    h = r.get("human_ok") or " "
    l = r.get("llm_ok") or " "
    flags = ",".join(r.get("auto_flags", []))
    # evidence-mode rows carry a prebuilt "claim => sentence" display string
    body = " ".join((r.get("display") or r["claim"]).split())
    note = r.get("note", "")
    return (f"{r['claim_id']} | H:[{h}] | L:[{l}] | A:[{flags}] | "
            f"{body} | note:{note}")


def stratified_sample(rows, n, seed):
    by_doc = {}
    for r in rows:
        by_doc.setdefault(r["doc_id"], []).append(r)
    rng = random.Random(seed)
    queues = []
    for doc in sorted(by_doc):
        claims = by_doc[doc][:]
        rng.shuffle(claims)
        queues.append(claims)
    rng.shuffle(queues)
    picked = []
    while queues and len(picked) < n:          # round-robin across docs
        for q in list(queues):
            if len(picked) >= n:
                break
            picked.append(q.pop(0))
            if not q:
                queues.remove(q)
    order = {id(r): i for i, r in enumerate(rows)}
    picked.sort(key=lambda r: order[id(r)])    # back to document order
    return picked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl")
    ap.add_argument("--n", type=int, default=50, help="sample size")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--doc", help="also write document_<doc>.txt for the "
                                  "coverage meld view (needs the original "
                                  "cache dir via --cache-dir)")
    ap.add_argument("--cache-dir", help="source_claims dir with sentences")
    a = ap.parse_args()

    rows = load(a.jsonl)
    out_dir = os.path.dirname(os.path.abspath(a.jsonl))
    base = os.path.splitext(os.path.basename(a.jsonl))[0]

    p_all = os.path.join(out_dir, f"review_{base}_ALL.txt")
    p_smp = os.path.join(out_dir, f"review_{base}_SAMPLE.txt")
    p_cl = os.path.join(out_dir, f"claims_{base}.txt")

    with open(p_all, "w", encoding="utf-8") as fh:
        fh.write("\n".join(fmt_line(r) for r in rows) + "\n")
    sample = stratified_sample(rows, a.n, a.seed)
    with open(p_smp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(fmt_line(r) for r in sample) + "\n")
    with open(p_cl, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(f"[{r['doc_id']}] " + " ".join(r["claim"].split()) + "\n")

    print(f"wrote {p_all}  ({len(rows)} claims)")
    print(f"wrote {p_smp}  ({len(sample)} claims, seed {a.seed})")
    print(f"wrote {p_cl}")

    if a.doc and a.cache_dir:
        import glob as _g
        for f in _g.glob(os.path.join(a.cache_dir, "*.json")):
            d = json.load(open(f, encoding="utf-8"))
            if d.get("key") == a.doc or d.get("paper_id") == a.doc:
                p_doc = os.path.join(out_dir, f"document_{a.doc}.txt")
                with open(p_doc, "w", encoding="utf-8") as fh:
                    for s in d.get("sentences", []):
                        fh.write(" ".join(s["text"].split()) + "\n")
                p_dc = os.path.join(out_dir, f"claims_{base}_{a.doc}.txt")
                with open(p_dc, "w", encoding="utf-8") as fh:
                    for r in rows:
                        if r["doc_id"] == a.doc:
                            fh.write(" ".join(r["claim"].split()) + "\n")
                print(f"\ncoverage view:\n  meld {p_doc} {p_dc}")
                break
        else:
            print(f"--doc {a.doc}: not found in {a.cache_dir}")

    print("\ntool-vs-tool view (after converting a second tool):")
    print(f"  meld {p_cl} <claims_<other>.txt>")
    print(f"\nedit H:[ ]->H:[y|n] + note: in {os.path.basename(p_smp)} "
          f"(or _ALL), then:\n  python3 decomp_bench/merge_review.py {p_smp}")


if __name__ == "__main__":
    main()
