#!/usr/bin/env python3
"""Stability study (internal label CARD160): the stability and invariance
table. No model calls — it only reads finished gate runs
(data/gate_<tag>_<text>/analysis.json).

For every arm it compares the arm's verdicts with its baseline arm's verdicts,
claim by claim, and reports the share that changed, split by direction:

  green turned red   supported in the baseline, not supported in the arm
  red turned green   unsupported in the baseline, supported in the arm
  other              a claim moved into or out of the uncited class ("own")

next to the arm's accuracy on the gate's hand-checked rows (the same two scorers
the gate prints: regression_check for paper1/bentonite/chimp, coverage_check for
essay/bohemia/pots, plus the card's hard-rows key when that text was run).

Arm kinds
  same      the arm judged the same texts (a repeat, a second judge model, a
            reworded instruction, a shuffled evidence order, a biased note,
            a doubting note).
            Claims are paired by their text (claim ids are positional, so a
            text join is the safe one). Two claims with the same text cannot
            be paired that way; they are dropped and counted
            ("dropped_duplicates").
  reworded  the arm judged copies of the texts in which claims were reworded
            with the same meaning (claim_variants.json).
            Each varied claim is paired with its original in the baseline;
            every changed verdict is an invariance failure.
  negated   the arm judged copies in which supported claims were turned into
            their opposite. The expected verdict is "unsupported"; a negated
            claim that stays supported is a missed negation. The catch rate is
            given three ways: over every negated claim supported in r1, without
            the claims that cite two or more sources, and over the claims
            supported in all three unchanged runs (r1, r2, r3). Also counted:
            the claims supported in r1 that have no negated copy at all
            ("supported_without_negated_copy"): the copies were chosen from an
            earlier run, before any run of the measured version, so a claim
            that run did not judge supported was never negated and is never
            tested.
  judges    two judges with three unchanged runs each ("runs",
            "baseline_runs"), claim by claim: the disagreement counted only on
            claims where all three runs of EACH judge agree, and majority
            verdict against majority verdict (score_judges).

Directions carry their own denominators: "green turned red" is out of the
claims supported in the baseline, "red turned green" out of those unsupported
in it. "Other" counts a claim that moved into or out of a class that is
neither supported nor unsupported (for example "own", the writer's uncited
words).

A one-run arm (prompt, shuffle, bias, doubt, reworded, negated) names its judge's
repeats ("repeats": r2 and r3). Each changed verdict is then sorted into
"moved by the arm" (r1, r2 and r3 agree, the arm differs from all three),
"also changed in a repeat", or "repeats unknown", and the arm is read against
the range of the judge's three repeat pairs (r2-r1, r3-r1, r3-r2,
"role": "repeat"): a count at or below the largest is "within the range of the
repeats". No significance test is made for any comparison. On changed claims
that have a hand-checked row, both runs' correct counts are given. The sellers
that answered each run's calls are counted, and per changed claim the sellers
of that claim's calls (the runner records the claim of every call).

Usage
  ./venv/bin/python3 benchmarks/card160/stability_table.py ARMS.json [--root data]
        [--out table.json] [--markdown table.md]

ARMS.json: {"arms": [{"name": "...", "kind": "same|reworded|negated",
                      "run": "<tag>", "baseline": "<tag>"}, ...],
            "texts": [...optional, default the six gate texts + "hard"]}
  baseline  accuracy only: "run" and "baseline" name the same arm, nothing is
            compared (gives the first run's own accuracy row).

A text whose folder is missing in either run is left out of that arm and named.
A text whose run stamp (.card160_arm, written by arm_verify.py) names any
other commit than the measured one, an edited checkout or another arm, is
left out the same way and named under "wrong version". A study folder (tag
starting "card160") without a stamp is left out the same way: it did not go
through the runner. Folders of runs made before the study carry no stamp and
are read as they are.

Failed model calls: a claim whose verdict came from a failed call
(judge_error, or a reason starting "no LLM response" / "LLM judgment
unparseable") in EITHER run is LEFT OUT of the comparison and of the accuracy,
and counted in "left out (failed call)" — it is never a changed verdict and
never a wrong row. A run with any failed call is also marked NOT CLEAN in the
notes, and is still scored: only its failed claims are left out; a claim
where only an extra check failed (checks_failed) keeps its verdict in the
comparison, because that check never changes the verdict.
"""

import argparse
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "benchmarks"))
import regression_check            # noqa: E402
import coverage_check              # noqa: E402

GATE_TEXTS = ("paper1", "bentonite", "chimp", "essay", "bohemia", "pots")
DEFAULT_TEXTS = GATE_TEXTS + ("hard",)
KEYS = {
    "paper1": ("regression", "benchmarks/paper1_ground_truth.json"),
    "bentonite": ("regression", "benchmarks/bentonite_ground_truth.json"),
    "chimp": ("regression", "benchmarks/chimpanzee_ground_truth.json"),
    "essay": ("coverage", "benchmarks/coverage_ground_truth_essay.json"),
    "bohemia": ("coverage", "benchmarks/coverage_ground_truth_bohemia.json"),
    "pots": ("coverage", "benchmarks/coverage_ground_truth_pots.json"),
    "hard": ("regression", "benchmarks/card160/hard_rows_ground_truth.json"),
}
VARIANTS = os.path.join(HERE, "claim_variants.json")
JUDGED = ("supported", "unsupported")
MEASURED_COMMIT = "4899c40"


def norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", s).casefold().strip()


def strip_markers(s):
    return re.sub(r"\[\[[^\]]*\]\]", "", s or "")


def run_dir(root, tag, text):
    return os.path.join(root, f"gate_{tag}_{text}")


def load(root, tag, text):
    p = os.path.join(run_dir(root, tag, text), "analysis.json")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


STUDY_PREFIX = "card160"
# Written by arm_verify.retry_loop when a text's retrying ends with claims
# whose verdict still came from a failed call.
RETRY_REPORT = ".card160_retry_report.json"


def wrong_version(root, tag, text, want=MEASURED_COMMIT):
    """'' when the folder's run stamp names the measured commit with a clean
    checkout and this arm; else what is wrong. A study folder (tag starting
    "card160") MUST carry the stamp the runner writes, because a run that went
    past the runner (a gate PROMPTS sends it through verify_with_prompts.py)
    has none. A folder of an earlier run made before the study has no stamp
    and is read as it is."""
    p = os.path.join(run_dir(root, tag, text), ".card160_arm")
    if not os.path.isfile(p):
        return ("no run stamp: this study folder was not made through the study's runner"
                if tag.startswith(STUDY_PREFIX) else "")
    try:
        with open(p, encoding="utf-8") as f:
            stamp = json.load(f)
    except ValueError:
        return "unreadable stamp"
    commit = stamp.get("checkout_commit", "")
    if not commit.startswith(want[:7]) or commit.endswith("+dirty"):
        return commit or "no commit recorded"
    if tag.startswith(STUDY_PREFIX) and stamp.get("CARD160_ARM") != tag:
        return f"stamped by arm {stamp.get('CARD160_ARM')!r}, not {tag}"
    return ""


def direction(va, vb):
    """'' when equal, else one of green_to_red / red_to_green / other."""
    if va == vb:
        return ""
    if va == "supported" and vb != "supported":
        return "green_to_red" if vb == "unsupported" else "other"
    if va == "unsupported" and vb == "supported":
        return "red_to_green"
    return "other"


def duplicate_texts(a):
    """Claims of one run whose normalised text occurs more than once."""
    seen = {}
    for c in a.get("text_claims", []):
        k = norm(c.get("text"))
        seen[k] = seen.get(k, 0) + 1
    return sum(n for n in seen.values() if n > 1)


def pairs_same(base, arm):
    """[(base claim, arm claim)] paired by normalised text; duplicate texts
    dropped (they cannot be paired unambiguously; duplicate_texts counts them)."""
    def index(a):
        out, dup = {}, set()
        for c in a.get("text_claims", []):
            k = norm(c.get("text"))
            if k in out:
                dup.add(k)
            out[k] = c
        for k in dup:
            out.pop(k, None)
        return out
    bi, ai = index(base), index(arm)
    return [(bi[k], ai[k]) for k in bi if k in ai]


def load_variants(path=VARIANTS):
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [r for r in json.load(f).get("rows", []) if r.get("md_original")]


def pairs_variant(base, arm, rows, field):
    """[(base claim, arm claim, row)] for the variant rows of one text. The arm's
    claim is found by id (positions do not move: a variant replaces words inside
    one claim and keeps every marker) and checked to hold the new words; the
    baseline claim by id and checked to hold the old words. A row failing either
    check is returned in the second list, never silently paired."""
    bi = {c.get("id"): c for c in base.get("text_claims", [])}
    ai = {c.get("id"): c for c in arm.get("text_claims", [])}
    ok, bad = [], []
    for r in rows:
        new = r.get(field)
        if not new:
            continue
        b, a = bi.get(r["id"]), ai.get(r["id"])
        if b is None or a is None:
            bad.append((r, "claim id missing in a run"))
        elif norm(strip_markers(r["md_original"])) not in norm(b.get("text")):
            bad.append((r, "baseline claim does not hold the original words"))
        elif norm(strip_markers(new)) not in norm(a.get("text")):
            bad.append((r, "arm claim does not hold the changed words"))
        else:
            ok.append((b, a, r))
    return ok, bad


def without_negated_copy(base, rows):
    """Ids of the baseline (r1) claims judged supported that no variant row
    negates (no row with that id and a "md_negated"). The negated copies were
    fixed before any run of the measured version, from an earlier run, so
    such a claim is never tested by the negated arm. A supported verdict never
    comes from a failed call (failed_claim), so none is left out."""
    negated = {r["id"] for r in rows if r.get("md_negated")}
    return [c.get("id") for c in base.get("text_claims", [])
            if c.get("verdict") == "supported" and c.get("id") not in negated]


