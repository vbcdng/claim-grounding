#!/usr/bin/env python3
"""Triage the 29 frozen arbiter-settlement rows: who can usefully read each one
(task #30, step 2).

Step 1 (`ci_settlement_rows.py`) froze *which* rows settle. This file records the
one thing no data field holds — what kind of question each row asks, and
therefore who can answer it:

  * **A — the author can decide it.** The question turns on arithmetic, dates,
    a hedge word, a count, a direction, or who wrote what. No biology or
    medicine is needed beyond reading the sentences already quoted on the row.
  * **B — only a rewritten copy can be read.** The question needs a specialist
    judgement, so it is not the author's to answer, but the row can be rewritten
    into a safe subject and shown to Fable.
  * **C — nobody reads the row.** Every checker agrees and the settlement is
    plainly right; the row stays one line in a table.

A second, independent property is recorded per row: whether it **converts** —
whether the sentence can be rewritten into a different subject (transport, city
planning, education) while keeping every feature the tool reacted to. `yes`, or
`care` when the rewrite is possible but fiddly, or `no` when the tool's mistake
hangs on a specific named thing and renaming it would destroy the very thing
being tested.

**Panel, settled 2026-08-04 (the author's objection, then step 3a).** The first
three replay arms were not three independent second checkers: two were the same
DeepSeek model at different snapshots (they agreed on 24 of these 29 rows,
against 13 and 14 for the OpenAI arm), so the old `9-for-0` / `5-for-2` figures
measured snapshot stability and are withdrawn. Three new companies then answered
the same 29 rows — Alibaba (`qwen37`), Moonshot (`kimi26`) and Anthropic
(`sonnet`) — so the panel is now one arm per company across five companies, with
the second DeepSeek snapshot kept as a repeatability control that never votes.
The three panel-framed groups below are **derived from that panel**, not written
by hand, so they cannot drift from the data; the row list, the bucket split and
the two non-panel groups never depended on it.

Rows in bucket A are grouped by the question they ask, because several rows ask
the *same* question and one ruling settles the group:

  * `panel-blocks-right` — a majority of the five companies would block this
    settlement, and the settlement looks correct. This is the cost side of the
    panel decision (task #23).
  * `panel-lets-error` — a majority keeps this settlement, and the row carries a
    real error. This is the residual risk of a panel.
  * `panel-stops-error` — a majority blocks this settlement, and the row carries
    a real error. This is the benefit of a panel.
  * `label-looks-wrong` — the official answer disagrees with both blind readers,
    so the row may be a labelling mistake rather than a tool mistake.
  * `readers-disagree` — the two blind readers split, so the row measures reader
    reliability before it measures anything about the tool.

The reasons are written by hand, from reading all 29 rows in full. What the file
guarantees mechanically is coverage: every frozen row is triaged exactly once,
no invented row ids, and the bucket counts match the pinned summary
(`tests/test_ci_settlement_triage.py`).

Pure: no API calls, no network.

    python3 benchmarks/ci_settlement_triage.py --out docs/settlement_rows_2026-08-04
"""

import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

FROZEN = os.path.join(ROOT, "docs", "settlement_rows_2026-08-04", "settlement_rows.json")

BUCKETS = ("A", "B", "C")
CONVERTS = ("yes", "care", "no")
PANEL_GROUPS = ("panel-blocks-right", "panel-lets-error", "panel-stops-error")
GROUPS = {
    "panel-blocks-right": "A majority of the five companies would block this settlement, and the settlement looks right",
    "panel-lets-error": "A majority keeps this settlement, and the row carries a real error",
    "panel-stops-error": "A majority blocks this settlement, and the row carries a real error",
    "label-looks-wrong": "The official answer disagrees with both blind readers",
    "readers-disagree": "The two blind readers split",
}


def panel_group(frozen_row):
    """Which panel-framed question this row asks, from the panel's own answer.

    Derived, never hand-written: the panel changed on 2026-08-04 when three new
    companies joined, and a hand-kept copy of these three groups would have
    silently gone stale. None = the panel upholds a settlement that looks right,
    which asks nobody anything.
    """
    upholds = frozen_row["panel_scoring"]["verdict"] == "upholds"
    right = frozen_row["side"] == "false alarm removed"
    if upholds:
        return None if right else "panel-lets-error"
    return "panel-blocks-right" if right else "panel-stops-error"


