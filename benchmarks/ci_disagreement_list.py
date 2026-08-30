#!/usr/bin/env python3
"""Build the blind-reader disagreement list: every row where the answer key, the
tool and the blind readers do not all agree.

Pure — no API calls, no network. Every definition of "pass" is imported from the
existing harnesses so no column can drift: `strict_side`/`grounding_side` for the
published label, `_tool_bucket` + `_collapse` for a tool run, and
`ci_blind_compare.sides` for a reader's vote.

    python3 benchmarks/ci_disagreement_list.py \
        --spec data/citation_integrity/blind_readers_2026-08-03_spec.json \
        --out docs/BLIND_READERS_2026-08-03.md

The spec is a small JSON file kept beside the data it joins (so under the
gitignored `data/`, like every other benchmark artefact) so the exact join can be
reproduced later:

    {"sets": [{"batch": "<batch dir>",
               "run": "<run dir>", "run_name": "<what to call it>",
               "readers": {"sonnet": {"blind_json": "<aggregate .json>"},
                           "opus":   {"votes_dir": "<raw votes dir>"}}}]}

Rows are split into three piles, which is the whole point of the exercise:

  A  both readers disagree with the answer key, and with each other they agree
     -> the key is the most likely thing that is wrong; a quick confirm
  B  the readers disagree with each other
     -> genuinely hard rows; only the author can settle them
  C  both readers agree with the key and the tool does not
     -> nothing to rule on: these are the tool's own errors
"""
import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from citation_integrity_bench import (  # noqa: E402
    strict_side, grounding_side, _row_co_citation,
)
from ci_blind_compare import sides as blind_sides  # noqa: E402
from ci_four_column import load_run  # noqa: E402
from ci_batch_ids import batch_tag, qualify  # noqa: E402

MISSING = "no verdict"


def load_reader(spec, batch_dir):
    """A reader spec -> {row id: vote dict}."""
    if spec.get("blind_json"):
        return json.load(open(spec["blind_json"]))["votes"]
    votes = {}
    for path in sorted(glob.glob(os.path.join(spec["votes_dir"], "*.json"))):
        if os.path.basename(path).startswith("_"):
            continue
        v = json.load(open(path))
        votes[v["id"]] = v
    return votes


def collect(spec):
    """The spec -> one flat list of row records, ids qualified with the batch tag."""
    rows, reader_names, sets = [], [], []
    for s in spec["sets"]:
        tag = batch_tag(s["batch"])
        gt = json.load(open(os.path.join(s["batch"], "ci_ground_truth.json")))["claims"]
        tool = load_run(s["run"]) if s.get("run") else {}
        readers = {name: load_reader(rs, s["batch"]) for name, rs in s["readers"].items()}
        for name in readers:
            if name not in reader_names:
                reader_names.append(name)
        sets.append({"tag": tag, "n": len(gt), "run_name": s.get("run_name", s.get("run", "")),
                     "readers": {n: len(v) for n, v in readers.items()}})
        for cid in sorted(gt):
            g = gt[cid]
            co, _ = _row_co_citation(g)
            rec = {
                "qid": qualify(tag, cid), "tag": tag, "cid": cid,
                "label": g["label"],
                "key": strict_side(g["label"]),
                "key_grounding": grounding_side(g["label"]),
                "tool": tool.get(cid, MISSING),
                "claim": g.get("claim_text", ""),
                "fair": (co or {}).get("class") == "single",
                "readers": {},
            }
            for name, votes in readers.items():
                v = votes.get(cid)
                if not v:
                    rec["readers"][name] = {"side": MISSING}
                    continue
                st, gr = blind_sides(v)
                rec["readers"][name] = {
                    "side": st, "grounding_side": gr, "defect": v.get("defect"),
                    "confidence": v.get("confidence", "unstated"),
                    "quote": (v.get("quote") or "").strip(),
                    "reason": (v.get("reason") or "").strip(),
                }
            rows.append(rec)
    return rows, reader_names, sets


