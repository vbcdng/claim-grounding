#!/usr/bin/env python3
"""Synthetic test-document generator — self-scoring regression material for the
owner-ruled failure classes (night-loop aid, 2026-07-11).

Builds a verifier project whose ground truth is decided at generation time:
fictional entities (towns, festivals, bridges — deliberately no health/bio/
chem/IT content) slotted into templates that engineer one failure class each:

  expect_full        every component's proof sentence IS in the cited source
  expect_partial     one named component is deliberately absent -> amber must
                     name it (t9/t12-class)
  expect_unsupported the source contradicts the claim's core number/fact
  expect_overcite    two sources cited, second adds nothing -> over_citation
  watch_decontext    source sentence matches locally but context negates it
                     (t6-class, #79 — a KNOWN gap: tracked, never failed)

Entity names/numbers are drawn from a seeded RNG, so each batch is textually
fresh (no cache reuse, no judge memorization) while ground truth stays exact.

Usage:
  python3 benchmarks/synth_docs.py --seed 11 --output-dir data/synth/batch_11
  python3 benchmarks/synth_docs.py score --analysis <run>/analysis.json \
      --ground-truth <batch>/synth_ground_truth.json

No LLM, no network.
"""

import argparse
import json
import os
import random
import sys

TOWNS = ["Velden", "Marbury", "Ostrova", "Quilby", "Ferrandale", "Lunow",
         "Brackwell", "Tarnitz", "Solvik", "Grenholm"]
RIVERS = ["Aldra", "Vesna", "Korrin", "Melby", "Sorne", "Tavra"]
FESTIVALS = ["Lantern Fair", "Harvest Regatta", "Winter Bridge Feast",
             "Stonemasons' Parade", "Bell Festival"]
ARCHITECTS = ["Elias Vorn", "Greta Salomon", "Tomas Reike", "Ilse Brandt",
              "Viktor Halm", "Annelie Kron"]


def _norm(s):
    return " ".join(s.lower().split())


