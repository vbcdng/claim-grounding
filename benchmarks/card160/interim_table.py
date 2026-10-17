#!/usr/bin/env python3
"""Stability study (CARD160): the results table so far, from the runs that are
finished. No model calls; it only reads finished gate folders.

It does not change how anything is counted: every number comes from the
pre-registered counting program stability_table.py (fingerprinted in the plan,
section 10), called on the arms of arms_{paid,free,sonnet,haiku}.json whose
runs are all finished. An arm that needs a run that is not finished yet is
listed as "waiting for arms", with the runs it waits for.

One comparison is added that the plan does not register: Sonnet against Haiku
(r1 against r1, and the claim-by-claim reading over all three runs of each).
It is printed in its own section, marked as added after publication, and never
mixed into the registered rows.

A run counts as finished when every text it runs has analysis.json and no
checkpoint.jsonl (a text the checker is still judging keeps a checkpoint).

Usage (one command; re-run it whenever another arm ends):
  ./venv/bin/python3 benchmarks/card160/interim_table.py [--root data]
        [--out-dir benchmarks/card160/results] [--date YYYY-MM-DD]
Writes <out-dir>/interim_table_<date>.md and .json.
"""

import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import stability_table as st      # noqa: E402

JUDGE_FILES = ("paid", "free", "sonnet", "haiku")
SIX_TEXT_KINDS = ("reworded", "negated")   # the copied texts carry no hard sentences
EXTRA = [
    {"name": "sonnet first run against haiku's first run (not in the plan; added after publication)",
     "kind": "same", "role": "judges", "run": "card160sonnetr1", "baseline": "card160haikur1"},
    {"name": "sonnet against haiku, all three runs of each, claim by claim (not in the plan; added after publication)",
     "kind": "judges",
     "runs": ["card160sonnetr1", "card160sonnetr2", "card160sonnetr3"],
     "baseline_runs": ["card160haikur1", "card160haikur2", "card160haikur3"],
     "run": "card160sonnetr1", "baseline": "card160haikur1"},
]


def arm_tags(arm):
    tags = {arm["run"], arm["baseline"]}
    tags.update(arm.get("repeats") or [])
    tags.update(arm.get("runs") or [])
    tags.update(arm.get("baseline_runs") or [])
    return tags


def tag_texts(tag):
    if any(tag.endswith(k) for k in SIX_TEXT_KINDS):
        return st.GATE_TEXTS
    return st.DEFAULT_TEXTS


def text_state(root, tag, text):
    d = st.run_dir(root, tag, text)
    if not os.path.isdir(d):
        return "not started"
    if os.path.isfile(os.path.join(d, "checkpoint.jsonl")) or \
            not os.path.isfile(os.path.join(d, "analysis.json")):
        return "running"
    return "finished"


def tag_state(root, tag):
    """(finished?, {text: state})."""
    states = {t: text_state(root, tag, t) for t in tag_texts(tag)}
    return all(s == "finished" for s in states.values()), states


def load_arms():
    out = []
    for j in JUDGE_FILES:
        with open(os.path.join(HERE, f"arms_{j}.json"), encoding="utf-8") as f:
            out.append((j, json.load(f)["arms"]))
    return out


def split_ready(arms, finished):
    ready, waiting = [], []
    for a in arms:
        missing = sorted(t for t in arm_tags(a) if t not in finished)
        (waiting if missing else ready).append((a, missing))
    return [a for a, _ in ready], waiting


def run_summary(root, tags):
    lines = ["| Run | State | Texts |", "|---|---|---|"]
    finished = set()
    for tag in sorted(tags):
        done, states = tag_state(root, tag)
        if done:
            finished.add(tag)
        n_done = sum(s == "finished" for s in states.values())
        detail = ", ".join(f"{t} {s}" for t, s in states.items() if s != "finished")
        lines.append(f"| {tag} | {'finished' if done else 'not finished'} | "
                     f"{n_done} of {len(states)} finished" + (f" ({detail})" if detail else "") + " |")
    return finished, "\n".join(lines) + "\n"