def pile_of(rec, reader_names):
    """Which pile a row belongs in, or None when everything agrees."""
    sides = [rec["readers"][n]["side"] for n in reader_names if n in rec["readers"]]
    if any(s == MISSING for s in sides) or len(sides) < 2:
        return "D"                                    # incomplete reading
    if len(set(sides)) > 1:
        return "B"                                    # readers split
    reader = sides[0]
    if reader != rec["key"]:
        return "A"                                    # both readers vs the key
    if rec["tool"] != rec["key"]:
        return "C"                                    # tool alone
    return None


_MARK = "\x00"
# The citation under test sits inside a bracket group that can also hold plain
# reference numbers ("[[[cidev0003]],10]"), so the group is rewritten whole
# rather than the marker alone — otherwise stray brackets reach the reader.
_SIBLINGS = re.compile(r"\[*" + _MARK + r"\]*\s*[,;]\s*\d[\d,;\s]*\]?")
# the converter's span sometimes stops inside the bracket group, leaving the
# sibling numbers off the end entirely ("SAMHD1 [[[cidev0003]],")
_SIBLINGS_CUT = re.compile(r"\[*" + _MARK + r"\]*\s*[,;]\s*$")
_GROUP = re.compile(r"\[*" + _MARK + r"\]*")
_WITH_OTHERS = "[the cited paper, plus other references]"


def wrap_claim(rec, n=300):
    """The citing sentence as a human should read it: markers spelled out."""
    text = " ".join(str(rec["claim"]).split())
    text = text.replace(f"[[{rec['cid']}]]", _MARK)
    text = _SIBLINGS.sub(_WITH_OTHERS, text)
    text = _SIBLINGS_CUT.sub(_WITH_OTHERS, text)
    text = _GROUP.sub("[the cited paper]", text)
    text = text.replace("<|multi_cit|>", "the cited paper")
    text = text.replace("<|cit|>", "the cited paper").replace("<|other_cit|>", "another paper")
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


FAIR_YES = "yes — the paper cited this statement to exactly one article"
FAIR_NO = ("no — the paper cited this statement to several articles and our converter kept "
           "only one, so a *flag* here may be the setup's fault")


def row_block(rec, reader_names):
    """One row, in full, for the piles the author has to rule on."""
    out = [f"#### {rec['qid']} — key says **{rec['label']}**",
           "",
           f"> {wrap_claim(rec)}",
           "",
           f"- **Answer key:** {rec['label']} → {rec['key']}",
           f"- **Tool:** {rec['tool']}"]
    for name in reader_names:
        r = rec["readers"].get(name)
        if not r or r["side"] == MISSING:
            out.append(f"- **{name.title()}:** no reading")
            continue
        bits = [r["side"]]
        if r.get("defect"):
            bits.append(f"defect: {r['defect']}")
        bits.append(f"confidence: {r['confidence']}")
        out.append(f"- **{name.title()}:** " + ", ".join(bits))
        if r.get("reason"):
            out.append(f"    - why: {r['reason']}")
        if r.get("quote"):
            out.append(f"    - rests on: “{r['quote']}”")
    out.append(f"- **Fair question:** {FAIR_YES if rec['fair'] else FAIR_NO}")
    out.append("")
    return "\n".join(out)


