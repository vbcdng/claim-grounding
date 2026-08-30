#!/usr/bin/env python3
"""Task #15 adjudication funnel — sort panel verdicts into agree / disagree / tail.

Reads the rows file and the panel's verdicts.jsonl and writes:
  <out-dir>/funnel.json     — machine-readable sorting
  <out-dir>/round_summary.md — plain-language summary for the author

Sorting rule (per the approved plan):
  - unanimous  : every answering model gives the same strict label AND at
                 least 2 models answered -> proposed label (still only a
                 proposal; the author rules before any answer key changes)
  - split      : answering models disagree -> goes to the tie-break round
  - insufficient: fewer than 2 models answered -> re-run or goes to the author

Refusals are counted and reported, never treated as votes (task #37 rule).
No LLM calls. Usage:
  python3 benchmarks/labeler/funnel.py --rows .../rows.jsonl \
      --verdicts .../verdicts.jsonl --out-dir .../round1
"""
import argparse
import json
import os
from collections import defaultdict

LABEL_WORDS = {
    "pass": "pass (true as written)",
    "fail_contradicted": "fail — the source states something different",
    "fail_unproven": "fail — the source is silent on an asserted part",
    "invalid": "invalid — source or claim unusable",
}


def load_jsonl(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def sort_rows(rows, verdicts):
    by_row = defaultdict(list)
    for v in verdicts:
        by_row[v["row_id"]].append(v)
    result = []
    for row in rows:
        vs = by_row.get(row["row_id"], [])
        answered = [v for v in vs if v.get("answered")]
        refused = [v for v in vs if not v.get("answered")]
        labels = {v["strict_label"] for v in answered}
        if len(answered) < 2:
            status = "insufficient"
            proposed = None
        elif len(labels) == 1:
            status = "unanimous"
            proposed = labels.pop()
        else:
            status = "split"
            proposed = None
        unverified = [
            {"model": v["model"], "part": p["part"], "quote": p["quote"]}
            for v in answered for p in v.get("parts", [])
            if p.get("quote") and not p.get("quote_verified")
        ]
        result.append({
            "row_id": row["row_id"], "pile": row["pile"],
            "claim_text": row.get("claim_text", ""),
            "old_label": row["old_label"], "status": status,
            "proposed_label": proposed,
            "votes": {v["model"]: v["strict_label"] for v in answered},
            "refusals": [v["model"] for v in refused],
            "unverified_quotes": unverified,
            "hard_notes": {v["model"]: v.get("hard_note") for v in answered
                           if v.get("hard_note")},
        })
    return result


def write_summary(sorted_rows, verdicts, path):
    n_refusals = sum(1 for v in verdicts if not v.get("answered"))
    n_answers = sum(1 for v in verdicts if v.get("answered"))
    buckets = defaultdict(list)
    for r in sorted_rows:
        buckets[r["status"]].append(r)
    n_unverified = sum(len(r["unverified_quotes"]) for r in sorted_rows)

    lines = [
        "# Panel round summary",
        "",
        f"The panel produced {n_answers} answers and {n_refusals} refusals across {len(sorted_rows)} rows. A refusal means the model never gave a verdict — it is counted here and excluded from every vote, so no refusal can masquerade as an 'unsupported' answer.",
        "",
        f"How the rows sorted: **{len(buckets['unanimous'])} unanimous** (every answering model gave the same label — these become proposed labels for the author to confirm), **{len(buckets['split'])} split** (the models disagree — these go to the tie-break round), **{len(buckets['insufficient'])} with too few answers** to sort (fewer than two models answered).",
        "",
    ]
    if n_unverified:
        lines += [f"Quote check: {n_unverified} copied proof sentences could not be found word-for-word in the source text. Each is listed under its row below — a verdict resting only on an unfindable quote should not be trusted until a human looks.", ""]
    else:
        lines += ["Quote check: every copied proof sentence was found word-for-word in its source text.", ""]

    for status, title in (("unanimous", "Unanimous rows — proposed labels"),
                          ("split", "Split rows — going to the tie-break round"),
                          ("insufficient", "Rows with too few answers")):
        rows = buckets[status]
        if not rows:
            continue
        lines += [f"## {title}", ""]
        for r in rows:
            votes = ", ".join(f"{m.split('/')[-1]}: {LABEL_WORDS.get(l, l)}"
                              for m, l in r["votes"].items()) or "no answers"
            lines.append(f"**{r['row_id']}** (old label: {r['old_label']})")
            if r.get("claim_text"):
                lines.append(f"- the claim: \"{r['claim_text']}\"")
            if r["proposed_label"]:
                lines.append(f"- proposed label: **{LABEL_WORDS[r['proposed_label']]}**")
            lines.append(f"- votes: {votes}")
            if r["refusals"]:
                lines.append(f"- refused to answer: {', '.join(r['refusals'])}")
            for u in r["unverified_quotes"]:
                lines.append(f"- UNFINDABLE QUOTE from {u['model'].split('/')[-1]} on part \"{u['part'][:80]}\": \"{(u['quote'] or '')[:120]}\"")
            for m, note in r["hard_notes"].items():
                lines.append(f"- {m.split('/')[-1]} found it hard: {note}")
            lines.append("")
    lines.append("No label in any answer key has been changed by this round. Every proposed label above waits for the author's ruling, and each accepted change will carry its era-stamp (old label, date, reason, rubric version).")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True)
    ap.add_argument("--verdicts", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    rows = load_jsonl(a.rows)
    verdicts = load_jsonl(a.verdicts)
    sorted_rows = sort_rows(rows, verdicts)
    os.makedirs(a.out_dir, exist_ok=True)
    with open(os.path.join(a.out_dir, "funnel.json"), "w") as f:
        json.dump({"rows": sorted_rows}, f, indent=1, ensure_ascii=False)
    write_summary(sorted_rows, verdicts, os.path.join(a.out_dir, "round_summary.md"))
    counts = defaultdict(int)
    for r in sorted_rows:
        counts[r["status"]] += 1
    print(dict(counts))


if __name__ == "__main__":
    main()