WORDS = {"green_to_red": "green turned red", "red_to_green": "red turned green", "other": "other",
         "stable": "on the claims all three runs of each judge agree on",
         "majority": "majority against majority"}


def change_lines(results):
    out = []
    for r in results:
        for c in r["changes"]:
            extra = [WORDS.get(x, x) for x in (c.get("which"), c.get("repeats")) if x]
            what = c.get("direction") or c.get("what")
            out.append(f"- {r['name']}: {c['text']}/{c['id']} was {c['baseline']} in the baseline "
                       f"and {c['arm']} in the compared run ({WORDS.get(what, what)})"
                       + (f" [{'; '.join(extra)}]" if extra else "") + f". Claim: {c['claim']}")
    return out


def build(root, date):
    arm_sets = load_arms()
    all_tags = set()
    for _, arms in arm_sets:
        for a in arms:
            all_tags |= arm_tags(a)
    for a in EXTRA:
        all_tags |= arm_tags(a)
    finished, runs_md = run_summary(root, all_tags)

    md = [f"# Stability study: results table so far ({date})", "",
          "Built by `benchmarks/card160/interim_table.py`, which calls the pre-registered counting "
          "program `stability_table.py` unchanged on every registered arm whose runs are all finished. "
          "No model call was made to build it. A row is final when every run it compares is finished; "
          "the other rows are listed under \"Waiting for arms\".", "",
          "## Which runs are finished", "", runs_md]
    payload = {"generated_by": "benchmarks/card160/interim_table.py", "date": date,
               "finished_runs": sorted(finished), "judges": {}, "extra": None}
    waiting_md = []
    for judge, arms in arm_sets:
        ready, waiting = split_ready(arms, finished)
        for a, missing in waiting:
            waiting_md.append(f"- {a['name']}: waiting for {', '.join(missing)}")
        if not ready:
            continue
        results, hard = st.score_all(ready, root, st.DEFAULT_TEXTS, st.load_variants())
        payload["judges"][judge] = {"arms": results, "hard_rows": hard}
        md += [f"## {judge}: final rows", "", "The six test texts:", "", st.markdown(results)]
        if hard:
            md += ["The ten constructed hard sentences (counts, not percentages):", "",
                   st.markdown(hard, percent=False)]
        ch = change_lines(results + hard)
        md += ["Every changed verdict:", ""] + (ch or ["- none"]) + [""]
    ready, waiting = split_ready(EXTRA, finished)
    for a, missing in waiting:
        waiting_md.append(f"- {a['name']}: waiting for {', '.join(missing)}")
    if ready:
        results, hard = st.score_all(ready, root, st.DEFAULT_TEXTS, st.load_variants())
        payload["extra"] = {"arms": results, "hard_rows": hard}
        md += ["## Sonnet against Haiku (not in the plan; added after publication)", "",
               "The plan compares each Claude judge with paid Gemma only. This comparison was asked "
               "for on 10 October 2026, after the plan was published, while the paid Gemma runs were "
               "still running. In the claim-by-claim table, \"this judge\" is Sonnet and the "
               "other judge is Haiku.", ""]
        for label, rs, pc in (("The six test texts:", results, True),
                              ("The ten constructed hard sentences (counts):", hard, False)):
            same = [r for r in rs if r["kind"] != "judges"]
            jud = [r for r in rs if r["kind"] == "judges"]
            md += [label, ""]
            if same:
                md.append(st.markdown(same, percent=pc))
            if jud:
                md.append(st.markdown_judges(jud, percent=pc).replace("paid Gemma", "Haiku"))
        ch = change_lines(results + hard)
        md += ["Every disagreement:", ""] + (ch or ["- none"]) + [""]
    md += ["## Waiting for arms", ""] + (waiting_md or ["- none"]) + [""]
    return "\n".join(md), payload


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.path.join(REPO, "data"))
    ap.add_argument("--out-dir", default=os.path.join(HERE, "results"))
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    args = ap.parse_args(argv)
    md, payload = build(args.root, args.date)
    os.makedirs(args.out_dir, exist_ok=True)
    base = os.path.join(args.out_dir, f"interim_table_{args.date}")
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(md)
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(md)
    print(f"WROTE {base}.md and {base}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
