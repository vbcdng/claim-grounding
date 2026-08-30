#!/usr/bin/env python3
"""Build the frozen round-1 input file for the task #15 labeling panel.

Reads the 19 rows frozen in docs/TASK15_LOOP.md (15 retreat contested rows +
4 fair-question hard misfires from batch_dev_pilot100), attaches each row's
full source text (PDFs extracted via pdftotext), and writes:

  benchmarks/labeler/rounds/round1/rows.jsonl   — one row per line
  benchmarks/labeler/rounds/round1/build_report.md — plain-language report

Row schema (one JSON object per line):
  row_id      "retreat:b008" | "pilot100:cidev0060"
  pile        "retreat_contested" | "fair_misfire"
  claim_text  the sentence being judged
  context     the paragraph it sits in
  old_label   what the current answer key says
  sources     [{name, chars, garble_ratio, text}]
  notes       anything a rater should know (never the answer)

No LLM calls, no network. Re-running overwrites the two output files.
"""
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(REPO, "benchmarks", "labeler", "rounds", "round1")

RETREAT_IDS = ["b008", "b014", "b039", "b047", "b071", "b081", "b086", "b089",
               "b094", "b117", "b120", "b121", "b128", "b132", "b137"]
MISFIRE_IDS = ["cidev0060", "cidev0063", "cidev0072", "cidev0080"]
PILOT_BATCH = os.path.join(REPO, "data", "citation_integrity", "batch_dev_pilot100")


def pdf_to_text(path):
    """Extract PDF text with pdftotext; retry with -layout if the plain
    extraction comes out letter-spaced (the garble class the pipeline hit)."""
    def run(extra):
        r = subprocess.run(["pdftotext"] + extra + [path, "-"],
                           capture_output=True, text=True, timeout=300)
        return r.stdout if r.returncode == 0 else ""

    text = run([])
    if garble_ratio(text) > 0.4:
        alt = run(["-layout"])
        if alt and garble_ratio(alt) < garble_ratio(text):
            text = alt
    return text


def garble_ratio(text):
    """Fraction of whitespace-separated tokens that are single characters —
    high values mean letter-spaced extraction garbage."""
    toks = text.split()
    if len(toks) < 50:
        return 1.0 if not toks else 0.0
    singles = sum(1 for t in toks if len(t) == 1)
    return singles / len(toks)


def read_source(path):
    full = os.path.join(REPO, path) if not os.path.isabs(path) else path
    if full.lower().endswith(".pdf"):
        text = pdf_to_text(full)
    else:
        with open(full, errors="replace") as f:
            text = f.read()
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return {"name": os.path.basename(full), "chars": len(text),
            "garble_ratio": round(garble_ratio(text), 3), "text": text}


def retreat_rows():
    pool = json.load(open(os.path.join(REPO, "retreat_pilot", "pool", "pool_items.json")))
    items = {i["id"]: i for i in pool["items"]}
    paths = json.load(open(os.path.join(REPO, "retreat_pilot", "pool", "source_paths.json")))["paths"]
    rows = []
    for rid in RETREAT_IDS:
        it = items[rid]
        src = read_source(paths[rid])
        rows.append({
            "row_id": f"retreat:{rid}",
            "pile": "retreat_contested",
            "claim_text": it["claim_text"],
            "context": it.get("paragraph", ""),
            "old_label": "contested (the author's July two-label scoring could not settle it)",
            "sources": [src],
            "notes": f"source tier: {it.get('publication_tier', '?')}; cited as: {it['source'].get('label', '?')}",
        })
    return rows


def misfire_rows():
    gt = json.load(open(os.path.join(PILOT_BATCH, "ci_ground_truth.json")))["claims"]
    rows = []
    for cid in MISFIRE_IDS:
        r = gt[cid]
        src = read_source(os.path.join(PILOT_BATCH, "sources", f"{cid}.txt"))
        rows.append({
            "row_id": f"pilot100:{cid}",
            "pile": "fair_misfire",
            "claim_text": re.sub(r"\s*\[\[\w+\]\]\s*", " ", r["claim_text"]).strip(),
            "context": r.get("citing_paragraph", ""),
            "old_label": f"{r['label']} (strict side: {r.get('strict_side', '?')})",
            "sources": [src],
            "notes": f"ci_id: {r['ci_id']}; the tool raised a red card here on the 2 August run",
        })
    return rows


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = retreat_rows() + misfire_rows()
    out = os.path.join(OUT_DIR, "rows.jsonl")
    with open(out, "w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    lines = [
        "# Round-1 input rows — build report",
        "",
        "This file describes `rows.jsonl` in the same folder: the 19 rows the labeling panel will judge in round 1, each carrying its claim, the paragraph around it, and the full text of its cited source. The list itself was frozen on 2026-08-07 in `docs/TASK15_LOOP.md`; this build only attaches the source texts.",
        "",
        "| Row | Pile | Source file | Source length (characters) | Extraction quality |",
        "|---|---|---|---|---|",
    ]
    problems = []
    for row in rows:
        s = row["sources"][0]
        quality = "ok"
        if s["chars"] < 2000:
            quality = "VERY SHORT — check by hand"
            problems.append(f"{row['row_id']}: source text is only {s['chars']} characters")
        elif s["garble_ratio"] > 0.3:
            quality = "possibly garbled — check by hand"
            problems.append(f"{row['row_id']}: {int(s['garble_ratio']*100)}% of words are single letters")
        lines.append(f"| {row['row_id']} | {row['pile']} | {s['name']} | {s['chars']:,} | {quality} |")
    lines.append("")
    if problems:
        lines.append("**Rows needing a look before the panel runs:**")
        lines.extend(f"- {p}" for p in problems)
    else:
        lines.append("No extraction problems found: every source came out long enough and readable.")
    lines.append("")
    total = sum(r["sources"][0]["chars"] for r in rows)
    lines.append(f"Total source text across all 19 rows: {total:,} characters (roughly {total//4:,} tokens). Each panel model reads each row's source once, so one full panel pass over all rows sends roughly {total//4:,} tokens to each model.")
    with open(os.path.join(OUT_DIR, "build_report.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {len(rows)} rows -> {out}")
    for p in problems:
        print("PROBLEM:", p)


if __name__ == "__main__":
    sys.exit(main())
