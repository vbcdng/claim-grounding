#!/usr/bin/env python3
"""Task #15 adjudication funnel — sort panel verdicts into agree / disagree / tail.

Reads the rows file and the panel's verdicts.jsonl and writes:
  <out-dir>/funnel.json     — machine-readable sorting
  <out-dir>/round_summary.md — plain-language summary for the author

Sorting rule (per the approved plan, tightened 2026-09-04 by the review
session's two rulings):
  - self_contradicting : a vote's own written note, or its own list of parts,
                 points at a different label than the label it gave. Such a
                 vote is not counted at all, and the row goes to the author
                 with that vote quoted.
  - unanimous  : every counted model gives the same strict label AND at
                 least 2 models answered -> proposed label (still only a
                 proposal; the author rules before any answer key changes)
  - needs_whole_reading : the counted votes agree, but the agreement rests on
                 a "the source never says this" answer reached by reading the
                 source in numbered pieces. The 2026-09-04 control run showed
                 that reading turns a pass into a silence on real rows, so no
                 label is proposed until a whole reading confirms it.
  - split      : counted models disagree -> goes to the tie-break round
  - insufficient: fewer than 2 models answered -> re-run or goes to the author

Refusals are counted and reported, never treated as votes (task #37 rule).
No LLM calls. Usage:
  python3 benchmarks/labeler/funnel.py --rows .../rows.jsonl \
      --verdicts .../verdicts.jsonl --out-dir .../round1
"""
import argparse
import json
import os
import re
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


# --- a vote that contradicts itself -----------------------------------------
# The project's standing rule (memory "Sonnet is the default grader") is to
# check a model's label field against its own reasoning, because the label has
# been wrong while the reasoning was right. score_conversions.py does the same
# for the conversion key in label_disagrees_with_reasoning(); this is the same
# idea over the labeler's four strict labels.
#
# The cues below are deliberately narrow: only a sentence in which the model
# STATES a label counts. Ordinary hedging ("I treated this as tolerated rather
# than unproven") must not fire, and was checked against every note the panel
# has written so far (round 1 + round 2, 35 answers): only retreat:b128's Gemma
# note fires, and its own last sentence is "Therefore, the label must be
# fail_unproven" under a label of pass.
_CONCLUDES_FAILURE = (
    r"must\s+(?:be\s+)?mark(?:ed)?\s+(?:it|this|the\s+claim)?\s*(?:as\s+)?"
    r"(?:unproven|not\s+proven|contradicted|fail\w*)",
    r"(?:label|verdict|answer)\s+(?:must|should|has\s+to)\s+be\s+"
    r"['\"‘’]?\s*(?:fail\w*|unproven|not\s+proven)",
    r"(?:it|this)\s+is\s+['\"‘’]?\s*fail_(?:unproven|contradicted)",
    r"(?:so|therefore)\s*,?\s*(?:the\s+)?(?:label|verdict)\s+is\s+"
    r"['\"‘’]?\s*(?:fail\w*|unproven)",
)
_CONCLUDES_PASS = (
    r"must\s+(?:be\s+)?mark(?:ed)?\s+(?:it|this|the\s+claim)?\s*(?:as\s+)?pass",
    r"(?:label|verdict|answer)\s+(?:must|should|has\s+to)\s+be\s+"
    r"['\"‘’]?\s*pass",
    r"(?:so|therefore)\s*,?\s*(?:the\s+)?(?:label|verdict)\s+is\s+"
    r"['\"‘’]?\s*pass",
)


def _matches_any(patterns, text):
    return any(re.search(p, text) for p in patterns)


def label_from_parts(parts):
    """The label the rubric's own table forces, given the part classifications.

    STRICT_RUBRIC_V1 step 3 derives the label mechanically: any contradicted
    part means fail_contradicted, otherwise any unproven part means
    fail_unproven, otherwise pass. Returns None when there are no parts to
    reason from (round-1 style records with an empty list).
    """
    classes = {p.get("classification") for p in (parts or [])}
    if not classes - {None}:
        return None
    if "contradicted" in classes:
        return "fail_contradicted"
    if "unproven" in classes:
        return "fail_unproven"
    if classes <= {"proven", "tolerated"}:
        return "pass"
    return None


def vote_contradicts_itself(verdict):
    """Return a plain-language reason when a vote disagrees with its own answer.

    Two ways that happens, both checked against the vote's own record and
    neither needing a model call:
      1. the written note states a different label than the label field;
      2. the part classifications force a different label than the label field
         (the rubric derives the label from the parts mechanically).
    Returns None when the vote is consistent with itself.
    """
    if not verdict.get("answered"):
        return None
    label = verdict.get("strict_label")
    if label not in LABEL_WORDS:
        return None
    note = (verdict.get("hard_note") or verdict.get("reasoning") or "").lower()
    if note:
        if label == "pass" and _matches_any(_CONCLUDES_FAILURE, note):
            return ("its own written note ends by saying the claim must be "
                    "marked as a failure, but the label it gave is pass")
        if label.startswith("fail") and _matches_any(_CONCLUDES_PASS, note):
            return ("its own written note says the claim must be marked pass, "
                    "but the label it gave is a failure")
    forced = label_from_parts(verdict.get("parts"))
    if forced and forced != label:
        return (f"its own list of parts forces the label "
                f"'{LABEL_WORDS[forced]}' under the rubric, but the label it "
                f"gave is '{LABEL_WORDS[label]}'")
    return None


