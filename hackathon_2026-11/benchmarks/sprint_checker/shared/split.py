"""Card S1 (card 395): the frozen split of round 1 into a tuning part and a held-back part.

Every claim (row_id) goes, with all its pieces, into one part. Claims are taken in the order
of sha256("sprint:" + row_id) (the method of benchmarks/card381/make_split.py); each claim
goes to the part where the counts stay closest to half: wrong pieces, right pieces, pieces
whose certainty is "possible" or "likely", and wrong pieces in scope. The five folds of the
tuning part are cut the same way (order sha256("sprintfold:" + row_id), five bins).

split.json is made once and never changed: a second run only checks that the file still
matches what this program makes (exit 1 when it does not) and never writes.

The one gate on the held-back part is `allowed_ids(final)`: every loader of labels and
saved answers in this folder asks it, and it gives only the tuning part unless the caller
passes final=True (the --final switch of the programs).

Run: ./venv/bin/python3 benchmarks/sprint_checker/shared/split.py   (makes or checks split.json)
"""
import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
ROUND1 = REPO / "benchmarks" / "card390" / "round1.jsonl"
SPLIT = HERE / "split.json"
PARTS = ("tuning", "held_back")
HEDGED = ("possible", "likely")
N_FOLDS = 5


def order(ids, salt):
    return sorted(ids, key=lambda i: hashlib.sha256((salt + i).encode()).hexdigest())


def read_raw(path=ROUND1):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def claim_vectors(recs):
    """row_id -> (wrong, right, hedged, wrong in scope) piece counts."""
    vec = {}
    for r in recs:
        v = vec.setdefault(r["row_id"], [0, 0, 0, 0])
        wrong = r["label"] == "not_supported"
        v[0] += wrong
        v[1] += not wrong
        v[2] += r["certainty"] in HEDGED
        v[3] += wrong and r["door"] == "in_scope"
    return vec


def balance(claims, vec, n_bins):
    """Each claim, in the given order, into the bin whose share stays closest to 1/n_bins
    of every count; ties go to the lowest bin number. Deterministic."""
    total = [sum(vec[c][k] for c in claims) for k in range(4)]
    bins = [[] for _ in range(n_bins)]
    sums = [[0, 0, 0, 0] for _ in range(n_bins)]
    for c in claims:
        def cost(b):
            s = 0.0
            for i in range(n_bins):
                for k in range(4):
                    x = sums[i][k] + (vec[c][k] if i == b else 0)
                    s += ((x - total[k] / n_bins) / max(total[k], 1)) ** 2
            # a bin that already holds more claims than its share is a weaker choice
            return (round(s, 12), len(bins[b]))
        b = min(range(n_bins), key=lambda i: (cost(i), i))
        bins[b].append(c)
        for k in range(4):
            sums[b][k] += vec[c][k]
    return bins


def make(recs):
    vec = claim_vectors(recs)
    tuning, held = balance(order(vec, "sprint:"), vec, 2)
    folds = balance(order(tuning, "sprintfold:"), vec, N_FOLDS)
    part_of = {c: "tuning" for c in tuning}
    part_of.update({c: "held_back" for c in held})
    out = {"about": "Card S1 (card 395) split of benchmarks/card390/round1.jsonl, made before any "
                    "answer was saved; never changed. See split.py.",
           "salt": "sprint:", "fold_salt": "sprintfold:"}
    for part, claims in (("tuning", tuning), ("held_back", held)):
        cs = sorted(claims)
        out[part] = {"claims": cs,
                     "pieces": sorted(r["piece_id"] for r in recs if part_of[r["row_id"]] == part)}
    out["folds"] = [sorted(f) for f in folds]
    out["counts"] = counts(recs, part_of)
    return out


def counts(recs, part_of):
    res = {}
    for part in PARTS:
        rs = [r for r in recs if part_of[r["row_id"]] == part]
        c = Counter()
        for r in rs:
            lab = "wrong" if r["label"] == "not_supported" else "right"
            c[lab] += 1
            if r["door"] == "in_scope":
                c[lab + "_in_scope"] += 1
            if r["certainty"] in HEDGED:
                c["hedged"] += 1
            if r.get("earlier_sentence"):
                c["earlier_sentence"] += 1
        c["pieces"] = len(rs)
        c["claims"] = len({r["row_id"] for r in rs})
        c["claims_with_wrong"] = len({r["row_id"] for r in rs if r["label"] == "not_supported"})
        c["claims_with_right"] = len({r["row_id"] for r in rs if r["label"] == "supported"})
        c["claims_in_scope"] = len({r["row_id"] for r in rs if r["door"] == "in_scope"})
        res[part] = dict(sorted(c.items()))
    return res


def load_split(path=SPLIT):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def allowed_ids(final=False, path=SPLIT):
    """Piece ids a loader may hand out: the tuning part, or both parts with final=True."""
    s = load_split(path)
    ids = set(s["tuning"]["pieces"])
    if final:
        ids |= set(s["held_back"]["pieces"])
    return ids


def part_of_piece(path=SPLIT):
    s = load_split(path)
    return {p: part for part in PARTS for p in s[part]["pieces"]}


def fold_of_claim(path=SPLIT):
    s = load_split(path)
    return {c: i for i, f in enumerate(s["folds"]) for c in f}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--round1", default=str(ROUND1))
    ap.add_argument("--out", default=str(SPLIT))
    a = ap.parse_args(argv)
    fresh = make(read_raw(a.round1))
    text = json.dumps(fresh, indent=1) + "\n"
    out = Path(a.out)
    if out.exists():
        if out.read_text(encoding="utf-8") != text:
            print(f"refused: {out} exists and differs from what this program makes now; "
                  "the split is never changed")
            return 1
        print(f"{out} unchanged")
    else:
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}")
    for part in PARTS:
        print(part, fresh["counts"][part])
    print("folds (claims):", [len(f) for f in fresh["folds"]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
