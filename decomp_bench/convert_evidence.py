#!/usr/bin/env python3
"""Evidence mode: convert a finished run's analysis.json into the canonical
decomp_bench JSONL — one row per (claim x cited source) evidence pair — so a
human can eyeball SUPPORTING SENTENCES for first-sight bullshit (garble,
fragments, boilerplate, off-topic picks).

Usage:
  convert_evidence.py <run dir with analysis.json> --corpus eggs \
      [--tool papertrail-evidence] [--out-dir decomp_bench/runs]

The review line (via make_review.py) shows:  [verdict/method] claim => sentence
Auto-flags run on the SENTENCE (the thing under review). No LLM, no network.
"""
import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root

from modules.papertrail.matcher import (_is_citation_header, _is_reference_fragment,
                                        _degenerate)
from convert_papertrail import _META_RE


def _flags(sentence: str) -> list:
    flags = []
    if _degenerate(sentence):
        flags.append("fragment")
    if _is_citation_header(sentence):
        flags.append("cite-header")
    if _is_reference_fragment(sentence):
        flags.append("ref-fragment")
    if _META_RE.search(sentence):
        flags.append("meta")
    return flags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", help="run output dir containing analysis.json")
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--tool", default="papertrail-evidence")
    ap.add_argument("--out-dir", default=os.path.join(_HERE, "runs"))
    a = ap.parse_args()

    analysis = json.load(open(os.path.join(a.run_dir, "analysis.json"),
                              encoding="utf-8"))
    meta = analysis.get("metadata", {})
    version = meta.get("model", "unknown")
    rows = []
    for order, tc in enumerate(analysis.get("text_claims", [])):
        if tc.get("verdict") in ("own", "omitted"):
            continue
        evs = tc.get("evidences") or ([tc["evidence"]] if tc.get("evidence") else [])
        for k, e in enumerate(evs):
            sent = (e.get("sentence") or "").strip()
            doc = e.get("source_title") or e.get("paper_id") or "?"
            display = (f"[{tc.get('verdict','?')}/{tc.get('method','?')}"
                       f"{'' if e.get('supported') else ' | this src: no'}] "
                       f"{' '.join(tc.get('text','').split())}  =>  "
                       f"{' '.join(sent.split()) or '(no sentence)'}")
            rows.append({
                "claim_id": f"{tc.get('id','t?')}_{k}",
                "doc_id": str(doc)[:60],
                "corpus": a.corpus,
                "tool": a.tool,
                "tool_version": str(version),
                "claim": tc.get("text", ""),
                "evidence": [sent] if sent else [],
                "evidence_pages": [e.get("page")] if e.get("page") else [],
                "order": order,
                "auto_flags": _flags(sent) if sent else ["no-sentence"],
                "human_ok": None,
                "llm_ok": None,
                "llm_reason": "",
                "note": "",
                "verdict": tc.get("verdict"),
                "method": tc.get("method"),
                "reason": tc.get("reason") or e.get("reason") or "",
                "display": display,
            })

    os.makedirs(a.out_dir, exist_ok=True)
    base = f"{a.corpus}__{a.tool}"
    out_jsonl = os.path.join(a.out_dir, base + ".jsonl")
    with open(out_jsonl, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    flagged = sum(1 for r in rows if r["auto_flags"])
    print(f"{out_jsonl}: {len(rows)} evidence rows from "
          f"{len({r['claim_id'].rsplit('_',1)[0] for r in rows})} claims; "
          f"auto-flagged {flagged}")


if __name__ == "__main__":
    main()