# row id -> triage record.
#   bucket    A / B / C, as described in the module docstring
#   group     which question the row asks (bucket A only). "panel" means the
#             panel-framed group is derived from the frozen row by
#             panel_group(); the two reader groups are named outright.
#   tier      "first"  = read this one to answer its question
#             "second" = same question, already answered by a first-tier row
#             "table"  = not read at all
#   question  what the author would actually be deciding, in one line
#   converts  yes / care / no, plus `converts_note` when not a plain yes
TRIAGE = {
    # ---------------------------------------------------------------- pilot100
    "pilot100:cidev0003": dict(
        bucket="B", group=None, tier="table", converts="care",
        converts_note="the mistake leans on three protein names that differ by a few "
                      "letters, so a rewrite needs three near-identical names too",
        question="The claim says a protein neither binds nor destroys another. The source "
                 "shows it does not bind. Does that also show nothing was destroyed?",
        why_not_author="Answering needs a judgement about what a binding experiment can "
                       "and cannot show — a specialist call, and the two blind readers split on it.",
    ),
    "pilot100:cidev0004": dict(
        bucket="A", group="label-looks-wrong", tier="second", converts="yes",
        question="The claim says two recent studies found something. The cited source is one "
                 "paper. Is 'two studies' proven?",
        note="The row's second citation was dropped by our own converter, so the question "
             "put to the tool was unfair (task #32).",
    ),
    "pilot100:cidev0017": dict(
        bucket="A", group="panel", tier="first", converts="yes",
        question="The source tracks distress by calendar date and finds it fell. The claim says "
                 "this shows people became less sensitive to each separate event. Same thing?",
    ),
    "pilot100:cidev0029": dict(
        bucket="A", group="readers-disagree", tier="second", converts="yes",
        question="The source says counties with LOWER disability had higher infection. The claim "
                 "says counties with HIGHER disability had lower infection. Is that the same "
                 "statement turned round, or a different one?",
    ),
    "pilot100:cidev0035": dict(
        bucket="A", group="readers-disagree", tier="first", converts="yes",
        question="The claim has three parts. Every checker agrees one of them — that this was a "
                 "popular topic of online discussion — is nowhere in the source. Is the claim "
                 "supported anyway?",
    ),
    # Was a panel question until 2026-08-04: four of the five companies now drop
    # the complaint and the row is labelled accurate, so the panel and the label
    # agree and nobody has to rule on it. The question is kept for the table.
    "pilot100:cidev0038": dict(
        bucket="C", group=None, tier="table", converts="yes",
        question="The claim says its model is similar to the one used by Larremore and colleagues. "
                 "The cited source IS the Larremore paper. Four of the five companies accept the "
                 "settlement and the row is labelled accurate, so nothing needs deciding.",
    ),
    "pilot100:cidev0039": dict(
        bucket="A", group="panel", tier="first", converts="yes",
        question="A figure caption credits two named papers and names three kinds of test. The "
                 "source is one of those papers and shows two thresholds. Enough?",
    ),
    "pilot100:cidev0042": dict(
        bucket="C", group=None, tier="table", converts="yes",
        question="Word-for-word match. The official answer, both readers and all four arbiters agree.",
    ),
    "pilot100:cidev0045": dict(
        bucket="A", group="panel", tier="first", converts="yes",
        question="The source says its findings show the need for careful monitoring and inform "
                 "decisions about moving patients to intensive care. The claim says they help "
                 "raise the level of care earlier. Is that covered?",
    ),
    # Became a question on 2026-08-04: the three new companies keep the complaint
    # on a row whose figure is word-for-word in the source. That is the clearest
    # single case of the panel costing something.
    "pilot100:cidev0052": dict(
        bucket="A", group="panel", tier="first", converts="yes",
        question="The 21% figure is word-for-word in the source; only the abbreviations differ. "
                 "Four of the five companies still want the complaint kept. Do you want a rule "
                 "that keeps a complaint here?",
    ),
    "pilot100:cidev0060": dict(
        bucket="C", group=None, tier="table", converts="yes",
        question="The odds ratio, interval and p-value match exactly. The tool only objected that "
                 "the source never names its own authors. Everyone agrees the settlement is right.",
    ),
    "pilot100:cidev0062": dict(
        bucket="A", group="panel", tier="second", converts="yes",
        question="Every percentage in the claim is in the source. The claim adds the words 'as well "
                 "as general population' to one of them. Does that need its own proof?",
    ),
    # Promoted to first tier on 2026-08-04: after the three new companies joined,
    # this is the ONLY row where a majority still drops a complaint on a row that
    # carries a real error — the whole residual risk of a panel, in one row.
    "pilot100:cidev0078": dict(
        bucket="A", group="panel", tier="first", converts="yes",
        question="The source says airborne spread is 'likely' and 'suggested'. The claim says it was "
                 "'demonstrated'. Is that a real difference?",
    ),
    "pilot100:cidev0083": dict(
        bucket="A", group="panel", tier="second", converts="yes",
        question="The source measured antibodies 12 days after infection, with one kind of test. The "
                 "claim says 2 to 3 weeks, with two kinds of test.",
    ),
    "pilot100:cidev0085": dict(
        bucket="A", group="label-looks-wrong", tier="first", converts="yes",
        question="The source calls the airborne route 'likely' and 'considerably less robust'. The "
                 "claim calls the spread significant. The official answer says the claim is accurate; "
                 "both readers say it overstates.",
    ),
    "pilot100:cidev0087": dict(
        bucket="A", group="panel", tier="first", converts="yes",
        question="The claim says the virus was detectable up to 20 days. The study ran 12 days and "
                 "detection stopped at 8.",
    ),
    "pilot100:cidev0088": dict(
        bucket="C", group=None, tier="table", converts="yes",
        question="Every organ and day in the claim is in the source; only the words 'less frequently' "
                 "are unproven, and no checker objected.",
    ),
    "pilot100:cidev0089": dict(
        bucket="A", group="label-looks-wrong", tier="second", converts="yes",
        question="The claim's window is 2 to 4-6 days. The source recovered virus from the windpipe "
                 "only at day 8, and failed at day 4. The official answer still calls the claim accurate.",
    ),
    # ----------------------------------------------------------------- fresh50
    "fresh50:cidev0011": dict(
        bucket="C", group=None, tier="table", converts="yes",
        question="The confidence interval is in the source word for word; the tool had simply shown the "
                 "wrong sentence. Everyone agrees.",
    ),
    "fresh50:cidev0017": dict(
        bucket="C", group=None, tier="table", converts="yes",
        question="Word-for-word match. Everyone agrees.",
    ),
    "fresh50:cidev0024": dict(
        bucket="A", group="panel", tier="first", converts="yes",
        question="The source is about protecting patients from a FUTURE infection by resuming services. "
                 "The claim is about treating patients who already have the infection, with medicines. "
                 "Same statement?",
    ),
    "fresh50:cidev0026": dict(
        bucket="A", group="label-looks-wrong", tier="first", converts="yes",
        question="The quoted source sentence lists intensive-care need and death together, which is what "
                 "the claim says. The official answer calls the claim a contradiction; both readers pass it.",
        note="The row's second citation was dropped by our own converter (task #32).",
    ),
    "fresh50:cidev0027": dict(
        bucket="C", group=None, tier="table", converts="yes",
        question="Both figures match word for word. Everyone agrees.",
    ),
    # Same change as pilot100:cidev0052 — the new companies keep a complaint the
    # first three arms all dropped. Second tier: one ruling covers both.
    "fresh50:cidev0029": dict(
        bucket="A", group="panel", tier="second", converts="yes",
        question="Both figures match; the only quibble is whether an international registry counts "
                 "as European data. Three of the five companies want the complaint kept anyway.",
    ),
    "fresh50:cidev0030": dict(
        bucket="C", group=None, tier="table", converts="yes",
        question="Odds ratio, interval and p-value match word for word. Everyone agrees.",
    ),
    "fresh50:cidev0037": dict(
        bucket="A", group="label-looks-wrong", tier="second", converts="yes",
        question="The source is global and never names South Africa. The claim says South Africa and "
                 "elsewhere. The official answer files this as a citing-manners problem.",
    ),
    "fresh50:cidev0043": dict(
        bucket="A", group="panel", tier="second", converts="no",
        converts_note="the mistake is a confusion between two disease names that differ by a suffix; "
                      "renaming into another subject destroys the very thing being tested",
        question="'Fully recovered within two weeks' against a source reporting recovery by day 12 — and "
                 "the claim says the animals did not develop SARS, while the source is about SARS-CoV-2.",
    ),
    "fresh50:cidev0044": dict(
        bucket="A", group="panel", tier="second", converts="yes",
        question="Same arithmetic as pilot 0087: up to 20 days claimed, against a 12-day study. Both "
                 "readers call it contradicted; the official answer files it as a citing-manners problem.",
    ),
    "fresh50:cidev0045": dict(
        bucket="A", group="panel", tier="first", converts="yes",
        question="The claim says no live virus was found, only its genetic traces. The source's own "
                 "sentences report live virus grown from saliva and lung.",
    ),
}