def failed_claim(c):
    """True when this claim's verdict came from a failed model call. The
    checker stores a claim whose judge calls all failed as "unsupported", so
    only an unsupported claim can carry a failed-call verdict: a supported
    verdict always rests on a real positive answer, even when some other call
    on the same claim failed, and it is kept."""
    if c.get("verdict") != "unsupported":
        return False
    return bool(c.get("judge_error")) or str(c.get("reason", "")).startswith(
        ("no LLM response", "LLM judgment unparseable"))


def _load_key(text):
    kind, key = KEYS.get(text, (None, None))
    if not key or not os.path.isfile(os.path.join(REPO, key)):
        return None, None
    with open(os.path.join(REPO, key), encoding="utf-8") as f:
        return kind, json.load(f)


def accuracy(analysis, text, only=None):
    """(correct, hard rows, rows left out) on that text's answer key, or None
    without a key. A hard row whose claim's verdict came from a failed call is
    left out of both counts and returned as the third number. With `only` (a
    set of normalised claim texts), every row whose claim in THIS run is not
    in the set is dropped first (used to score a judge on the claims where two
    runs disagree)."""
    kind, gt = _load_key(text)
    if gt is None:
        return None
    claims = analysis.get("text_claims", [])
    if kind == "regression":
        by_id = {c.get("id"): c for c in claims}
        keep, left = [], 0
        for entry in gt["claims"]:
            c, _ = regression_check.find_claim(entry, by_id, claims)
            if only is not None and (c is None or norm(c.get("text")) not in only):
                continue
            if c is not None and entry["expect"] != "watch" and failed_claim(c):
                left += 1
            else:
                keep.append(entry)
        rep = regression_check.score(analysis, dict(gt, claims=keep))
        n = rep["passes"] + len(rep["failures"]) + len(rep["accepted_red"]) + len(rep["missing"])
        return rep["passes"], n, left
    rows = gt.get("claims", [])
    if only is not None:
        by_id = {c.get("id"): c for c in claims}
        rows = [r for r in rows if r.get("id") in by_id and norm(by_id[r["id"]].get("text")) in only]
    failed_ids = {c.get("id") for c in claims if failed_claim(c)}
    keep = [r for r in rows if r.get("kind") == "watch" or r.get("id") not in failed_ids]
    left = len(rows) - len(keep)
    fails, accepted, _, n_hard = coverage_check.check_split(analysis, dict(gt, claims=keep))
    return n_hard - len(fails) - len(accepted), n_hard, left


def claim_correct(analysis, text, claim_text):
    """True / False when the claim with this (normalised) text has a hand-
    checked row in this run and its verdict did not come from a failed call;
    None otherwise."""
    acc = accuracy(analysis, text, only={claim_text})
    if not acc or not acc[1]:
        return None
    return acc[0] == acc[1]


def index_by_text(analysis):
    """{normalised text: claim}, claims whose text occurs twice left out."""
    out, dup = {}, set()
    for c in (analysis or {}).get("text_claims", []):
        k = norm(c.get("text"))
        if k in out:
            dup.add(k)
        out[k] = c
    for k in dup:
        out.pop(k, None)
    return out


def usable_verdict(c):
    """The claim's verdict, or None when it is missing or came from a failed call."""
    if c is None or failed_claim(c):
        return None
    return c.get("verdict")


def calls_by_claim(root, tag, text):
    """({seller: number of calls}, {claim id: {seller: number of calls}}) from
    the run's call log. The claim of a call is the "claim_id" the study's runner
    fills in (install_claim_attribution); a call with no seller recorded counts
    under "not recorded"."""
    p = os.path.join(run_dir(root, tag, text), "llm_calls.jsonl")
    total, per = {}, {}
    if not os.path.isfile(p):
        return total, per
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("purpose") == "sanity_check":
                continue
            sellers = rec.get("served_by") or "not recorded"
            sellers = sellers if isinstance(sellers, list) else [sellers]
            for s in sellers:
                total[s] = total.get(s, 0) + 1
                cid = rec.get("claim_id")
                if cid:
                    d = per.setdefault(cid, {})
                    d[s] = d.get(s, 0) + 1
    return total, per


def cli_versions(root, tag, text):
    """Every Claude program recorded by the runner for one text of one arm."""
    p = os.path.join(run_dir(root, tag, text), ".card160_runs.jsonl")
    out = set()
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    v = json.loads(line).get("claude_cli_version")
                except ValueError:
                    continue
                if v:
                    out.add(v)
    return out


def start_times(root, tag, text):
    """Every start time the runner recorded for one text of one arm."""
    p = os.path.join(run_dir(root, tag, text), ".card160_runs.jsonl")
    out = []
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    at = json.loads(line).get("at")
                except ValueError:
                    continue
                if at:
                    out.append(at)
    return out


def multi_source(c):
    """True when the claim cites two or more different sources."""
    return len(set(c.get("markers") or [])) >= 2


