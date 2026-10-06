#!/usr/bin/env python3
"""Card #105 — did reading every piece directly answer the long rows, and does
it agree with a whole reading?

Four numbers, all from files already on disk, no model calls:

 1. Of the rows whose source does not fit the free seat's reading window, how
    many now have a verdict at all? Before this reading they had none.
 2. The one-variable control: on the rows the free model DID read whole, does
    the piece-by-piece reading give the same answer? Same model, same rulebook,
    same rows, only the reading differs. The scan-then-judge reading of round
    1.5 scored four of six here, and both of its two disagreements turned "true
    as written" into "the source is silent on part of this claim".
 3. Where the author has already ruled on a row, how often does each reading
    match that ruling? Two readings disagreeing tells you only that one of
    them is wrong; the ruling tells you which. Added 2026-09-13 (card #105
    follow-up) because a disagreement on row retreat:b137 was the piece-by-
    piece reading being right and the whole reading being wrong.
 4. On the long rows, does the piece-by-piece answer match the other panel
    seat's whole reading of the same source, where that seat could read it?

Usage:
  python3 benchmarks/labeler/compare_piece_read.py \
      --piece-read benchmarks/labeler/rounds/round3/verdicts_gemma_piece_read.jsonl \
      --control benchmarks/labeler/rounds/round3/control_gemma_piece_read.jsonl \
      --whole-same-model benchmarks/labeler/rounds/round1/verdicts_gemma.jsonl \
      --whole-other-seat benchmarks/labeler/rounds/round1/verdicts_sonnet.jsonl \
      --rulings benchmarks/labeler/strict_labels.jsonl \
      --out benchmarks/labeler/rounds/round3/piece_read_report.md
"""
import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from benchmarks.labeler.compare_read_modes import LABEL_WORDS, load  # noqa: E402


def _word(label):
    return LABEL_WORDS.get(label, label or "no answer")


def compare_pairs(new_recs, reference_recs, same_model):
    """One comparison row per record in `new_recs` that has a counterpart.

    `same_model` True compares a model against its own whole reading (the
    control); False compares it against the other seat's whole reading, so the
    counterpart is looked up by row alone, not by (row, model)."""
    by_row = {}
    for (row_id, model), rec in reference_recs.items():
        by_row.setdefault(row_id, []).append((model, rec))
    out = []
    for (row_id, model), new in sorted(new_recs.items()):
        if same_model:
            ref = reference_recs.get((row_id, model))
            ref_model = model
        else:
            others = [(m, r) for m, r in by_row.get(row_id, []) if m != model]
            ref_model, ref = others[0] if others else (None, None)
        if ref is None:
            continue
        out.append({
            "row_id": row_id, "model": model, "reference_model": ref_model,
            "new_answered": bool(new.get("answered")),
            "reference_answered": bool(ref.get("answered")),
            "new_label": new.get("strict_label"),
            "reference_label": ref.get("strict_label"),
            "pieces": new.get("pieces"),
            "quotes_dropped": new.get("quotes_dropped"),
            "agree": (bool(new.get("answered")) and bool(ref.get("answered"))
                      and new.get("strict_label") == ref.get("strict_label")),
        })
    return out


def load_rulings(path):
    """row_id -> the label the author ruled for that row, from strict_labels.jsonl.

    Only rows the author actually ruled on are returned; a row a panel merely
    agreed on is not a ruling and must not be scored against."""
    out = {}
    if not path or not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            if (rec.get("ruled_by") or "").strip().lower() != "the author":
                continue
            if rec.get("strict_label"):
                out[rec["row_id"]] = {
                    "label": rec["strict_label"],
                    "date": rec.get("date", ""),
                    "reason": rec.get("reason", ""),
                }
    return out


def score_against_rulings(comparison_rows, rulings):
    """For each control row the author ruled on, say which reading matched."""
    scored = []
    for r in comparison_rows:
        ruling = rulings.get(r["row_id"])
        if ruling is None:
            continue
        scored.append({
            "row_id": r["row_id"],
            "ruling": ruling["label"],
            "ruling_date": ruling["date"],
            "piece_label": r["new_label"] if r["new_answered"] else None,
            "whole_label": r["reference_label"] if r["reference_answered"] else None,
            "piece_matches": bool(r["new_answered"])
            and r["new_label"] == ruling["label"],
            "whole_matches": bool(r["reference_answered"])
            and r["reference_label"] == ruling["label"],
        })
    return scored