def build(seed, output_dir):
    rng = random.Random(seed)
    town, town2 = rng.sample(TOWNS, 2)
    river = rng.choice(RIVERS)
    fest = rng.choice(FESTIVALS)
    arch, arch2 = rng.sample(ARCHITECTS, 2)
    yr = rng.randint(1751, 1912)
    yr_wrong = yr + rng.randint(3, 40)
    span = rng.randint(88, 340)
    visitors = rng.choice([12, 18, 25, 40, 60]) * 1000

    claims, sources, gt = [], {}, {}

    # 1. expect_full — every component provable, proofs are separate sentences
    sources["bridgehist"] = (
        f"The stone bridge at {town} was completed in {yr}. "
        f"It was designed by the engineer {arch}. "
        f"At {span} metres, it was the longest span on the {river} river at the time of its opening. "
        f"The bridge replaced a wooden ferry crossing that had operated for over a century."
    )
    claims.append((f"The {town} bridge, designed by {arch} and completed in {yr}, "
                   f"was the longest span on the {river} at its opening.", ["bridgehist"]))
    gt["bridgehist"] = {"kind": "expect_full",
                        "anchors": [_norm(f"designed by the engineer {arch}"),
                                    _norm(f"completed in {yr}")]}

    # 2. expect_partial — the "financed by public subscription" component is
    # in NO source sentence; everything else is provable
    sources["festhist"] = (
        f"The {fest} in {town2} was first held in {yr}. "
        f"The festival takes place each year on the banks of the {river}. "
        f"In recent years it has drawn around {visitors:,} visitors annually. "
        f"Local guilds organise the opening procession."
    )
    claims.append((f"The {fest}, first held in {yr} and held under the patronage of the "
                   f"town council, draws around {visitors:,} visitors each year.", ["festhist"]))
    gt["festhist"] = {"kind": "expect_partial",
                      "missing_terms": ["patronage", "town council"]}

    # 3. expect_unsupported — the source states a different year outright
    sources["townrec"] = (
        f"Municipal records show that {town} received its town charter in {yr_wrong}. "
        f"The charter was granted by the provincial assembly. "
        f"A copy of the charter is kept in the {town} archive."
    )
    claims.append((f"{town} received its town charter in {yr}.", ["townrec"]))
    gt["townrec"] = {"kind": "expect_unsupported"}

    # 4. expect_overcite — src A proves everything; src B is about something else
    sources["archbio"] = (
        f"{arch2} was born in {town2}. "
        f"{arch2} designed the {town2} concert hall, completed in {yr_wrong}. "
        f"The concert hall seats 1,200 people."
    )
    sources["riverguide"] = (
        f"The {river} rises in the northern hills and flows for 240 kilometres. "
        f"Barge traffic on the {river} peaked in the nineteenth century."
    )
    claims.append((f"{arch2}, born in {town2}, designed the {town2} concert hall.",
                   ["archbio", "riverguide"]))
    gt["archbio"] = {"kind": "expect_overcite", "redundant_source": "riverguide"}

    # 5. watch_decontext — locally-matching sentence, context negates it (t6/#79)
    sources["prizerec"] = (
        f"Early newspaper reports stated that {arch} won the provincial design prize of {yr}. "
        f"The prize committee's own minutes, published a month later, show the award "
        f"in fact went to {arch2}, and the earlier reports were retracted."
    )
    claims.append((f"{arch} won the provincial design prize of {yr}.", ["prizerec"]))
    gt["prizerec"] = {"kind": "watch_decontext"}

    os.makedirs(os.path.join(output_dir, "sources"), exist_ok=True)
    for key, text in sources.items():
        with open(os.path.join(output_dir, "sources", f"{key}.txt"), "w", encoding="utf-8") as f:
            f.write(text)
    paras = [f"{c} {' '.join(f'[[{k}]]' for k in keys)}" for c, keys in claims]
    with open(os.path.join(output_dir, "my_text.md"), "w", encoding="utf-8") as f:
        f.write(f"# Synthetic check batch (seed {seed})\n\n" + "\n\n".join(paras) + "\n")
    with open(os.path.join(output_dir, "my_text.md.refs.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(f"{k} = {k}.txt" for k in sources) + "\n")
    with open(os.path.join(output_dir, "synth_ground_truth.json"), "w", encoding="utf-8") as f:
        json.dump({"seed": seed, "claims": gt}, f, indent=1)
    print(f"wrote {len(claims)} claims / {len(sources)} sources -> {output_dir}")
    print(f"run: venv/bin/python3 verify_my_text.py --text {output_dir}/my_text.md "
          f"--sources {output_dir}/sources --references {output_dir}/my_text.md.refs.txt "
          f"--output-dir {output_dir}_run --yes")


def score(analysis_path, gt_path):
    analysis = json.load(open(analysis_path, encoding="utf-8"))
    gt = json.load(open(gt_path, encoding="utf-8"))["claims"]
    claims = analysis.get("text_claims", [])
    by_key = {}
    for c in claims:
        for key in c.get("markers") or []:
            by_key.setdefault(key, c)

    failures, watches = [], []
    for key, g in gt.items():
        c = by_key.get(key)
        kind = g["kind"]
        if c is None:
            failures.append(f"{key}: no claim found for this source")
            continue
        verdict = c.get("verdict")
        cov = c.get("covering") or {}
        uncovered = " ".join(_norm(u if isinstance(u, str) else json.dumps(u))
                             for u in cov.get("uncovered", []))
        if kind == "expect_full":
            covered = " ".join(_norm(r.get("sentence", "")) for r in cov.get("covered", []))
            if verdict != "supported":
                failures.append(f"{key}: expect_full but verdict={verdict}")
            elif cov.get("uncovered"):
                failures.append(f"{key}: expect_full but amber fired: {uncovered[:120]}")
            elif not all(a in covered for a in g.get("anchors", [])):
                failures.append(f"{key}: expect_full but an anchor proof is not shown")
        elif kind == "expect_partial":
            # The engineered gap must be SURFACED somewhere the reader sees it.
            # Production Gemini surfaces it as supported+amber (the t9/t12
            # class); stricter judges (haiku) fail the claim and name the gap
            # in the reason — also honest. Only an invisible gap is a failure.
            terms = [_norm(t) for t in g["missing_terms"]]
            reason = _norm(c.get("reason", ""))
            if verdict == "supported" and any(t in uncovered for t in terms):
                pass  # design behavior: flag path
            elif verdict != "supported" and any(t in reason for t in terms):
                watches.append(f"{key}: gap surfaced via strict verdict (judge-"
                               f"dependent), not the amber flag path")
            elif verdict == "supported":
                failures.append(f"{key}: engineered gap INVISIBLE — supported with no "
                                f"amber naming {g['missing_terms']} (got: {uncovered[:100]})")
            else:
                failures.append(f"{key}: unsupported but reason doesn't name the gap "
                                f"{g['missing_terms']}: {reason[:120]}")
        elif kind == "expect_unsupported":
            if verdict == "supported":
                failures.append(f"{key}: expect_unsupported but verdict=supported (FALSE SUPPORT)")
        elif kind == "expect_overcite":
            if verdict != "supported":
                failures.append(f"{key}: expect_overcite but verdict={verdict}")
            elif not c.get("over_citation"):
                watches.append(f"{key}: over_citation chip did not fire (redundant "
                               f"{g['redundant_source']}) — advisory, tracked")
        elif kind == "watch_decontext":
            status = "PASSED (gap may be closing!)" if verdict != "supported" else \
                     "still fooled (known gap #79)"
            watches.append(f"{key}: decontext watch — verdict={verdict} — {status}")

    for w in watches:
        print(f"WATCH {w}")
    if failures:
        print(f"\n{len(failures)} FAILURE(S):")
        for f_ in failures:
            print(f"  FAIL {f_}")
        return 1
    print(f"\nall {len(gt)} engineered expectations hold "
          f"({len(watches)} watch row(s) above)")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", nargs="?", default="build", choices=["build", "score"])
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--output-dir")
    ap.add_argument("--analysis")
    ap.add_argument("--ground-truth")
    a = ap.parse_args()
    if a.cmd == "build":
        if not a.output_dir:
            sys.exit("--output-dir required for build")
        build(a.seed, a.output_dir)
    else:
        if not (a.analysis and a.ground_truth):
            sys.exit("score needs --analysis and --ground-truth")
        sys.exit(score(a.analysis, a.ground_truth))


if __name__ == "__main__":
    main()