def repeat_label(base_claim, repeat_runs):
    """How the unchanged repeats (r2, r3) judged this baseline (r1) claim:
    "moved_by_arm" when both repeats exist and agree with r1 (so an arm that
    differs from r1 differs from all three), "also_changed_in_repeats" when
    either repeat differs from r1, "repeats_unknown" otherwise (a repeat run
    or claim missing, or its verdict from a failed call)."""
    vb = usable_verdict(base_claim)
    k = norm(base_claim.get("text"))
    seen = []
    for idx in repeat_runs:
        v = usable_verdict(idx.get(k)) if idx is not None else None
        if v is not None and v != vb:
            return "also_changed_in_repeats"
        seen.append(v)
    if len(seen) >= 2 and all(v == vb for v in seen):
        return "moved_by_arm"
    return "repeats_unknown"


def score_arm(arm, root, texts, variants):
    res = {"name": arm["name"], "kind": arm["kind"], "run": arm["run"],
           "baseline": arm["baseline"], "role": arm.get("role", ""),
           "compared": 0, "changed": 0,
           "green_to_red": 0, "red_to_green": 0, "other": 0,
           "baseline_supported": 0, "baseline_unsupported": 0,
           "texts_missing": [], "not_clean": [], "unpaired": [], "changes": [],
           "correct": 0, "hard_rows": 0, "left_out_failed": 0, "left_out": [],
           "accuracy_left_out": 0, "wrong_version": [], "dropped_duplicates": 0,
           "changed_checked": 0, "changed_correct_baseline": 0, "changed_correct_arm": 0,
           "sellers": {}, "cli_differs": [], "retry_ended": []}
    repeats = arm.get("repeats") or []
    res["repeat_tags"] = list(repeats)
    if repeats:
        res.update({"moved_by_arm": 0, "also_changed_in_repeats": 0, "repeats_unknown": 0})
    if arm["kind"] == "negated":
        res.update({"negated_rows": 0, "negation_caught": 0,
                    "negated_rows_single_source": 0, "negation_caught_single_source": 0,
                    "negated_rows_stable": 0, "negation_caught_stable": 0,
                    "supported_without_negated_copy": 0, "without_negated_copy": []})
    for text in texts:
        base, run = load(root, arm["baseline"], text), load(root, arm["run"], text)
        if base is None or run is None:
            res["texts_missing"].append(text)
            continue
        wrong = [f"{text} ({label}: {w})" for label, tag in (("baseline", arm["baseline"]),
                                                             ("arm", arm["run"]))
                 for w in [wrong_version(root, tag, text)] if w]
        if wrong:
            res["wrong_version"] += wrong
            continue
        # A text whose runner stopped retrying with claims still failed (the
        # 24-hour retry limit, a stop, or repeated errors) is named.
        for label, tag in (("baseline", arm["baseline"]), ("arm", arm["run"])):
            rp = os.path.join(run_dir(root, tag, text), RETRY_REPORT)
            if (label == "arm" or arm["kind"] != "baseline") and os.path.isfile(rp):
                try:
                    with open(rp, encoding="utf-8") as f:
                        rep = json.load(f)
                except ValueError:
                    rep = {}
                ids = ", ".join(str(c.get("id")) for c in rep.get("claims") or [])
                entry = f"{text} ({label}): {rep.get('reason', 'unreadable report')}; still failed: {ids}"
                if entry not in res["retry_ended"]:
                    res["retry_ended"].append(entry)
        for label, a in (("baseline", base), ("arm", run)):
            if regression_check.contamination(a):
                res["not_clean"].append(f"{text} ({label})")
                if arm["kind"] == "baseline":
                    break
        # Which sellers answered the arm's calls (paid Gemma records one per
        # call), and which calls belonged to which claim.
        seller_total, seller_per_claim = calls_by_claim(root, arm["run"], text)
        for s, n in seller_total.items():
            res["sellers"][s] = res["sellers"].get(s, 0) + n
        base_sellers = calls_by_claim(root, arm["baseline"], text)[1]
        # A Claude arm whose Claude program differs from its baseline's is
        # reported apart (the runner refuses it unless told otherwise).
        cv_run, cv_base = cli_versions(root, arm["run"], text), cli_versions(root, arm["baseline"], text)
        if cv_run and cv_base and cv_run != cv_base:
            res["cli_differs"].append(f"{text} (this run: {'; '.join(sorted(cv_run))}; "
                                      f"baseline: {'; '.join(sorted(cv_base))})")
        # A repeat whose run stamp is wrong (another commit, an edited copy,
        # another arm, no stamp) tells nothing about this claim: it is read as
        # missing, so its claims count as "repeats unknown".
        rep_idx = [index_by_text(r) if r is not None and not wrong_version(root, t, text) else None
                   for t, r in ((t, load(root, t, text)) for t in repeats)]
        if arm["kind"] in ("same", "baseline"):
            acc = accuracy(run, text)
            if acc:
                res["correct"] += acc[0]
                res["hard_rows"] += acc[1]
                res["accuracy_left_out"] += acc[2]
            if arm["kind"] == "baseline":
                continue
            pairs = [(b, a, None) for b, a in pairs_same(base, run)]
            res["dropped_duplicates"] += max(duplicate_texts(base), duplicate_texts(run))
        else:
            field = "md_reworded" if arm["kind"] == "reworded" else "md_negated"
            rows = [r for r in variants if r["text_name"] == text]
            pairs, bad = pairs_variant(base, run, rows, field)
            res["unpaired"] += [f"{text}/{r['id']}: {why}" for r, why in bad]
            if arm["kind"] == "negated":
                ids = without_negated_copy(base, rows)
                res["supported_without_negated_copy"] += len(ids)
                res["without_negated_copy"] += [f"{text}/{i}" for i in ids]
        for b, a, row in pairs:
            vb, va = b.get("verdict"), a.get("verdict")
            if failed_claim(b) or failed_claim(a):
                which = b if failed_claim(b) else a
                res["left_out_failed"] += 1
                res["left_out"].append(f"{text}/{a.get('id')}: "
                                       + ("baseline" if failed_claim(b) else "arm")
                                       + " verdict came from a failed call ("
                                       + (str(which.get("reason") or "")
                                          or "judge_error") + ")")
                continue
            sellers = {"sellers_arm": seller_per_claim.get(a.get("id"), {}),
                       "sellers_baseline": base_sellers.get(b.get("id"), {})}
            if arm["kind"] == "negated":
                if vb != "supported":
                    continue
                caught = va == "unsupported"
                single = not multi_source(b)
                stable = bool(repeats) and repeat_label(b, rep_idx) == "moved_by_arm"
                res["negated_rows"] += 1
                res["negation_caught"] += caught
                res["negated_rows_single_source"] += single
                res["negation_caught_single_source"] += caught and single
                res["negated_rows_stable"] += stable
                res["negation_caught_stable"] += caught and stable
                if not caught:
                    res["changes"].append(dict({"text": text, "id": a.get("id"),
                                                "baseline": vb, "arm": va,
                                                "claim": a.get("text") or "",
                                                "what": "negation missed",
                                                "cites_several_sources": not single,
                                                "supported_in_all_three_unchanged_runs": stable},
                                               **sellers))
                continue
            if vb not in JUDGED and va not in JUDGED:
                continue
            res["compared"] += 1
            res["baseline_supported"] += vb == "supported"
            res["baseline_unsupported"] += vb == "unsupported"
            d = direction(vb, va)
            if d:
                res["changed"] += 1
                res[d] += 1
                change = dict({"text": text, "id": a.get("id"), "baseline": vb,
                               "arm": va, "direction": d,
                               "claim": a.get("text") or ""}, **sellers)
                if repeats:
                    lab = repeat_label(b, rep_idx)
                    res[lab] += 1
                    change["repeats"] = lab
                if arm["kind"] == "same":
                    # On a changed claim that has a hand-checked row: which of
                    # the two runs got it right.
                    cb = claim_correct(base, text, norm(b.get("text")))
                    ca = claim_correct(run, text, norm(a.get("text")))
                    if cb is not None and ca is not None:
                        res["changed_checked"] += 1
                        res["changed_correct_baseline"] += cb
                        res["changed_correct_arm"] += ca
                        change["correct_baseline"], change["correct_arm"] = cb, ca
                res["changes"].append(change)
    return res