def load_frozen(path=FROZEN):
    with open(path) as fh:
        return json.load(fh)


def arm_agreement(frozen=None):
    """How independent the arms really are, arm by arm and pair by pair.

    Born from the author's 2026-08-04 objection: two of the first three arms were
    the same DeepSeek model at different snapshots, so a majority of them was not
    a panel of companies. It now also reports the answer — with one arm per
    company, how often each single arm equals the majority, and how often each
    arm drops the complaint at all. An arm that settles far more rows than the
    others is the lenient one, whatever the majority says.
    """
    frozen = frozen or load_frozen()
    arms = list(frozen["arms"])
    voters = list(frozen.get("voters") or arms)
    rows = frozen["rows"]
    n = len(rows)
    decide = {a: [bool(r["arms"][a].get("settles")) for r in rows] for a in arms}
    pairs = {}
    for i, x in enumerate(arms):
        for y in arms[i + 1:]:
            pairs[f"{x} vs {y}"] = sum(1 for k in range(n) if decide[x][k] == decide[y][k])
    majority = [r["panel_scoring"]["verdict"] == "upholds" for r in rows]
    follows = {a: sum(1 for k in range(n) if decide[a][k] == majority[k]) for a in arms}
    settles = {a: sum(decide[a]) for a in arms}
    companies = {a: (frozen.get("arm_registry") or {}).get(a, {}).get("company")
                 for a in arms}
    return dict(n=n, arms=arms, voters=voters, companies=companies,
                pair_agreement=pairs, majority_follows_arm=follows,
                rows_settled_by_arm=settles,
                majority_upholds=sum(majority))


