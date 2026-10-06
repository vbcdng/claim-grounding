#!/usr/bin/env python3
"""Task #15 — does reading a source in numbered pieces give the same answer?

One-variable control: the SAME model, the SAME rubric, the SAME rows, and the
only difference is whether the model read the source in one sitting or in
numbered pieces. Rows the model could read whole are judged both ways and the
two verdicts are compared here, so the piece-by-piece reading is measured
before any large row's verdict is trusted.

No model calls. Usage:
  python3 benchmarks/labeler/compare_read_modes.py \
      --whole benchmarks/labeler/rounds/round1/verdicts_gemma.jsonl \
      --sectioned benchmarks/labeler/rounds/round2/control_gemma_sectioned.jsonl \
      --out benchmarks/labeler/rounds/round2/control_report.md
"""
import argparse
import json
import os

LABEL_WORDS = {
    "pass": "pass (true as written)",
    "fail_contradicted": "fail — the source states something different",
    "fail_unproven": "fail — the source is silent on an asserted part",
    "invalid": "invalid — source or claim unusable",
}


def load(path):
    out = {}
    with open(path) as f:
        for line in f:
            if line.strip():
                rec = json.loads(line)
                out[(rec["row_id"], rec["model"])] = rec
    return out


def compare(whole, sectioned):
    rows = []
    for key, sec in sorted(sectioned.items()):
        who = whole.get(key)
        if who is None:
            continue
        rows.append({
            "row_id": key[0], "model": key[1],
            "whole_answered": bool(who.get("answered")),
            "sectioned_answered": bool(sec.get("answered")),
            "whole_label": who.get("strict_label"),
            "sectioned_label": sec.get("strict_label"),
            "pieces": sec.get("pieces"),
            "scan_quotes_kept": sec.get("scan_quotes_kept"),
            "agree": (bool(who.get("answered")) and bool(sec.get("answered"))
                      and who.get("strict_label") == sec.get("strict_label")),
        })
    return rows


def write_report(rows, path):
    both = [r for r in rows if r["whole_answered"] and r["sectioned_answered"]]
    agree = [r for r in both if r["agree"]]
    lines = [
        "# Does reading the source in numbered pieces change the answer?",
        "",
        "This page compares two readings of the same rows by the same model under the same rulebook. In the first reading the model was given the whole source in one go. In the second the source was cut into numbered pieces, each piece was read on its own for sentences bearing on the claim, and the model then judged the claim on the sentences that reading collected. Nothing else differs, so any difference in the answers comes from the reading method.",
        "",
    ]
    if not both:
        lines += ["No row was judged both ways, so there is nothing to compare yet.", ""]
    else:
        lines += [
            f"Of {len(both)} rows judged both ways, the two readings gave the same answer on {len(agree)} and a different answer on {len(both) - len(agree)}.",
            "",
            "| Row | Whole reading | Piece-by-piece reading | Pieces | Sentences the reading pass kept | Same answer? |",
            "|---|---|---|---|---|---|",
        ]
        for r in both:
            lines.append(
                f"| {r['row_id']} | {LABEL_WORDS.get(r['whole_label'], r['whole_label'])} "
                f"| {LABEL_WORDS.get(r['sectioned_label'], r['sectioned_label'])} "
                f"| {r['pieces']} | {r['scan_quotes_kept']} "
                f"| {'yes' if r['agree'] else 'NO'} |")
        lines.append("")
    unanswered = [r for r in rows if not r["sectioned_answered"]]
    if unanswered:
        lines += [f"{len(unanswered)} of these rows gave no answer at all in the piece-by-piece reading, which happens when a piece could not be read; those rows are listed in the run's own output file and are not counted above.", ""]
    lines += ["How to read a disagreement: the piece-by-piece reading can only ever show the judge fewer sentences than the whole reading, so it can miss a proof and turn a pass into 'the source is silent on this part'. A disagreement in the other direction — the piece-by-piece reading finding proof the whole reading missed — would mean the whole reading overlooked something, which is also worth knowing.", ""]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--whole", required=True)
    ap.add_argument("--sectioned", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rows = compare(load(a.whole), load(a.sectioned))
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    write_report(rows, a.out)
    with open(os.path.splitext(a.out)[0] + ".json", "w") as f:
        json.dump({"rows": rows}, f, indent=1, ensure_ascii=False)
    both = [r for r in rows if r["whole_answered"] and r["sectioned_answered"]]
    print(f"{sum(1 for r in both if r['agree'])}/{len(both)} rows agree "
          f"across the two reading modes")


if __name__ == "__main__":
    main()