def _majority(verdicts):
    """The verdict at least two of the three runs gave, else None."""
    vs = [v for v in verdicts if v is not None]
    for v in set(vs):
        if vs.count(v) >= 2:
            return v
    return None


def score_judges(arm, root, texts):
    """Two judges, three unchanged runs each ("runs" and "baseline_runs"),
    claim by claim. A claim is paired across all six runs by its text.

    stable:   only claims where all three runs of EACH judge agree (and none
              came from a failed call); a disagreement there is between two
              judges that each repeat themselves, not randomness.
    majority: the verdict at least two of a judge's three runs gave, against
              the other judge's; a claim with no majority on either side is
              counted under "no majority".
    On every disagreeing claim with a hand-checked row, each judge counts as
    right when at least two of its three runs got that row right.
    Also the first and last start time of each judge's runs (the time gap)."""
    res = {"name": arm["name"], "kind": "judges", "run": arm["run"], "baseline": arm["baseline"],
           "runs": arm["runs"], "baseline_runs": arm["baseline_runs"],
           "texts_missing": [], "wrong_version": [], "claims": 0, "left_out_failed": 0,
           "stable": {"compared": 0, "changed": 0, "green_to_red": 0, "red_to_green": 0,
                      "other": 0, "baseline_supported": 0, "baseline_unsupported": 0,
                      "checked": 0, "correct_baseline": 0, "correct_arm": 0},
           "majority": {"compared": 0, "changed": 0, "green_to_red": 0, "red_to_green": 0,
                        "other": 0, "baseline_supported": 0, "baseline_unsupported": 0,
                        "no_majority": 0, "checked": 0, "correct_baseline": 0, "correct_arm": 0},
           "changes": [], "starts": {}, "cli_differs": [],
           # Changed verdicts of each judge's own repeat pairs (r2-r1, r3-r1,
           # r3-r2) on the claims this comparison pairs, so the disagreement is
           # read next to each judge's own randomness.
           "repeat_changes": {"arm": [0, 0, 0], "baseline": [0, 0, 0]}}
    tags = list(arm["runs"]) + list(arm["baseline_runs"])
    for t in tags:
        res["starts"][t] = []
    for text in texts:
        loaded = {t: load(root, t, text) for t in tags}
        if any(v is None for v in loaded.values()):
            res["texts_missing"].append(text)
            continue
        wrong = [f"{text} ({t}: {w})" for t in tags for w in [wrong_version(root, t, text)] if w]
        if wrong:
            res["wrong_version"] += wrong
            continue
        for t in tags:
            res["starts"][t] += start_times(root, t, text)
        # Each judge's three runs must have used one Claude program; a run on
        # another version is reported apart (the runner refuses it unless told
        # otherwise). Gemma runs record no Claude program and are never named.
        for side in ("runs", "baseline_runs"):
            seen = {t: cli_versions(root, t, text) for t in arm[side]}
            if len({frozenset(v) for v in seen.values() if v}) > 1:
                res["cli_differs"].append(
                    f"{text} (" + "; ".join(f"{t}: {', '.join(sorted(v)) or 'not recorded'}"
                                            for t, v in seen.items()) + ")")
        idx = {t: index_by_text(a) for t, a in loaded.items()}
        keys = [k for k in idx[arm["baseline_runs"][0]] if all(k in idx[t] for t in tags)]
        for k in keys:
            vb_all = [usable_verdict(idx[t][k]) for t in arm["baseline_runs"]]
            va_all = [usable_verdict(idx[t][k]) for t in arm["runs"]]
            raw = [idx[t][k].get("verdict") for t in tags]
            if not any(v in JUDGED for v in raw):
                continue
            res["claims"] += 1
            if None in vb_all or None in va_all:
                res["left_out_failed"] += 1
            for side, vs in (("arm", va_all), ("baseline", vb_all)):
                for i, (x, y) in enumerate(((1, 0), (2, 0), (2, 1))):
                    if vs[x] is not None and vs[y] is not None and vs[x] != vs[y]:
                        res["repeat_changes"][side][i] += 1
            right_b = [claim_correct(loaded[t], text, k) for t in arm["baseline_runs"]]
            right_a = [claim_correct(loaded[t], text, k) for t in arm["runs"]]
            checked = all(r is not None for r in right_b + right_a)
            cb, ca = sum(bool(r) for r in right_b) >= 2, sum(bool(r) for r in right_a) >= 2
            for name, vb, va, ok in (
                    ("stable", vb_all[0], va_all[0],
                     None not in vb_all + va_all and len(set(vb_all)) == 1 and len(set(va_all)) == 1),
                    ("majority", _majority(vb_all), _majority(va_all), True)):
                part = res[name]
                if not ok:
                    continue
                if vb is None or va is None:
                    if name == "majority":
                        part["no_majority"] += 1
                    continue
                part["compared"] += 1
                part["baseline_supported"] += vb == "supported"
                part["baseline_unsupported"] += vb == "unsupported"
                d = direction(vb, va)
                if not d:
                    continue
                part["changed"] += 1
                part[d] += 1
                change = {"text": text, "id": idx[arm["runs"][0]][k].get("id"), "which": name,
                          "baseline": vb, "arm": va, "direction": d,
                          "claim": idx[arm["runs"][0]][k].get("text") or ""}
                if checked:
                    part["checked"] += 1
                    part["correct_baseline"] += cb
                    part["correct_arm"] += ca
                    change["correct_baseline"], change["correct_arm"] = cb, ca
                res["changes"].append(change)
    return res