def triaged_rows(frozen=None):
    """Join the frozen list with the hand-written triage. Raises if they disagree."""
    frozen = frozen or load_frozen()
    ids = [r["row"] for r in frozen["rows"]]
    missing = [i for i in ids if i not in TRIAGE]
    extra = [i for i in TRIAGE if i not in ids]
    if missing:
        raise ValueError(f"rows in the frozen list with no triage: {missing}")
    if extra:
        raise ValueError(f"triage entries that are not frozen rows: {extra}")
    out = []
    for r in frozen["rows"]:
        t = TRIAGE[r["row"]]
        if t["bucket"] not in BUCKETS:
            raise ValueError(f"{r['row']}: bad bucket {t['bucket']!r}")
        if t["converts"] not in CONVERTS:
            raise ValueError(f"{r['row']}: bad converts {t['converts']!r}")
        rec = dict(t)
        if rec["group"] in PANEL_GROUPS:
            raise ValueError(f"{r['row']}: panel groups are derived — write "
                             f"group='panel', not {rec['group']!r}")
        if rec["group"] == "panel":
            rec["group"] = panel_group(r)
            rec["group_derived"] = True
            if rec["group"] is None:
                raise ValueError(
                    f"{r['row']}: marked as a panel question, but the panel "
                    "upholds a settlement that looks right — nothing to ask. "
                    "Re-triage this row by hand.")
        if t["bucket"] == "A" and rec["group"] not in GROUPS:
            raise ValueError(f"{r['row']}: bucket A needs one of {sorted(GROUPS)}")
        if t["bucket"] != "A" and rec["group"] is not None:
            raise ValueError(f"{r['row']}: only bucket A rows carry a group")
        out.append(dict(
            row=r["row"], label=r["label"], side=r["side"], headline=r["headline"],
            panel_scoring=r["panel_scoring"]["verdict"],
            arms_settling=r["panel_scoring"]["arms_settling"],
            arms_voting=r["panel_scoring"]["arms_replayed"],
            judge_verdict=r["judge"]["verdict"],
            live_arbiter=r["live_arbiter"]["action"],
            **rec,
        ))
    return out


def summary(rows):
    by_bucket, by_group, by_tier, by_converts = {}, {}, {}, {}
    for r in rows:
        by_bucket[r["bucket"]] = by_bucket.get(r["bucket"], 0) + 1
        by_tier[r["tier"]] = by_tier.get(r["tier"], 0) + 1
        by_converts[r["converts"]] = by_converts.get(r["converts"], 0) + 1
        if r["group"]:
            by_group[r["group"]] = by_group.get(r["group"], 0) + 1
    return dict(n=len(rows), by_bucket=by_bucket, by_group=by_group,
                by_tier=by_tier, by_converts=by_converts)