def table(recs, reader_names):
    head = "| row | key label | key | tool | " + " | ".join(n for n in reader_names) + " | fair question |"
    sep = "|---|---|---|---|" + "---|" * (len(reader_names) + 1)
    lines = [head, sep]
    for rec in recs:
        cells = [rec["readers"].get(n, {}).get("side", MISSING) for n in reader_names]
        lines.append(f"| {rec['qid']} | {rec['label']} | {rec['key']} | {rec['tool']} | "
                     + " | ".join(cells) + f" | {'yes' if rec['fair'] else 'no'} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True, help="markdown file to write")
    ap.add_argument("--json", help="also write the machine-readable piles here")
    a = ap.parse_args()

    spec = json.load(open(a.spec))
    rows, reader_names, sets = collect(spec)
    piles = {"A": [], "B": [], "C": [], "D": []}
    for rec in rows:
        p = pile_of(rec, reader_names)
        if p:
            piles[p].append(rec)

    # agreement of each column with the answer key, per set — reported per column
    # and never averaged across columns (standing rule).
    agree = {}
    for s in sets:
        tag = s["tag"]
        mine = [r for r in rows if r["tag"] == tag]
        cols = {"tool": [(r["tool"], r["key"]) for r in mine]}
        for n in reader_names:
            cols[n] = [(r["readers"].get(n, {}).get("side", MISSING), r["key"]) for r in mine]
        agree[tag] = {}
        for col, pairs in cols.items():
            scored = [(g, w) for g, w in pairs if g != MISSING]
            agree[tag][col] = {
                "n": len(scored), "of": len(mine),
                "agree": sum(1 for g, w in scored if g == w),
                "passed_a_flagged_row": sum(1 for g, w in scored if w == "flag" and g == "pass"),
                "flagged_an_accurate_row": sum(1 for g, w in scored if w == "pass" and g == "flag"),
            }

    if a.json:
        json.dump({"spec": a.spec, "sets": sets, "readers": reader_names,
                   "agreement_with_key": agree,
                   "piles": {k: [r["qid"] for r in v] for k, v in piles.items()},
                   "rows": {r["qid"]: r for r in rows}},
                  open(a.json, "w"), indent=1)

    render(a.out, spec, rows, reader_names, sets, piles, agree)
    print(f"wrote {a.out}"
          + (f" and {a.json}" if a.json else "")
          + f"\n  pile A (both readers vs the key): {len(piles['A'])}"
          + f"\n  pile B (readers split):           {len(piles['B'])}"
          + f"\n  pile C (tool alone):              {len(piles['C'])}"
          + f"\n  pile D (incomplete reading):      {len(piles['D'])}")
    return 0


def render(out_path, spec, rows, reader_names, sets, piles, agree):
    names = ", ".join(n.title() for n in reader_names)
    L = []
    A = L.append
    A(f"# {spec.get('title', 'Blind-reader disagreement list')}")
    A("")
    A(spec.get("intro", "").strip())
    A("")
    A("## Words used in this document")
    A("")
    A("- **Answer key** — the label the benchmark's creators gave a row: is this")
    A("  citation accurate, or what is wrong with it.")
    A("- **Blind reader** — a strong model that was given the citing sentence and the")
    A("  whole cited paper, and asked for its own verdict. It never saw the answer")
    A("  key or the tool's verdict, which is what makes the reading blind. Two")
    A("  readers here: " + names + ".")
    A("- **pass / flag** — the two answers everyone in this document gives. *pass* =")
    A("  the cited paper supports the sentence as written; *flag* = anything else.")
    A("- **The tool** — our own pipeline's verdict for that row, from the run named")
    A("  below. Its three verdicts are folded to the same two answers.")
    A("- **Fair question** — the paper cited that statement to exactly one article.")
    A("  Where it cited several, our converter kept only one of them, so asking a")
    A("  reader to prove the whole sentence from that one article can be unfair, and")
    A("  a *flag* there may be the setup's fault rather than the paper's.")
    A("- **Row id** — written `pilot100:cidev0007` / `fresh50:cidev0007`, because")
    A("  every batch numbers its rows from 1 and the same number means a different")
    A("  row in the other batch.")
    A("")
    A("## What was read")
    A("")
    A("| rows | tool run | readers |")
    A("|---|---|---|")
    for s in sets:
        rd = ", ".join(f"{n} ({k} rows)" for n, k in s["readers"].items())
        A(f"| {s['tag']} ({s['n']}) | {s['run_name']} | {rd} |")
    A("")
    A("## How often each column matched the answer key")
    A("")
    A("Read down the columns, not across: these are three separate measurements of")
    A("the same rows, and averaging them would hide which kind of mistake each one")
    A("makes. *Passed a flagged row* means the column saw no problem where the key")
    A("says there is one; *flagged an accurate row* is the opposite.")
    A("")
    for s in sets:
        tag = s["tag"]
        A(f"**{tag}**")
        A("")
        A("| column | matched the key | passed a flagged row | flagged an accurate row |")
        A("|---|---|---|---|")
        for col, d in agree[tag].items():
            A(f"| {col} | {d['agree']}/{d['n']} | {d['passed_a_flagged_row']} | "
              f"{d['flagged_an_accurate_row']} |")
        A("")
    # Hand-written reading of the numbers above, kept in its own file so
    # regenerating this document never overwrites it.
    if spec.get("findings_file"):
        A(open(spec["findings_file"]).read().strip())
        A("")
    A("## The three piles")
    A("")
    A(f"- **Pile A — both readers disagree with the answer key: {len(piles['A'])} rows.**")
    A("  Both readers read the paper and both landed on the opposite side of the key.")
    A("  This is where a wrong label is most likely. Your job here is a quick confirm.")
    A("  (The two readers are a smaller and a larger model from the same family, so")
    A("  they are two readings, not two companies — see the limits section.)")
    A(f"- **Pile B — the readers disagree with each other: {len(piles['B'])} rows.**")
    A("  Genuinely hard rows. Nothing can settle these except your own reading.")
    A(f"- **Pile C — both readers agree with the key, the tool does not: {len(piles['C'])} rows.**")
    A("  Nothing to rule on: the key and two independent readings all say the same")
    A("  thing, so these are our tool's own mistakes. Listed as a table only; they")
    A("  are the input to the review pages (follow-up item 2).")
    if piles["D"]:
        A(f"- **Pile D — a reader is missing: {len(piles['D'])} rows.** Should be empty.")
    A("")
    A("---")
    A("")
    A(f"## Pile A — both readers against the answer key ({len(piles['A'])} rows)")
    A("")
    if not piles["A"]:
        A("(none)")
        A("")
    else:
        for direction, title, gloss in [
            ("pass", "The key says something is wrong; both readers found the sentence supported",
             "If the readers are right, the label is too harsh and the row currently "
             "punishes any tool that gets it right."),
            ("flag", "The key says the citation is accurate; both readers found a problem",
             "If the readers are right, the label is too lenient and the row currently "
             "rewards a tool for missing a real error."),
        ]:
            group = [r for r in piles["A"] if r["readers"][reader_names[0]]["side"] == direction]
            if not group:
                continue
            A(f"### {title} ({len(group)} rows)")
            A("")
            A(gloss)
            A("")
            for rec in group:
                A(row_block(rec, reader_names))
    A(f"## Pile B — the readers disagree with each other ({len(piles['B'])} rows)")
    A("")
    if not piles["B"]:
        A("(none)")
        A("")
    for rec in piles["B"]:
        A(row_block(rec, reader_names))
    A(f"## Pile C — the tool alone disagrees ({len(piles['C'])} rows)")
    A("")
    A("No ruling needed. The key and both readers agree; the tool does not.")
    A("")
    if piles["C"]:
        A(table(piles["C"], reader_names))
    else:
        A("(none)")
    A("")
    if piles["D"]:
        A(f"## Pile D — incomplete reading ({len(piles['D'])} rows)")
        A("")
        A(table(piles["D"], reader_names))
        A("")
    if spec.get("outro"):
        A(spec["outro"].strip())
        A("")
    A("## Every row, one table")
    A("")
    A("All rows, including the ones where everything already agrees.")
    A("")
    A(table(rows, reader_names))
    A("")
    open(out_path, "w").write("\n".join(L))


if __name__ == "__main__":
    main()