def annotate_repeat_range(results):
    """For every one-run arm (it names its repeats): the smallest and largest
    number of changed verdicts among the repeat pairs of the same judge (r2-r1,
    r3-r1, r3-r2), and the pre-registered reading: a count at or below the
    largest is "within the range of the repeats", above it "above the range of
    the repeats". No significance test is made for any comparison."""
    reps = [r for r in results if r.get("role") == "repeat"]
    for r in results:
        tags = r.get("repeat_tags") or []
        if not tags:
            continue
        group = {r["baseline"], *tags}
        counts = [x["changed"] for x in reps if x["run"] in group and x["baseline"] in group
                  and not x["texts_missing"] and not x["wrong_version"]]
        if len(counts) < 3:
            r["repeat_range"] = None
            continue
        r["repeat_range"] = [min(counts), max(counts)]
        if r["kind"] != "negated":
            r["reading"] = ("within the range of the repeats" if r["changed"] <= max(counts)
                            else "above the range of the repeats")


def score_all(arms_spec, root, texts, variants):
    """(results over the six texts, results over the hard rows). The hard rows
    are constructed test sentences with their own key, so they never enter the
    six texts' changed-verdict counts or accuracy; an arm that did not run
    them (the reworded and negated arms) has no hard-rows line."""
    main_texts = tuple(t for t in texts if t != "hard")

    def one(a, ts):
        return score_judges(a, root, ts) if a["kind"] == "judges" else score_arm(a, root, ts, variants)
    results = [one(a, main_texts) for a in arms_spec]
    hard = []
    if "hard" in texts:
        for a in arms_spec:
            if a["kind"] in ("same", "baseline", "judges"):
                hard.append(one(a, ("hard",)))
    annotate_repeat_range(results)
    annotate_repeat_range(hard)
    return results, hard