def markdown(rows, frozen=None):
    s = summary(rows)
    ag = arm_agreement(frozen)
    L = []
    L.append("# Who can read which settlement row — triage of all 29")
    L.append("")
    voters = [a for a in ag["arms"] if a in ag["voters"]]
    L.append(f"> **The checkers, after 2026-08-04.** The first three arms were not three "
             "independent second checkers — two were the same DeepSeek model at two snapshots — so "
             "the old two-of-three figures (9-for-0 and 5-for-2) are **withdrawn and must not be "
             f"quoted**. Three new companies then answered the same {ag['n']} rows, so the panel is "
             f"now one arm per company across {len(voters)} companies "
             + ", ".join(f"`{a}` ({ag['companies'].get(a) or '?'})" for a in voters)
             + ", with `ds0731` kept as a same-model repeatability control that never votes. How "
             "often each arm drops the complaint at all:")
    L.append(">")
    for arm, k in ag["rows_settled_by_arm"].items():
        role = "votes" if arm in ag["voters"] else "control, no vote"
        L.append(f"> - `{arm}` ({ag['companies'].get(arm) or '?'}, {role}) drops the complaint on "
                 f"**{k} of {ag['n']}** rows")
    L.append(">")
    L.append("> And how often each single arm equals the panel's answer:")
    L.append(">")
    for arm, k in ag["majority_follows_arm"].items():
        L.append(f"> - the panel equals `{arm}` alone on **{k} of {ag['n']}** rows")
    L.append(">")
    L.append("> The two DeepSeek snapshots agree with each other on "
             f"**{ag['pair_agreement']['incumbent-or vs ds0731']} of {ag['n']}** rows, far more "
             "than either agrees with any other company — which is what the author's objection "
             "said. With one arm per company the picture reverses: the arbiter the live run used "
             f"drops the complaint on {ag['rows_settled_by_arm']['incumbent-or']} of {ag['n']} "
             "rows while every other company drops it on about a third, and it is the arm the "
             "panel agrees with least.")
    L.append("")
    L.append("> **The reading list below was cut to one row on 2026-08-06 (the author's "
             "ruling).** Splitting the same rows by whether the tool was asked a fair question "
             "(`settlement_rows.md`, \"The same rate, split by whether the question was fair\") "
             "showed that 4 of the 5 false supports a majority-of-companies rule prevents sit on "
             "rows the converter broke by deleting the paper's other citations, while all 7 "
             "warnings the rule puts back onto good citations sit on fair rows. So the rule "
             "cannot be decided on this evidence: task #23 is parked behind task #32, and the "
             "only row still worth reading is `pilot100:cidev0017`, the one fair row where the "
             "rule prevents a false support (`row_pages/cidev0017.html`). The tiers below stay "
             "as they were computed, as the list to return to once the multi-cited rows ask a "
             "fair question.")
    L.append("")
    L.append("Step 1 froze *which* rows settle (`settlement_rows.md` beside this file). This "
             "page answers the next question: for each row, is the thing being decided something "
             "you can decide, something only a rewritten copy can be shown for, or nothing at all.")
    L.append("")
    L.append("Nothing here re-ran a model. The buckets come from reading all 29 rows in full — "
             "the citing sentence, the source passage, the official answer, the tool's reasoning, "
             "all four arbiter rulings and both blind readers.")
    L.append("")
    L.append("## Words used on this page")
    L.append("")
    L.append("- **Settlement row** — a row where letting the second checker (the arbiter) drop a "
             "complaint on its own changes the score.")
    L.append("- **Official answer** — the label the benchmark's creators gave the row.")
    L.append("- **Blind reader** — a model that read the row with the official answer hidden. Two of "
             "them read all 150 rows on 2026-08-03.")
    L.append("- **Majority-of-companies rule** — the proposal under decision (task #23): a complaint may "
             "only be dropped when more than half of several second checkers, each from a different "
             "company, agree it should be. Five companies answered these rows.")
    L.append("- **Repeatability control** — a second copy of a model already on the panel, run to show "
             "how much one model agrees with itself. It is never counted in a majority.")
    L.append("- **Converts** — the row's sentence can be rewritten into a different subject (transport, "
             "city planning, education) keeping every feature the tool reacted to, so a model that will "
             "not read medical text can still judge the tool's behaviour.")
    L.append("")
    L.append("## The split")
    L.append("")
    L.append("| bucket | rows | what it means |")
    L.append("|---|---|---|")
    L.append(f"| A | {s['by_bucket'].get('A', 0)} | you can decide it — arithmetic, dates, a hedge "
             "word, a count, a direction, or who wrote what |")
    L.append(f"| B | {s['by_bucket'].get('B', 0)} | needs a specialist judgement, but converts, so a "
             "rewritten copy can be shown to Fable |")
    L.append(f"| C | {s['by_bucket'].get('C', 0)} | every checker agrees and the settlement is plainly "
             "right — one line in a table, nobody reads it |")
    L.append("")
    L.append(f"Converts into a safe subject: **{s['by_converts'].get('yes', 0)} yes, "
             f"{s['by_converts'].get('care', 0)} with care, {s['by_converts'].get('no', 0)} no**. "
             "So convertibility is not what limits this work — almost everything converts. What limits "
             "it is how many rows ask a question worth anyone's time.")
    L.append("")
    L.append("## The five questions the bucket-A rows ask")
    L.append("")
    L.append("Several rows ask the same question, so one ruling settles the group. The **first-tier** "
             "row of each group is the one worth reading; the others repeat it.")
    L.append("")
    L.append("| question | rows | first-tier |")
    L.append("|---|---|---|")
    for g, desc in GROUPS.items():
        members = [r for r in rows if r["group"] == g]
        first = [r["row"] for r in members if r["tier"] == "first"]
        L.append(f"| {desc} | {len(members)} | {', '.join(f'`{f}`' for f in first) or '—'} |")
    L.append("")
    L.append(f"That makes the reading list **{s['by_tier'].get('first', 0)} rows**, with "
             f"{s['by_tier'].get('second', 0)} more available if a ruling needs a second example and "
             f"{s['by_tier'].get('table', 0)} that stay in the table.")
    L.append("")
    for tier, title, blurb in (
        ("first", "First tier — the reading list",
         "One or two rows per question. Each is a short read: two sentences and a number."),
        ("second", "Second tier — same questions, other examples",
         "Only worth building if a first-tier ruling needs a second case to stand on."),
        ("table", "Table only — nobody reads these",
         "Bucket C rows where every checker agrees, plus the one row that needs a specialist."),
    ):
        members = [r for r in rows if r["tier"] == tier]
        L.append(f"## {title}")
        L.append("")
        L.append(blurb)
        L.append("")
        L.append("| row | official answer | side | what would be decided |")
        L.append("|---|---|---|---|")
        for r in members:
            q = r["question"].replace("|", "\\|")
            L.append(f"| `{r['row']}` | {r['label']} | {r['side']} | {q} |")
        L.append("")
    L.append("## Every row, with its bucket")
    L.append("")
    L.append("| row | official answer | side | bucket | question it asks | tier | converts |")
    L.append("|---|---|---|---|---|---|---|")
    for r in rows:
        conv = r["converts"]
        if r.get("converts_note"):
            conv = f"{conv} — {r['converts_note']}"
        L.append(f"| `{r['row']}` | {r['label']} | {r['side']} | {r['bucket']} | "
                 f"{r['group'] or '—'} | {r['tier']} | {conv} |")
    L.append("")
    L.append("## Notes on single rows")
    L.append("")
    for r in rows:
        bits = []
        if r.get("why_not_author"):
            bits.append(r["why_not_author"])
        if r.get("note"):
            bits.append(r["note"])
        if r.get("converts_note") and r["converts"] != "yes":
            bits.append(f"Converts: {r['converts']} — {r['converts_note']}.")
        if bits:
            L.append(f"- **`{r['row']}`** — " + " ".join(bits))
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--frozen", default=FROZEN, help="the frozen settlement-row list (JSON)")
    ap.add_argument("--out", help="directory to write triage.md + triage.json into")
    args = ap.parse_args()

    rows = triaged_rows(load_frozen(args.frozen))
    s = summary(rows)
    text = markdown(rows)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "triage.md"), "w") as fh:
            fh.write(text)
        with open(os.path.join(args.out, "triage.json"), "w") as fh:
            json.dump(dict(summary=s, groups=GROUPS, rows=rows), fh, indent=1)
        print(f"wrote {args.out}/triage.md and triage.json")
    else:
        print(text)
    print(f"buckets: {s['by_bucket']}  tiers: {s['by_tier']}  converts: {s['by_converts']}")


if __name__ == "__main__":
    main()