# Readings that can put fewer sentences in front of the judge than a whole
# reading would, so an answer of "the source never says this" from them may
# only mean the proof was lost on the way. "sectioned" is the scan-then-judge
# mode of round 1.5, whose summarising pass is where the loss happened.
LOSSY_READ_MODES = {"sectioned"}


def silence_is_trustworthy(verdict):
    """Can this vote's "the source is silent" answer stand on its own?

    A vote that records the answer itself is believed (the piece-read
    mode sets silence_trustworthy=True only when every piece of the source was
    read and answered). Otherwise the mode decides, and a vote with no mode
    recorded at all is a round-1 whole-source read."""
    if "silence_trustworthy" in verdict:
        return bool(verdict["silence_trustworthy"])
    return verdict.get("read_mode", "whole_source") not in LOSSY_READ_MODES


def sort_rows(rows, verdicts):
    by_row = defaultdict(list)
    for v in verdicts:
        by_row[v["row_id"]].append(v)
    result = []
    for row in rows:
        vs = by_row.get(row["row_id"], [])
        answered = [v for v in vs if v.get("answered")]
        refused = [v for v in vs if not v.get("answered")]
        # A vote whose own note or own parts point at a different label is set
        # aside: it is counted towards neither unanimity nor a split, and the
        # row is handed to the author with that vote quoted.
        self_contradicting = []
        counted = []
        for v in answered:
            reason = vote_contradicts_itself(v)
            if reason:
                self_contradicting.append({
                    "model": v["model"], "label": v["strict_label"],
                    "reason": reason, "note": v.get("hard_note") or "",
                    "read_mode": v.get("read_mode", "whole_source"),
                })
            else:
                counted.append(v)
        # "Silent" answers from a reading that could have lost the proof on the
        # way: the 2026-09-04 control run turned two of three passes into
        # silences this way, so such a vote may not carry a proposed label on
        # its own. Which readings those are is decided per vote, not per mode:
        # a vote may record silence_trustworthy itself (the piece-read
        # mode does, because it reads every piece and has no summarising step
        # that can drop a sentence), and otherwise the scan-then-judge mode
        # named "sectioned" is the one known-lossy reading.
        sectioned_unproven = sorted(
            v["model"] for v in counted
            if not silence_is_trustworthy(v)
            and v["strict_label"] in ("fail_unproven", "invalid"))
        labels = {v["strict_label"] for v in counted}
        proposed = None
        if self_contradicting:
            status = "self_contradicting"
        elif len(counted) < 2:
            status = "insufficient"
        elif len(labels) > 1:
            status = "split"
        elif sectioned_unproven:
            status = "needs_whole_reading"
        else:
            status = "unanimous"
            proposed = sorted(labels)[0]
        unverified = [
            {"model": v["model"], "part": p["part"], "quote": p["quote"]}
            for v in answered for p in v.get("parts", [])
            if p.get("quote") and not p.get("quote_verified")
        ]
        # Round-1 verdicts predate the read_mode field; they were all whole-source.
        read_modes = {v["model"]: v.get("read_mode", "whole_source")
                      for v in counted}
        result.append({
            "row_id": row["row_id"], "pile": row["pile"],
            "claim_text": row.get("claim_text", ""),
            "old_label": row["old_label"], "status": status,
            "proposed_label": proposed,
            "votes": {v["model"]: v["strict_label"] for v in counted},
            "read_modes": read_modes,
            "sectioned_unproven": sectioned_unproven,
            "self_contradicting_votes": self_contradicting,
            "refusals": [v["model"] for v in refused],
            "unverified_quotes": unverified,
            "hard_notes": {v["model"]: v.get("hard_note") for v in counted
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
        f"How the rows sorted: **{len(buckets['unanimous'])} unanimous** (every counted model gave the same label — these become proposed labels for the author to confirm), **{len(buckets['split'])} split** (the models disagree — these go to the tie-break round), **{len(buckets['needs_whole_reading'])} agreed but resting on a piece-by-piece reading** (the models agree that the source never says part of the claim, but at least one of them reached that answer without ever reading the source in one sitting, so no label is proposed yet), **{len(buckets['self_contradicting'])} with a vote that contradicts itself** (a model's own written note or its own list of parts points at a different label than the label it gave, so that vote is not counted at all), and **{len(buckets['insufficient'])} with too few answers** to sort (fewer than two counted models answered).",
        "",
    ]
    n_sectioned = sum(1 for r in sorted_rows
                      if any(m == "sectioned" for m in r.get("read_modes", {}).values()))
    if n_sectioned:
        lines += [f"Reading mode: on {n_sectioned} of these rows at least one model did not read the source in one sitting. Its source was cut into numbered pieces, each piece was read on its own for sentences bearing on the claim in either direction, and the model then judged the claim on the sentences that reading found. That can miss a proof sentence a single whole reading would have caught, so a verdict of 'the source is silent on this part' from a piece-by-piece reading is weaker evidence than the same verdict from a whole reading. Every such vote is marked below.", ""]
    n_piece_read = sum(1 for r in sorted_rows
                       if any(m == "piece_read"
                              for m in r.get("read_modes", {}).values()))
    if n_piece_read:
        lines += [f"Reading mode: on {n_piece_read} of these rows at least one model read the source piece by piece in the stronger way built for sources too long for one call. The claim was split into its checkable parts once, and then every numbered piece of the source was asked directly, about those same parts, whether it proves them, contradicts them or says nothing about them. There is no summarising step in between that could drop a sentence, and every piece was read, so an answer of 'the source is silent on this part' from this reading rests on the whole source and is not held back the way a scan-then-judge answer is.", ""]
    if n_unverified:
        lines += [f"Quote check: {n_unverified} copied proof sentences could not be found word-for-word in the source text. Each is listed under its row below — a verdict resting only on an unfindable quote should not be trusted until a human looks.", ""]
    else:
        lines += ["Quote check: every copied proof sentence was found word-for-word in its source text.", ""]

    n_contradicting = sum(len(r.get("self_contradicting_votes", []))
                          for r in sorted_rows)
    if n_contradicting:
        lines += [f"Votes that contradict themselves: {n_contradicting}. A model is asked for a label and for one line saying what it found hard, and it also records each part of the claim as proven, tolerated, unproven or contradicted. When that written line says the claim must be marked a failure while the label says pass, or when the recorded parts force a different label than the one given (the rulebook derives the label from the parts mechanically), the vote disagrees with itself. Such a vote is left out of every count here and the row is listed under its own heading below with the vote quoted, because a vote that cannot agree with itself is evidence of nothing.", ""]

    for status, title in (("unanimous", "Unanimous rows — proposed labels"),
                          ("needs_whole_reading",
                           "Needs a whole reading before any label is proposed"),
                          ("self_contradicting",
                           "Rows with a vote whose reasoning contradicts its label"),
                          ("split", "Split rows — going to the tie-break round"),
                          ("insufficient", "Rows with too few answers")):
        rows = buckets[status]
        if not rows:
            continue
        lines += [f"## {title}", ""]
        if status == "needs_whole_reading":
            lines += ["Every row here has the same shape: the models that answered agree that the source never says some part of the claim, but at least one of those answers came from reading the source in numbered pieces rather than in one sitting. Reading in pieces can only ever put fewer sentences in front of the judge, never more, so it can miss the very sentence that would have proved the part. The control run of 2026-09-04 measured this on six rows the free model had read both ways: four answers matched, and both mismatches turned an answer of 'true as written' into 'the source is silent on this part'. So these rows are held back until a model reads the whole source, rather than proposed as label changes.", ""]
        if status == "self_contradicting":
            lines += ["Each row here has one vote that disagrees with its own answer, so that vote was not counted. The vote is quoted in full so the author can see what the model actually concluded, and the row is settled by the author rather than by the panel.", ""]
        for r in rows:
            modes = r.get("read_modes", {})
            votes = ", ".join(
                f"{m.split('/')[-1]}: {LABEL_WORDS.get(l, l)}"
                + (" (read the source in numbered pieces)"
                   if modes.get(m) == "sectioned"
                   else " (read every numbered piece of the source directly)"
                   if modes.get(m) == "piece_read" else "")
                for m, l in r["votes"].items()) or "no answers"
            lines.append(f"**{r['row_id']}** (old label: {r['old_label']})")
            if r.get("claim_text"):
                lines.append(f"- the claim: \"{r['claim_text']}\"")
            if r["proposed_label"]:
                lines.append(f"- proposed label: **{LABEL_WORDS[r['proposed_label']]}**")
            lines.append(f"- votes counted: {votes}")
            if r["refusals"]:
                lines.append(f"- refused to answer: {', '.join(r['refusals'])}")
            for bad in r.get("self_contradicting_votes", []):
                lines.append(f"- NOT COUNTED — {bad['model'].split('/')[-1]} gave the label {LABEL_WORDS.get(bad['label'], bad['label'])}, and {bad['reason']}.")
                if bad.get("note"):
                    lines.append(f"  Its own words: \"{bad['note']}\"")
                left = len(r["votes"])
                counted_words = ("no counted votes remain" if left == 0 else
                                 "only one counted vote remains" if left == 1
                                 else f"{left} counted votes remain")
                lines.append(f"  Leaving that vote out, {counted_words} on this row, so the panel proposes nothing here and the author decides.")
            if r.get("sectioned_unproven"):
                who = ", ".join(m.split('/')[-1] for m in r["sectioned_unproven"])
                lines.append(f"- CAUTION: {who} reached that answer after reading the source in numbered pieces, and the answer is that the source never says it. A piece-by-piece reading can miss a sentence, so this particular kind of answer needs a whole reading to confirm it before any label changes.")
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