def pct(n, d):
    return f"{100.0 * n / d:.1f}%" if d else "-"


def _count(n, d, percent):
    """"n (x%)" for the six texts; "n of d" for the ten hard sentences, where
    one changed verdict is already 10 percentage points."""
    return f"{n} ({pct(n, d)})" if percent else f"{n} of {d}"


def _sellers(d, shares=False):
    """"Novita 3, Venice 1"; with shares=True "Novita 3 (75.0%), Venice 1 (25.0%)"."""
    total = sum(d.values())
    return ", ".join(f"{s} {n}" + (f" ({pct(n, total)})" if shares else "")
                     for s, n in sorted(d.items(), key=lambda kv: -kv[1])) or "none recorded"


def markdown_judges(results, percent=True):
    """One line per judge comparison and kind of reading (stable / majority)."""
    lines = ["| Comparison | Reading | Claims compared | Disagree | Green turned red | Red turned green | Other | Disagreeing claims with a checked row: right (this judge / paid Gemma) | Notes |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        notes = []
        if r["texts_missing"]:
            notes.append("missing: " + ", ".join(r["texts_missing"]))
        if r["wrong_version"]:
            notes.append("left out, wrong version: " + ", ".join(r["wrong_version"]))
        if r.get("cli_differs"):
            notes.append("REPORTED APART: the Claude program differs between one judge's three runs for "
                         + ", ".join(r["cli_differs"]))
        if r["left_out_failed"]:
            notes.append(f"{r['left_out_failed']} claims had a failed call in at least one of the six runs")
        if r["claims"]:
            ra, rb = r["repeat_changes"]["arm"], r["repeat_changes"]["baseline"]
            notes.append(f"own repeats (r2-r1, r3-r1, r3-r2) changed: this judge {ra[0]}, {ra[1]}, {ra[2]}; "
                         f"paid Gemma {rb[0]}, {rb[1]}, {rb[2]}")
        span = {t: (min(v), max(v)) for t, v in r["starts"].items() if v}
        if span:
            a = [span[t] for t in r["runs"] if t in span]
            b = [span[t] for t in r["baseline_runs"] if t in span]
            if a and b:
                notes.append(f"this judge's runs started {min(x[0] for x in a)} to {max(x[1] for x in a)}; "
                             f"paid Gemma's {min(x[0] for x in b)} to {max(x[1] for x in b)}")
        for name, label in (("stable", "only claims all three runs of each judge agree on"),
                            ("majority", "majority verdict against majority verdict")):
            p = r[name]
            extra = list(notes)
            if name == "majority" and p["no_majority"]:
                extra.append(f"{p['no_majority']} claims without a majority")
            right = (f"{p['correct_arm']} / {p['correct_baseline']} of {p['checked']}"
                     if p["checked"] else "-")
            lines.append(f"| {r['name']} | {label} | {p['compared']} | {_count(p['changed'], p['compared'], percent)} | "
                         f"{p['green_to_red']} of {p['baseline_supported']} | "
                         f"{p['red_to_green']} of {p['baseline_unsupported']} | {p['other']} | {right} | "
                         f"{'; '.join(extra)} |")
    return "\n".join(lines) + "\n"


def markdown(results, percent=True):
    judges = [r for r in results if r["kind"] == "judges"]
    results = [r for r in results if r["kind"] != "judges"]
    lines = ["| Arm | Claims compared | Changed | Green turned red (of the claims supported in the baseline) | Red turned green (of the claims unsupported in the baseline) | Other | Left out (failed call) | Accuracy on checked rows | Notes |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        notes = []
        if r.get("repeat_tags"):
            if r.get("repeat_range"):
                lo, hi = r["repeat_range"]
                notes.append(f"repeats of this judge changed {lo} to {hi}"
                             + (f"; {r['reading']}" if r.get("reading") else ""))
            else:
                notes.append("repeat range not available (a repeat pair is missing)")
            if r["kind"] != "negated":
                notes.append(f"of the changed: {r['moved_by_arm']} moved by the arm (r1, r2 and r3 "
                             f"agree), {r['also_changed_in_repeats']} also changed in a repeat, "
                             f"{r['repeats_unknown']} repeats unknown")
        if r.get("changed_checked"):
            notes.append(f"of the {r['changed_checked']} changed claims with a checked row: "
                         f"right in the arm {r['changed_correct_arm']}, in the baseline "
                         f"{r['changed_correct_baseline']}")
        if r.get("cli_differs"):
            notes.append("REPORTED APART: the Claude program differs from the baseline's for "
                         + ", ".join(r["cli_differs"]))
        if r.get("sellers") and set(r["sellers"]) != {"not recorded"}:
            notes.append("calls per seller (share of the arm's calls): " + _sellers(r["sellers"], shares=True))
        if r["texts_missing"]:
            notes.append("missing: " + ", ".join(r["texts_missing"]))
        if r.get("wrong_version"):
            notes.append("left out, wrong version: " + ", ".join(r["wrong_version"]))
        if r.get("dropped_duplicates"):
            notes.append(f"{r['dropped_duplicates']} claims with a duplicate text not paired")
        if r["not_clean"]:
            notes.append("NOT CLEAN (failed calls): " + ", ".join(r["not_clean"]))
        if r.get("retry_ended"):
            notes.append("retrying ended with claims still failed: " + " | ".join(r["retry_ended"]))
        if r["unpaired"]:
            notes.append(f"{len(r['unpaired'])} variant rows could not be paired")
        if r.get("accuracy_left_out"):
            notes.append(f"{r['accuracy_left_out']} checked rows left out of the accuracy (failed call)")
        left = r.get("left_out_failed", 0)
        if r["kind"] == "negated":
            caught = (f"negation caught {_count(r['negation_caught'], r['negated_rows'], percent)}"
                      f" of {r['negated_rows']}; without the claims citing several sources "
                      f"{r['negation_caught_single_source']} of {r['negated_rows_single_source']}; "
                      f"on the claims supported in all three unchanged runs "
                      f"{r['negation_caught_stable']} of {r['negated_rows_stable']}")
            notes.append(f"{r['supported_without_negated_copy']} claims supported in the baseline "
                         "have no negated copy (not tested)"
                         + (": " + ", ".join(r["without_negated_copy"])
                            if r["without_negated_copy"] else ""))
            lines.append(f"| {r['name']} | {r['negated_rows']} negated | "
                         f"{r['negated_rows'] - r['negation_caught']} stayed green | - | - | - | {left} | "
                         f"{caught} | {'; '.join(notes)} |")
            continue
        acc = (f"{r['correct']} of {r['hard_rows']}"
               + (f" ({pct(r['correct'], r['hard_rows'])})" if percent else "")
               if r["hard_rows"] else "-")
        if r["kind"] == "baseline":
            lines.append(f"| {r['name']} | - | - | - | - | - | - | {acc} | {'; '.join(notes)} |")
            continue
        lines.append(f"| {r['name']} | {r['compared']} | {_count(r['changed'], r['compared'], percent)} | "
                     f"{r['green_to_red']} of {r['baseline_supported']} | "
                     f"{r['red_to_green']} of {r['baseline_unsupported']} | {r['other']} | {left} | "
                     f"{acc} | {'; '.join(notes)} |")
    out = "\n".join(lines) + "\n"
    if judges:
        out += ("\nThe second judges against paid Gemma, all three runs of each judge, claim by "
                "claim:\n\n" + markdown_judges(judges, percent))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("arms")
    ap.add_argument("--root", default=os.path.join(REPO, "data"))
    ap.add_argument("--variants", default=VARIANTS)
    ap.add_argument("--out")
    ap.add_argument("--markdown")
    args = ap.parse_args(argv)
    with open(args.arms, encoding="utf-8") as f:
        spec = json.load(f)
    texts = tuple(spec.get("texts") or DEFAULT_TEXTS)
    variants = load_variants(args.variants)
    results, hard = score_all(spec["arms"], args.root, texts, variants)
    table = markdown(results)
    if hard:
        table += ("\nThe ten constructed hard sentences, counted apart from the six texts "
                  "(their expected verdicts are hard_rows_ground_truth.json). Counts, not "
                  "percentages: with ten sentences, one changed verdict is already 10 "
                  "percentage points.\n\n" + markdown(hard, percent=False))
    print(table)
    for r in results + hard:
        for c in r["changes"]:
            extra = []
            if c.get("which"):
                extra.append(c["which"])
            if c.get("repeats"):
                extra.append(c["repeats"])
            for side in ("arm", "baseline"):
                if c.get(f"sellers_{side}"):
                    extra.append(f"sellers in the {side}: {_sellers(c[f'sellers_{side}'])}")
            print(f"  {r['name']}: {c['text']}/{c['id']} {c['baseline']} -> {c['arm']}"
                  f"  {c.get('direction') or c.get('what')}"
                  + (f"  [{'; '.join(extra)}]" if extra else "") + f"  {c['claim']}")
        for u in r.get("unpaired", []):
            print(f"  {r['name']}: unpaired {u}")
        for u in r.get("left_out", []):
            print(f"  {r['name']}: left out {u}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"generated_by": "benchmarks/card160/stability_table.py",
                       "arms": results, "hard_rows": hard}, f, ensure_ascii=False, indent=1)
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
