#!/usr/bin/env python3
"""Freeze the list of arbiter-settlement rows of the 2026-08-02 Citation-Integrity
run — the rows where letting the second checker (the arbiter) drop a complaint on
its own changes the score (follow-up item 3, task #30, step 1).

A **settlement row** is a row whose two-label side changes when the arbiter's
finding is folded in before scoring: the tool's own verdict layer says *flag* and
the adjudicated layer says *pass*. That fold is `wice_bench._adjudicated_bucket`
— scoring-time only, the verdict field is never touched (house rule).

The published exchange-rate table (docs/BENCHMARK_RUN_2026-08-02.md §3.3) counts
two of those sides:

  * **false alarm removed** — the row's label is ACCURATE, so the settlement is
    right: 12 on pilot100, 6 on fresh50.
  * **error let through** — the row carries a *major* error (contradicted /
    unsubstantiated / irrelevant), so the settlement is wrong: 2 on pilot100,
    3 on fresh50.

23 rows in total, and they are the reading list this task builds pages for. Six
further rows settle too but fall outside those two counts (four minor content
errors on pilot100, two etiquette rows on fresh50); they are emitted as well,
flagged `headline: false`, because on this tool's own scoring line a minor
content error is also an error let through, and hiding them would make the
exchange rate look better than it is.

For every row this writes: which side it falls on, the judge's verdict and
method, the live arbiter's ruling with its word-for-word proof quotes, the same
from every replay arm, and two separate panel simulations —

  * `panel_scoring` — how many VOTING arms' own rulings would settle the row the
    same way, i.e. would a majority of companies uphold this settlement;
  * `panel_amber` — the harness's recorded `resolve_ambers` answer per arm, which
    is the *display* decision (clear the yellow warning on the card) rather than
    the scoring one. The two are different mechanisms and are kept apart on
    purpose; §3.3's "blocked 4 of the 12 removals" is the amber one.

**One arm per company, since 2026-08-04 (the author's objection, step 3a).** The
first three arms were `incumbent-or`, `ds0731` and `luna` — but the first two are
the same DeepSeek model at two snapshots, so a two-of-three majority measured how
repeatable one model is, not what independent checkers would say. Three new
companies were run over these 29 rows on 2026-08-04 (`qwen37` = Alibaba,
`kimi26` = Moonshot, `sonnet` = Anthropic); the panel is now one arm per company
(DeepSeek, OpenAI, Alibaba, Moonshot, Anthropic) and `ds0731` is kept as a
same-model repeatability control that never votes. See `ARMS` below.

Rows outside the replay sample (69 of 100 and 39 of 50 were replayed) get
`panel_scoring.verdict = "not replayed"` rather than a guessed answer.

Pure: no API calls, no network. Everything comes off disk.

    python3 benchmarks/ci_settlement_rows.py --out docs/settlement_rows_2026-08-04

Totals are asserted against the published figures by
`tests/test_ci_settlement_rows.py`, so this list cannot drift from the document
it explains.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from ci_batch_ids import batch_tag, qualify  # noqa: E402
from citation_integrity_bench import (ACCURATE, ETIQUETTE, MAJOR,  # noqa: E402
                                      MINOR_CONTENT, PROVENANCE, _collapse,
                                      _row_co_citation, grounding_side,
                                      strict_side)
from wice_bench import _adjudicated_bucket, _tool_bucket  # noqa: E402

# Every replay arm, in reading order. `company` is what makes a panel a panel:
# two arms from one company are one voice, however differently they are named.
# `votes` False = present for comparison only, never counted in a majority.
ARMS = {
    "incumbent-or": {"company": "DeepSeek", "votes": True, "run": "2026-08-02",
                     "note": "the model the live run used, via OpenRouter"},
    "ds0731": {"company": "DeepSeek", "votes": False, "run": "2026-08-02",
               "note": "same model, later snapshot — repeatability control, "
                       "not a second voice"},
    "luna": {"company": "OpenAI", "votes": True, "run": "2026-08-02",
             "note": "gpt-5.6-luna"},
    "qwen37": {"company": "Alibaba", "votes": True, "run": "2026-08-04",
               "note": "qwen3.7-flash via OpenRouter, hidden reasoning off"},
    "kimi26": {"company": "Moonshot", "votes": True, "run": "2026-08-04",
               "note": "kimi-k2.6 on the Kimi platform, thinking disabled"},
    "sonnet": {"company": "Anthropic", "votes": True, "run": "2026-08-04",
               "note": "claude-code/sonnet — also a blind reader and the "
                       "benchmark grader, so its agreement with those is "
                       "never independent confirmation"},
}

VOTERS = tuple(a for a, m in ARMS.items() if m["votes"])
CONTROLS = tuple(a for a, m in ARMS.items() if not m["votes"])

# The two batches of the 2026-08-02 run, as (batch dir, run dir, replay dirs),
# all relative to the repo root. The third element may be one path or several —
# an arm is looked up in each in turn, so the 2026-08-04 arms live in their own
# workspace without the earlier one being touched. Overridable on the command line.
BATCHES = (
    ("data/citation_integrity/batch_dev_pilot100",
     "data/citation_integrity/batch_dev_pilot100_run_gemma_0802",
     ("docs/arbiter_replay_2026-08-02/pilot100",
      "docs/arbiter_replay_2026-08-04/pilot100")),
    ("data/citation_integrity/batch_dev_fresh50",
     "data/citation_integrity/batch_dev_fresh50_run_gemma_0802",
     ("docs/arbiter_replay_2026-08-02/fresh50",
      "docs/arbiter_replay_2026-08-04/fresh50")),
)

# What the published §3.3 table says, so a drift is a test failure and not a
# quietly different document.
PUBLISHED = {"pilot100": {"false_alarm_removed": 12, "major_error_let_through": 2},
             "fresh50": {"false_alarm_removed": 6, "major_error_let_through": 3}}


def band(label):
    """Which family of labels this row belongs to."""
    if label == ACCURATE:
        return "accurate"
    if label in MAJOR:
        return "major"
    if label in MINOR_CONTENT:
        return "minor-content"
    if label in PROVENANCE:
        return "provenance"
    if label in ETIQUETTE:
        return "etiquette"
    return "unknown"


def _side(row_label, adjudicated):
    """(side name, is it one of the published table's two counts?)

    `adjudicated` is the collapsed two-label side the settlement produces —
    always "pass" here, since a settlement is by definition a dropped complaint.
    """
    b = band(row_label)
    if b == "accurate":
        return "false alarm removed", True
    if b == "major":
        return "major error let through", True
    if b == "minor-content":
        return "minor content error let through", False
    if b == "provenance":
        # PROVENANCE passes on this tool's line, so a dropped complaint is right
        return "false alarm removed (provenance)", False
    if b == "etiquette":
        return "etiquette row, outside the tally", False
    return "unknown", False


def _arbiter_view(arb):
    """The comparable part of an arbiter payload — live or replayed."""
    arb = arb or {}
    proofs = [p for p in (arb.get("proofs") or []) if p]
    return {
        "model": arb.get("model"),
        "action": arb.get("action"),
        "trigger": arb.get("trigger"),
        "conflict": bool(arb.get("conflict")),
        "n_proofs": len(proofs),
        "quotes_dropped": arb.get("quotes_dropped"),
        "missing_subclaim": arb.get("missing_subclaim"),
        "rewrite_suggestion": arb.get("rewrite_suggestion"),
        "why": arb.get("why"),
        "proofs": proofs,
    }


def _settles(claim, arbiter_payload):
    """Would this arbiter payload settle the row? (bucket, reason, settles?)

    The claim is copied with its arbiter replaced, so the base bucket — which
    does not depend on the arbiter — stays exactly what the run recorded.
    """
    probe = dict(claim)
    probe["arbiter"] = arbiter_payload
    bucket, why = _adjudicated_bucket(probe)
    return _collapse(bucket), why, _collapse(bucket) == "pass"


def _replay_roots(replay_dir):
    """One path or several — always returned as a tuple."""
    if isinstance(replay_dir, (list, tuple)):
        return tuple(replay_dir)
    return (replay_dir,)


def load_batch(batch_dir, run_dir, replay_dir):
    """Ground truth, claims by marker, and every arm's replay row by claim id.

    `replay_dir` may name several workspaces; an arm is taken from the first one
    that has it, so arms run on different days keep their own directories.
    """
    with open(os.path.join(batch_dir, "ci_ground_truth.json"),
              encoding="utf-8") as f:
        gt = json.load(f)["claims"]
    with open(os.path.join(run_dir, "analysis.json"), encoding="utf-8") as f:
        analysis = json.load(f)
    by_key = {}
    for c in analysis.get("text_claims", []):
        for key in c.get("markers") or []:
            by_key.setdefault(key, c)
    replays = {}
    for arm in ARMS:
        rows = {}
        for root in _replay_roots(replay_dir):
            path = os.path.join(root, "replays", arm, "results.jsonl")
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        r = json.loads(line)
                        rows[r["claim_id"]] = r
            break
        replays[arm] = rows
    return gt, by_key, replays


def _majority(n_yes, n_seen):
    """Strict majority of the arms that actually answered."""
    return n_seen > 0 and n_yes * 2 > n_seen


def settlement_rows(batch_dir, run_dir, replay_dir):
    """Every row of one batch whose side changes when the arbiter may settle."""
    tag = batch_tag(batch_dir)
    gt, by_key, replays = load_batch(batch_dir, run_dir, replay_dir)
    out = []
    for key, g in sorted(gt.items(), key=lambda kv: kv[0]):
        claim = by_key.get(key)
        if claim is None:
            continue
        tool = _collapse(_tool_bucket(claim))
        adj_bucket, adj_why = _adjudicated_bucket(claim)
        adj = _collapse(adj_bucket)
        if adj == tool:
            continue
        label = g["label"]
        side, headline = _side(label, adj)
        arms, n_settle, n_seen = {}, 0, 0
        for arm in ARMS:
            r = replays[arm].get(claim["id"])
            if r is None:
                arms[arm] = {"replayed": False}
                continue
            view = _arbiter_view(r.get("new_payload"))
            _, why, settles = _settles(claim, r.get("new_payload"))
            if ARMS[arm]["votes"]:          # controls never enter the majority
                n_seen += 1
                n_settle += bool(settles)
            amber = r.get("amber") or {}
            rescue = r.get("rescue") or {}
            arms[arm] = {
                "replayed": True,
                "company": ARMS[arm]["company"],
                "votes": ARMS[arm]["votes"],
                "ruling": view,
                "settles": settles,
                "settle_reason": why,
                "same_action_as_live": bool(r.get("action_match")),
                "amber_eligible": bool(amber.get("eligible")),
                "amber_would_resolve": amber.get("would_resolve"),
                "rescue_proposed": bool(rescue.get("proposed")),
                "rescue_would_flip": rescue.get("would_flip"),
            }
        if n_seen == 0:
            panel = {"verdict": "not replayed", "arms_settling": None,
                     "arms_replayed": 0, "companies_settling": [],
                     "companies_blocking": []}
        else:
            panel = {"verdict": "upholds" if _majority(n_settle, n_seen)
                     else "blocks",
                     "arms_settling": n_settle, "arms_replayed": n_seen,
                     "companies_settling": [ARMS[a]["company"] for a in VOTERS
                                            if arms.get(a, {}).get("settles")],
                     "companies_blocking": [
                         ARMS[a]["company"] for a in VOTERS
                         if arms.get(a, {}).get("replayed")
                         and not arms[a].get("settles")]}
        amber_votes = [arms[a].get("amber_would_resolve") for a in VOTERS
                       if arms.get(a, {}).get("amber_eligible")]
        panel_amber = {
            "arms_eligible": len(amber_votes),
            "arms_would_resolve": sum(1 for v in amber_votes if v),
        }
        if not amber_votes:
            panel_amber["verdict"] = "not an amber row in the replay"
        else:
            panel_amber["verdict"] = (
                "upholds" if _majority(panel_amber["arms_would_resolve"],
                                       len(amber_votes)) else "blocks")
        # The repeatability control, reported next to the panel and never in it.
        control = {}
        for a in CONTROLS:
            v = arms.get(a, {})
            if v.get("replayed"):
                control[a] = {
                    "settles": v.get("settles"),
                    "agrees_with_same_company_voter": next(
                        (v.get("settles") == arms[o].get("settles")
                         for o in VOTERS
                         if ARMS[o]["company"] == ARMS[a]["company"]
                         and arms.get(o, {}).get("replayed")), None),
                }
        out.append({
            "row": qualify(tag, key),
            "batch": tag,
            "cidev": key,
            "ci_id": g.get("ci_id"),
            "label": label,
            "band": band(label),
            "strict_side": strict_side(label),
            "grounding_side": grounding_side(label),
            "side": side,
            "headline": headline,
            "fair_question": _row_co_citation(g)[0]["is_single_cited"],
            "settled_from": tool,
            "settled_to": adj,
            "settlement_reason": adj_why,
            "cleared_in_viewer_too": claim.get("proof_state") == "arbiter_resolved",
            "judge": {
                "claim_id": claim["id"],
                "verdict": claim.get("verdict"),
                "method": claim.get("method"),
                "proof_state": claim.get("proof_state"),
                "partial_support": bool(claim.get("partial_support")),
                "reason": claim.get("reason"),
                "text": claim.get("text"),
            },
            "live_arbiter": _arbiter_view(claim.get("arbiter")),
            "arms": arms,
            "panel_scoring": panel,
            "panel_amber": panel_amber,
            "repeatability_control": control,
        })
    return out


def panel_effect(rows):
    """What the exchange rate becomes if a settlement needs a majority of the
    VOTING arms — one per company — to agree. Counted on the published rows only,
    and on the scoring panel (`panel_scoring`), which is the complete one —
    every settlement row of this run happened to be in the replay sample.
    """
    out = {}
    for r in rows:
        if not r["headline"]:
            continue
        t = out.setdefault(r["batch"], {"false_alarm_removed": 0,
                                        "major_error_let_through": 0,
                                        "blocked_correct": 0,
                                        "blocked_wrong": 0,
                                        "no_panel_data": 0})
        verdict = r["panel_scoring"]["verdict"]
        right = r["side"] == "false alarm removed"
        if verdict == "not replayed":
            t["no_panel_data"] += 1
            continue
        if verdict == "upholds":
            t["false_alarm_removed" if right else
              "major_error_let_through"] += 1
        else:
            t["blocked_correct" if right else "blocked_wrong"] += 1
    return out


def panel_effect_by_fairness(rows):
    """The same exchange rate, split by whether the row asks a FAIR question.

    A fair row cited one article, so the single source the benchmark hands over
    really is supposed to carry the whole sentence and a warning is the tool's
    own responsibility. On a multi-cited row the converter deleted the paper's
    other citations, so the tool was asked to prove a whole sentence from a
    fraction of its support — a warning there is partly the setup's fault
    (task #32, `_co_citation`).

    Why this split decides task #23 (2026-08-06): the majority-of-companies
    rule prevents 5 false supports for 7 warnings put back on good citations,
    which reads as a fair trade — but 4 of those 5 sit on multi-cited rows and
    every one of the 7 sits on a fair row. On fair questions alone the rule
    prevents ONE false support. Its apparent value comes from a defect in the
    benchmark's own preparation, so the rule cannot be decided until multi-cited
    rows ask a fair question. Both batches are pooled: the split is 17 fair and
    6 unfair rows in total, and per batch the cells fall to one or two rows.
    """
    out = {}
    for r in rows:
        if not r["headline"] or r["panel_scoring"]["verdict"] == "not replayed":
            continue
        t = out.setdefault("fair" if r["fair_question"] else "unfair",
                           {"kept_and_right": 0, "kept_and_wrong": 0,
                            "blocked_and_right": 0, "blocked_and_wrong": 0,
                            "rows": []})
        upholds = r["panel_scoring"]["verdict"] == "upholds"
        right = r["side"] == "false alarm removed"
        t["kept_and_right" if upholds and right else
          "kept_and_wrong" if upholds else
          "blocked_and_right" if right else "blocked_and_wrong"] += 1
        t["rows"].append(r["row"])
    return out


def totals(rows):
    """Per-batch counts, in the shape the published table uses."""
    out = {}
    for r in rows:
        t = out.setdefault(r["batch"], {"false_alarm_removed": 0,
                                        "major_error_let_through": 0,
                                        "other_settlements": 0,
                                        "headline": 0, "all": 0})
        t["all"] += 1
        if r["side"] == "false alarm removed":
            t["false_alarm_removed"] += 1
        elif r["side"] == "major error let through":
            t["major_error_let_through"] += 1
        else:
            t["other_settlements"] += 1
        t["headline"] += bool(r["headline"])
    return out


def build(batches=BATCHES, root=ROOT):
    rows = []
    for batch_dir, run_dir, replay_dir in batches:
        rows += settlement_rows(os.path.join(root, batch_dir),
                                os.path.join(root, run_dir),
                                [os.path.join(root, p)
                                 for p in _replay_roots(replay_dir)])
    tot = totals(rows)
    rescue = rescue_candidates(batches, root)
    return {"generated_from": [list(b) for b in batches],
            "arms": list(ARMS),
            "arm_registry": {a: dict(m) for a, m in ARMS.items()},
            "voters": list(VOTERS),
            "controls": list(CONTROLS),
            "published": PUBLISHED,
            "totals": tot,
            "panel_effect": panel_effect(rows),
            "panel_effect_by_fairness": panel_effect_by_fairness(rows),
            "n_rows": len(rows),
            "n_headline": sum(1 for r in rows if r["headline"]),
            "rows": rows,
            "rescue_candidates": rescue}


def rescue_candidates(batches=BATCHES, root=ROOT):
    """The separate mechanism: rows where an arm's proof made the primary judge
    change a verdict outright (`rescue.would_flip`), plus the one flip the live
    run actually made. Not settlements — a settlement leaves the verdict alone —
    but the same decision in task #23 covers both, so they are listed here
    rather than found again by hand.
    """
    out = []
    for batch_dir, run_dir, replay_dir in batches:
        tag = batch_tag(batch_dir)
        gt, by_key, replays = load_batch(os.path.join(root, batch_dir),
                                         os.path.join(root, run_dir),
                                         [os.path.join(root, p)
                                          for p in _replay_roots(replay_dir)])
        label_of = {}
        for key, g in gt.items():
            c = by_key.get(key)
            if c is not None:
                label_of[c["id"]] = (key, g["label"])
        seen = {}
        for key, g in sorted(gt.items()):
            c = by_key.get(key)
            if c is not None and c.get("method") == "arbiter_rescue":
                seen[c["id"]] = {"row": qualify(tag, key), "batch": tag,
                                 "label": g["label"], "live_flip": True,
                                 "arms_flipping": []}
        for arm in ARMS:
            for cid, r in replays[arm].items():
                if not (r.get("rescue") or {}).get("would_flip"):
                    continue
                key, label = label_of.get(cid, (cid, None))
                rec = seen.setdefault(cid, {"row": qualify(tag, key),
                                            "batch": tag, "label": label,
                                            "live_flip": False,
                                            "arms_flipping": []})
                rec["arms_flipping"].append(arm)
        for cid, rec in sorted(seen.items()):
            voting = [a for a in rec["arms_flipping"] if ARMS[a]["votes"]]
            rec["voting_arms_flipping"] = voting
            rec["companies_flipping"] = sorted(
                {ARMS[a]["company"] for a in voting})
            n_seen = sum(1 for a in VOTERS if replays[a].get(cid))
            rec["voting_arms_answering"] = n_seen
            rec["panel_scoring"] = ("upholds" if _majority(len(voting), n_seen)
                                    else "blocks")
            out.append(rec)
    return out


# ---------- human-readable table ------------------------------------------

def _short(s, n=90):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def markdown(data):
    L = []
    L.append("# Arbiter-settlement rows, 2026-08-02 run")
    L.append("")
    L.append("A **settlement row** is a row where letting the second checker "
             "(the arbiter) drop a complaint on its own changes the score: the "
             "tool's own verdict layer flags it, the adjudicated layer passes "
             "it. Nothing here re-ran a model — every column is read off the "
             "run and replay files already on disk.")
    L.append("")
    L.append(f"{data['n_rows']} rows settle in total; "
             f"{data['n_headline']} of them are the ones the published "
             "exchange-rate table counts.")
    L.append("")
    L.append("| batch | false alarms removed | major errors let through | "
             "other settlements | published |")
    L.append("|---|---|---|---|---|")
    for tag, t in data["totals"].items():
        p = data["published"].get(tag, {})
        ok = (t["false_alarm_removed"] == p.get("false_alarm_removed")
              and t["major_error_let_through"]
              == p.get("major_error_let_through"))
        L.append(f"| {tag} | {t['false_alarm_removed']} | "
                 f"{t['major_error_let_through']} | {t['other_settlements']} | "
                 f"{p.get('false_alarm_removed')}/"
                 f"{p.get('major_error_let_through')} "
                 f"{'reproduced' if ok else 'DRIFT'} |")
    L.append("")
    L.append("## Who the checkers are")
    L.append("")
    L.append("A panel is only a panel if the checkers come from different "
             "companies. The first three arms did not qualify: two of them were "
             "the same DeepSeek model at two snapshots (they agreed on 24 of "
             "these 29 rows, against 13 and 14 for the OpenAI arm), so a "
             "two-of-three majority measured one model's repeatability. Three "
             "new companies were run over the same 29 rows on 2026-08-04. **The "
             "old 9-for-0 and 5-for-2 figures are withdrawn and are not "
             "reproduced here.**")
    L.append("")
    L.append("| arm | company | counts in the majority | what it is |")
    L.append("|---|---|---|---|")
    for arm, meta in ARMS.items():
        L.append(f"| `{arm}` | {meta['company']} | "
                 f"{'yes' if meta['votes'] else 'no'} | {meta['note']} |")
    L.append("")
    L.append("## What a majority-of-companies rule would do to these rows")
    L.append("")
    L.append(f"Every voting arm ({len(VOTERS)} companies) answered the same "
             "complaints. Requiring more than half of them to agree before a "
             "complaint may be dropped changes the exchange rate like this — "
             "kept means the settlement still happens, blocked means the "
             "complaint stays on the card.")
    L.append("")
    L.append("| batch | kept, and right | kept, and wrong | blocked, and it was "
             "right | blocked, and it was wrong | no panel data |")
    L.append("|---|---|---|---|---|---|")
    for tag, e in data["panel_effect"].items():
        L.append(f"| {tag} | {e['false_alarm_removed']} | "
                 f"{e['major_error_let_through']} | {e['blocked_correct']} | "
                 f"{e['blocked_wrong']} | {e['no_panel_data']} |")
    L.append("")
    L.append("### The same rate, split by whether the question was fair")
    L.append("")
    L.append("A **fair** row cited one article, so the single source handed to "
             "the tool really is supposed to carry the whole sentence. On a "
             "**multi-cited** row the original paper cited several articles and "
             "the converter deleted all but one, so the tool was asked to prove "
             "a whole sentence from a fraction of its support (task #32).")
    L.append("")
    L.append("| rows | kept, and right | kept, and wrong | blocked, and it was "
             "right | blocked, and it was wrong |")
    L.append("|---|---|---|---|---|")
    for tag in ("fair", "unfair"):
        e = data.get("panel_effect_by_fairness", {}).get(tag)
        if not e:
            continue
        name = ("fair — one citation" if tag == "fair"
                else "unfair — several citations")
        L.append(f"| {name} | {e['kept_and_right']} | {e['kept_and_wrong']} | "
                 f"{e['blocked_and_right']} | {e['blocked_and_wrong']} |")
    L.append("")
    L.append("**Blocked, and it was wrong** is the column the majority rule "
             "exists for: a false support prevented. Most of those sit on rows "
             "the converter broke, while every warning the rule puts back onto "
             "a good citation sits on a fair row. On fair questions alone the "
             "rule buys very little, so task #23 cannot be decided until "
             "task #32 is.")
    L.append("")
    L.append("## Every settlement row")
    L.append("")
    L.append("`panel (score)` = how many of the voting arms — one per company — "
             "would settle the row the same way, and whether a majority of them "
             "upholds it. "
             "`panel (amber)` = the same question for the display decision — "
             "clearing the yellow warning on the card — which is a different "
             "mechanism and is counted separately.")
    L.append("")
    L.append("| row | label | side | in the table | settled from | live arbiter |"
             " proofs | in viewer | panel (score) | panel (amber) |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in data["rows"]:
        ps, pa = r["panel_scoring"], r["panel_amber"]
        score_cell = (f"{ps['verdict']} {ps['arms_settling']}/"
                      f"{ps['arms_replayed']}" if ps["arms_settling"] is not None
                      else ps["verdict"])
        amber_cell = (f"{pa['verdict']} {pa['arms_would_resolve']}/"
                      f"{pa['arms_eligible']}" if pa["arms_eligible"]
                      else "n/a")
        L.append(f"| `{r['row']}` | {r['label']} | {r['side']} | "
                 f"{'yes' if r['headline'] else 'no'} | {r['settled_from']} | "
                 f"{r['live_arbiter']['action']} | "
                 f"{r['live_arbiter']['n_proofs']} | "
                 f"{'yes' if r['cleared_in_viewer_too'] else 'no'} | "
                 f"{score_cell} | {amber_cell} |")
    L.append("")
    L.append("## Verdict changes — a different mechanism, listed for task #23")
    L.append("")
    L.append("A settlement leaves the verdict alone. These rows are the ones "
             "where a proof quote made the primary judge change a verdict "
             "outright, in the live run or in a replay.")
    L.append("")
    L.append("| row | label | changed in the live run | arms whose proof "
             "changed it | companies | majority of companies |")
    L.append("|---|---|---|---|---|---|")
    for r in data["rescue_candidates"]:
        L.append(f"| `{r['row']}` | {r['label']} | "
                 f"{'yes' if r['live_flip'] else 'no'} | "
                 f"{', '.join(r['arms_flipping']) or 'none'} | "
                 f"{', '.join(r.get('companies_flipping') or []) or 'none'} | "
                 f"{r['panel_scoring']} |")
    L.append("")
    L.append("## What each row says, in order")
    L.append("")
    for r in data["rows"]:
        L.append(f"### `{r['row']}` — {r['label']} — {r['side']}")
        L.append("")
        L.append(f"- source row id: `{r['ci_id']}`")
        L.append(f"- judge: **{r['judge']['verdict']}** via "
                 f"`{r['judge']['method']}` — {_short(r['judge']['reason'], 240)}")
        L.append(f"- settlement: {r['settled_from']} → {r['settled_to']} "
                 f"({_short(r['settlement_reason'], 160)})")
        live = r["live_arbiter"]
        L.append(f"- live arbiter (`{live['model']}`): **{live['action']}**, "
                 f"{live['n_proofs']} proof quote(s), "
                 f"{live['quotes_dropped']} dropped by the word-for-word check")
        if live["missing_subclaim"]:
            L.append(f"  - what it said was missing: "
                     f"{_short(live['missing_subclaim'], 240)}")
        for arm in ARMS:
            a = r["arms"][arm]
            tail = ("" if ARMS[arm]["votes"]
                    else " (control, does not vote)")
            if not a["replayed"]:
                L.append(f"- `{arm}`{tail}: not in the replay sample")
                continue
            L.append(f"- `{arm}` ({ARMS[arm]['company']}){tail}: "
                     f"**{a['ruling']['action']}**, "
                     f"{a['ruling']['n_proofs']} proof quote(s) — "
                     f"{'would settle' if a['settles'] else 'would not settle'}"
                     + (f", amber: "
                        f"{'clear' if a['amber_would_resolve'] else 'keep'}"
                        if a["amber_eligible"] else ""))
        L.append("")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", help="directory for settlement_rows.json + .md "
                                  "(default: print the table only)")
    ap.add_argument("--batch", action="append", nargs=3,
                    metavar=("BATCH_DIR", "RUN_DIR", "REPLAY_DIR"),
                    help="override the built-in batches; repeatable")
    args = ap.parse_args()
    batches = tuple(tuple(b) for b in args.batch) if args.batch else BATCHES
    data = build(batches)
    md = markdown(data)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "settlement_rows.json"), "w",
                  encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
        with open(os.path.join(args.out, "settlement_rows.md"), "w",
                  encoding="utf-8") as f:
            f.write(md)
        print(f"wrote {args.out}/settlement_rows.json + settlement_rows.md")
    for tag, t in data["totals"].items():
        p = data["published"].get(tag, {})
        print(f"{tag}: {t['false_alarm_removed']} false alarms removed, "
              f"{t['major_error_let_through']} major errors let through "
              f"(published {p.get('false_alarm_removed')}/"
              f"{p.get('major_error_let_through')}), "
              f"{t['other_settlements']} further settlements outside the table")
    print(f"total settlement rows {data['n_rows']}, "
          f"of them in the published table {data['n_headline']}")
    print(f"voting arms (one per company): {', '.join(VOTERS)}; "
          f"control, never counted: {', '.join(CONTROLS)}")
    for tag, e in data["panel_effect"].items():
        print(f"{tag} under a majority-of-companies rule: "
              f"{e['false_alarm_removed']} false alarms still removed, "
              f"{e['major_error_let_through']} major errors still let through "
              f"(blocked {e['blocked_correct']} right settlements and "
              f"{e['blocked_wrong']} wrong ones)")
    if not args.out:
        print()
        print(md)


if __name__ == "__main__":
    main()
