"""Card 398 (branch B): five-fold check with branch B's own objective.

combine.py --folds picks the grid value with the fewest of one mistake; for an early rejection that
always picks the smallest Jev line (nothing settled cheaply). The plan's rule for branch B is
different: the early rejections are tightened until they wrongly reject at most 1 right piece on the
tuning part, and inside that they should settle as many pieces as they can. So for each fold, on the
other four folds: among grid values whose EARLY rules (--early, rule names) wrongly reject at most
--max-early right pieces, take the one that settles the most pieces early (ties: the smaller value).
An optional "fit2" section fits a second line (the veto against Sonnet) after the first, on the same
four folds: fewest of its "minimize" with at most its "max_undecided" share undecided (ties: fewer
undecided, then grid order). The early count of the first line is read at fit2's first grid value,
so that value must leave the early rules unchanged (the veto acts only on a later rule).
Tuning part only; reads labels through records.load_labels.

Usage: folds.py RULE --out DECISIONS [--early cheap_reject,haiku_reject] [--max-early 1]
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

combine = sibling("combine")
records = sibling("records")
split = sibling("split")
quote_check = sibling("quote_check")

# The grid is decided once per (line, line) pair; quote_in_work normalises the whole work on every
# call, so cache its answer by quote and work list (Chain keeps one list object per piece).
_raw_quote_in_work = quote_check.quote_in_work
_qcache = {}


def _cached_quote_in_work(quote, work_texts):
    key = (quote, id(work_texts))
    if key not in _qcache:
        _qcache[key] = (_raw_quote_in_work(quote, work_texts), work_texts)
    return _qcache[key][0]


quote_check.quote_in_work = _cached_quote_in_work


def early_counts(table, labels, ids, early):
    wr = sum(1 for p in ids if table[p]["step"] in early and table[p]["answer"] == "not_supported"
             and labels[p]["label"] == "supported")
    settled = sum(1 for p in ids if table[p]["step"] in early)
    return wr, settled


def run(rule_path, early, max_early):
    rule = json.loads(Path(rule_path).read_text(encoding="utf-8"))
    chain = combine.Chain(rule, Path(rule_path).parent)
    fit = rule["fit"]
    labels = records.load_labels(final=False)
    fold = split.fold_of_claim()
    ids = [p for p in chain.piece_ids() if p in labels]
    fit2 = rule.get("fit2")  # optional second line (the veto), fitted after the first on the same folds
    grid2 = fit2["grid"] if fit2 else [None]

    def params(v, w):
        return {fit["param"]: v, **({fit2["param"]: w} if fit2 else {})}

    table = {(v, w): {p: chain.decide(p, params(v, w)) for p in ids} for v in fit["grid"] for w in grid2}
    out, chosen = {}, []
    for k in sorted({fold[labels[p]["row_id"]] for p in ids}):
        test = [p for p in ids if fold[labels[p]["row_id"]] == k]
        train = [p for p in ids if fold[labels[p]["row_id"]] != k]
        best = None
        for gi, v in enumerate(fit["grid"]):
            wr, settled = early_counts(table[(v, grid2[0])], labels, train, early)
            if wr > max_early:
                continue
            key = (-settled, gi)
            if best is None or key < best[0]:
                best = (key, v)
        v = best[1] if best else fit["grid"][0]
        w = grid2[0]
        if fit2:  # fewest wrongly rejected with at most max_undecided undecided; ties: fewer undecided
            best2 = None
            for gi, ww in enumerate(grid2):
                bad, und, n = combine.objective(table[(v, ww)], labels, train, fit2.get("minimize", "wrongly_rejected"))
                if n and und / n > fit2.get("max_undecided", 1.0):
                    continue
                key = (bad, und, gi)
                if best2 is None or key < best2[0]:
                    best2 = (key, ww)
            w = best2[1] if best2 else grid2[-1]
        chosen.append((k, (v, w) if fit2 else v))
        for p in test:
            out[p] = dict(table[(v, w)][p], params=params(v, w), fold=k)
    whole = {v: early_counts(table[(v, grid2[0])], labels, ids, early) for v in fit["grid"]}
    return rule, out, chosen, whole


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("rule")
    ap.add_argument("--out", required=True)
    ap.add_argument("--early", default="cheap_reject,haiku_reject")
    ap.add_argument("--max-early", type=int, default=1)
    a = ap.parse_args(argv)
    rule, dec, chosen, whole = run(a.rule, set(a.early.split(",")), a.max_early)
    print("fitted per fold:", ", ".join(f"fold {k}: {v}" for k, v in chosen))
    print("whole tuning part, per grid value: (early wrongly rejected, settled early):",
          ", ".join(f"{v}: {w}" for v, w in whole.items()))
    with open(a.out, "w", encoding="utf-8") as f:
        for p in sorted(dec):
            f.write(json.dumps(dict(dec[p], rule=rule.get("name")), ensure_ascii=False) + "\n")
    counts = {}
    for d in dec.values():
        counts[d["answer"]] = counts.get(d["answer"], 0) + 1
    print(f"{rule.get('name')}: {len(dec)} decisions {dict(sorted(counts.items()))}; {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