def _ruling_lines(scored):
    if not scored:
        return [
            "## Scored against the author's own rulings",
            "",
            "No row compared above has a ruling from the author yet, so there is nothing to score.",
            "",
        ]
    piece_right = sum(1 for s in scored if s["piece_matches"])
    whole_right = sum(1 for s in scored if s["whole_matches"])
    lines = [
        "## Scored against the author's own rulings",
        "",
        "The comparison above says only whether two readings gave the same answer. It cannot say which one is right. For these rows the author has written down the correct answer, so each reading can be marked right or wrong instead of merely same or different.",
        "",
        f"**Read piece by piece: {piece_right} of {len(scored)} rows match the author's ruling. Read whole: {whole_right} of {len(scored)}.**",
        "",
        "| row | the author ruled | read piece by piece | read whole |",
        "|---|---|---|---|",
    ]
    for s in scored:
        lines.append(
            f"| {s['row_id']} | {_word(s['ruling'])} ({s['ruling_date']}) "
            f"| {_word(s['piece_label'])} — {'right' if s['piece_matches'] else 'WRONG'} "
            f"| {_word(s['whole_label'])} — {'right' if s['whole_matches'] else 'WRONG'} |")
    lines.append("")
    only_piece = [s["row_id"] for s in scored
                  if s["piece_matches"] and not s["whole_matches"]]
    only_whole = [s["row_id"] for s in scored
                  if s["whole_matches"] and not s["piece_matches"]]
    if only_piece:
        lines += [
            "Rows the piece-by-piece reading got right and the whole reading got wrong: "
            + ", ".join(only_piece)
            + ". A disagreement on one of these rows is not a loss from cutting the source; it is the piece-by-piece reading being more careful.",
            "",
        ]
    if only_whole:
        lines += [
            "Rows the whole reading got right and the piece-by-piece reading got wrong: "
            + ", ".join(only_whole)
            + ". These are the rows where reading in pieces, or splitting the claim into parts, cost a correct answer, and they are the ones to look at first.",
            "",
        ]
    return lines


def _table(rows, new_name, ref_name):
    lines = [f"| row | {new_name} | {ref_name} | same answer? |",
             "|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| {r['row_id']} | {_word(r['new_label'])}"
            f"{' (' + str(r['pieces']) + ' pieces)' if r.get('pieces') else ''}"
            f" | {_word(r['reference_label'])} | "
            f"{'yes' if r['agree'] else 'NO'} |")
    return lines


