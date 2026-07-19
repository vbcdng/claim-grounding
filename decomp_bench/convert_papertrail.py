#!/usr/bin/env python3
"""Convert papertrail source_claims caches (schema 3-8, incl. the pre-migration
monorepo format — identical structure) to the canonical decomp_bench JSONL.

Usage:
  convert_papertrail.py <source_claims dir | single .json file> \
      --corpus paper1 --tool papertrail-flashlite-prejunk [--tool-version SHA] \
      [--out-dir decomp_bench/runs]

Auto-flags each claim with the repo's $0 heuristics (junk / cite-header /
ref-fragment). No LLM, no network.
"""
import argparse
import glob
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # repo root

from modules.papertrail.matcher import _is_citation_header, _is_reference_fragment
from modules.papertrail.source_decomposer import _is_junk_claim

# bench-local ADVISORY flag: claims about the publication artifact itself
# (ISSN/DOI/copyright/licensing), which the pipeline's junk filter doesn't
# catch once the LLM rephrases boilerplate as a full sentence.
import re
_META_RE = re.compile(
    r"\bISSN\b|\bDOI\b|doi\.org|copyright\s*©|©\s*\d{4}|\bcopyright \d{4}\b|"
    r"\ball rights reserved\b|\bcreative commons\b|\bCC BY\b|"
    r"\bopen.access article\b|\blicensee\b|\breprints?\s+and\s+permissions?\b",
    re.I)


def auto_flags(text: str) -> list:
    flags = []
    if _is_junk_claim(text):
        flags.append("junk")
    if _is_citation_header(text):
        flags.append("cite-header")
    if _is_reference_fragment(text):
        flags.append("ref-fragment")
    if _META_RE.search(text):
        flags.append("meta")
    return flags


def convert_file(path: str, corpus: str, tool: str, version: str,
                 doc_id: str = None):
    d = json.load(open(path, encoding="utf-8"))
    doc_id = doc_id or d.get("key") or d.get("paper_id") or os.path.basename(path)
    sent_index = {s["text"]: i for i, s in enumerate(d.get("sentences", []))}
    rows = []
    for c in d.get("claims", []):
        ev = c.get("evidence", []) or []
        order = min((sent_index[e] for e in ev if e in sent_index), default=-1)
        rows.append({
            "claim_id": c.get("id") or f"{doc_id}_{len(rows)}",
            "doc_id": doc_id,
            "corpus": corpus,
            "tool": tool,
            "tool_version": version,
            "claim": c.get("text", ""),
            "evidence": ev,
            "evidence_pages": c.get("evidence_pages", []) or [],
            "order": order,
            "auto_flags": auto_flags(c.get("text", "")),
            "human_ok": None,
            "llm_ok": None,
            "llm_reason": "",
            "note": "",
        })
    doc_stats = {
        "doc_id": doc_id,
        "filename": d.get("filename", ""),
        "num_sentences": d.get("num_sentences", len(d.get("sentences", []))),
        "source_text_chars": d.get("source_text_chars", 0),
        "num_pages": d.get("num_pages", 0),
        "cache_schema": d.get("schema"),
        "n_claims": len(rows),
    }
    return rows, doc_stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="source_claims dir or a single cache .json")
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--tool", required=True)
    ap.add_argument("--tool-version", default="unknown")
    ap.add_argument("--out-dir", default=os.path.join(_HERE, "runs"))
    a = ap.parse_args()

    files = ([a.src] if os.path.isfile(a.src)
             else sorted(glob.glob(os.path.join(a.src, "*.json"))))
    if not files:
        sys.exit(f"no .json files under {a.src}")

    os.makedirs(a.out_dir, exist_ok=True)
    base = f"{a.corpus}__{a.tool}"
    out_jsonl = os.path.join(a.out_dir, base + ".jsonl")
    out_docs = os.path.join(a.out_dir, "docs_" + base + ".json")

    # disambiguate citation keys shared by two different PDFs (real case:
    # two randcorporation2025 files) — suffix with the paper_id prefix
    keys = {}
    for f in files:
        d = json.load(open(f, encoding="utf-8"))
        keys.setdefault(d.get("key") or d.get("paper_id")
                        or os.path.basename(f), []).append(f)
    doc_ids = {}
    for key, fs in keys.items():
        for f in fs:
            if len(fs) == 1:
                doc_ids[f] = key
            else:
                pid = json.load(open(f, encoding="utf-8")).get("paper_id", "")
                doc_ids[f] = f"{key}~{pid[:8]}"

    all_rows, all_docs = [], []
    for f in files:
        rows, doc = convert_file(f, a.corpus, a.tool, a.tool_version,
                                 doc_id=doc_ids[f])
        all_rows.extend(rows)
        all_docs.append(doc)
    all_rows.sort(key=lambda r: (r["doc_id"], r["order"] if r["order"] >= 0 else 10**9))

    with open(out_jsonl, "w", encoding="utf-8") as fh:
        for r in all_rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    json.dump(all_docs, open(out_docs, "w", encoding="utf-8"),
              indent=1, ensure_ascii=False)

    flagged = sum(1 for r in all_rows if r["auto_flags"])
    print(f"{out_jsonl}: {len(all_rows)} claims from {len(all_docs)} docs; "
          f"auto-flagged {flagged} ({flagged/max(1,len(all_rows)):.1%})")
    print(f"sidecar: {out_docs}")


if __name__ == "__main__":
    main()