def write_report(piece_read, control_rows, other_seat_rows, path, rulings=None):
    answered = [r for r in piece_read.values() if r.get("answered")]
    refused = [r for r in piece_read.values() if not r.get("answered")]
    both_control = [r for r in control_rows
                    if r["new_answered"] and r["reference_answered"]]
    agree_control = [r for r in both_control if r["agree"]]
    both_other = [r for r in other_seat_rows
                  if r["new_answered"] and r["reference_answered"]]
    agree_other = [r for r in both_other if r["agree"]]
    total_pieces = sum(r.get("pieces") or 0 for r in piece_read.values())
    total_calls = total_pieces + len(piece_read)

    lines = [
        "# Reading a source that does not fit one model call, measured",
        "",
        "**What was read and how.** A source too long for one model call was cut into numbered pieces of 40,000 characters, with 2,000 characters shared between neighbouring pieces so no sentence is lost at a cut. The claim was split into its checkable parts in a single call. Then every piece was asked, about those same parts, whether it proves them, contradicts them, or says nothing about them, and every quoted proof was checked word for word against the piece it came from. A part counts as proven when any piece proves it, and the answer \"the source is silent on this part\" is only given when every piece was read and none of them found anything.",
        "",
        f"**How many long rows now have an answer at all.** {len(answered)} of {len(piece_read)} rows read this way produced a verdict; {len(refused)} did not. Before this reading, a source over the free seat's reading window produced no verdict from that seat at all, so every one of these answers is new. The reading cost {total_calls} calls in total ({total_pieces} piece calls plus one claim-splitting call per row), all of them on the no-billing Google keys, so the money cost was nothing and the cost was time.",
        "",
    ]
    if refused:
        lines += ["Rows with no verdict, and why:", ""]
        for r in refused:
            lines.append(f"- **{r['row_id']}**: {r.get('reason', 'no reason recorded')}")
        lines.append("")

    lines += [
        "## The control: the same model, the same rows, read both ways",
        "",
        "These are rows the free model could read whole. It read them again piece by piece, so the only thing that differs between the two answers is the reading method. A disagreement here is a place where the reading method changed the answer; the next section says which of the two answers was the correct one.",
        "",
    ]
    if not both_control:
        lines += ["No row was read both ways, so there is nothing to compare yet.", ""]
    else:
        lines += [
            f"**{len(agree_control)} of {len(both_control)} rows got the same answer both ways.** For comparison, the scan-then-judge reading measured on 2026-09-04 agreed on four of six, and both of its disagreements turned an answer of \"true as written\" into \"the source is silent on part of this claim\".",
            "",
        ] + _table(control_rows, "read piece by piece", "read whole") + [""]
        wrong_way = [r for r in both_control if not r["agree"]
                     and r["reference_label"] == "pass"
                     and r["new_label"] == "fail_unproven"]
        if wrong_way:
            lines += [
                f"{len(wrong_way)} of the disagreements are the failure this reading was built to remove: the whole reading found the claim true as written and the piece-by-piece reading said the source is silent. Each one means a proof sentence was still lost on the way, and the rows are named here so a human can look at them: "
                + ", ".join(r["row_id"] for r in wrong_way) + ".", ""]
        else:
            lines += ["No disagreement turned a pass into a silence, which is the failure this reading was built to remove.", ""]

    scored = score_against_rulings(control_rows, rulings or {})
    lines += _ruling_lines(scored)

    lines += [
        "## The long rows against the other seat's whole reading",
        "",
        "The second panel seat has a much larger reading window, so on most of the long rows it read the whole source in one go. Where it did, its answer is compared with the piece-by-piece answer here. This is not a control — two different models can honestly disagree — but a match is evidence that reading in pieces did not change what the source says.",
        "",
    ]
    if not both_other:
        lines += ["No long row has an answer from both seats, so there is nothing to compare yet.", ""]
    else:
        lines += [
            f"**{len(agree_other)} of {len(both_other)} long rows got the same answer from both seats.**",
            "",
        ] + _table(other_seat_rows, "free seat, piece by piece",
                   "other seat, read whole") + [""]

    lines += [
        "## What this does not show",
        "",
        "A reading in pieces can still be wrong in one direction that no comparison here catches: if the model reading one piece overlooks the sentence that proves a part, and no other piece carries it, the merged answer says the source is silent. An answer of silence from any reading is therefore still weaker evidence than a finding. Note also that two readings disagreeing does not tell you which one is wrong; only the section scored against the author's rulings does that, and it covers the ruled rows alone. Nothing on this page changes an answer key: every answer is a proposal for the author.",
        "",
    ]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return {"answered": len(answered), "rows": len(piece_read),
            "control_agree": len(agree_control), "control_both": len(both_control),
            "control_ruled": len(scored),
            "control_piece_right": sum(1 for s in scored if s["piece_matches"]),
            "control_whole_right": sum(1 for s in scored if s["whole_matches"]),
            "other_agree": len(agree_other), "other_both": len(both_other),
            "calls": total_calls}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--piece-read", required=True,
                    help="verdicts from the piece-by-piece reading of the long rows")
    ap.add_argument("--control", default="",
                    help="verdicts from the piece-by-piece reading of rows the "
                         "same model also read whole")
    ap.add_argument("--whole-same-model", default="",
                    help="that same model's whole readings (round 1)")
    ap.add_argument("--whole-other-seat", default="",
                    help="the other seat's whole readings (round 1)")
    ap.add_argument("--rulings", default="",
                    help="strict_labels.jsonl — the rows the author has ruled on, "
                         "used to mark each reading right or wrong rather than "
                         "merely same or different")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    piece_read = load(a.piece_read)
    control_rows = []
    if a.control and a.whole_same_model and os.path.exists(a.control):
        control_rows = compare_pairs(load(a.control), load(a.whole_same_model),
                                     same_model=True)
    other_rows = []
    if a.whole_other_seat and os.path.exists(a.whole_other_seat):
        other_rows = compare_pairs(piece_read, load(a.whole_other_seat),
                                   same_model=False)
    summary = write_report(piece_read, control_rows, other_rows, a.out,
                           rulings=load_rulings(a.rulings))
    print(json.dumps(summary, indent=2))
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
