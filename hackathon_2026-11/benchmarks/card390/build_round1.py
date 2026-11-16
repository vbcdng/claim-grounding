"""Card 390: the first round of the new verdict benchmark (about 150 right and 150 wrong pieces).

No model call. Inputs: pieces_262.jsonl (merge_cuts.py) and piece_labels.jsonl (merge_labels.py).
- Wrong pieces: pieces with a final label not_supported (decided and checked, card 390).
- Right pieces: pieces of the 134 supported claims (label kept from the claim: the old label proved
  every part of the claim) and pieces of wrong claims whose final label is supported.
- Skipped pieces (the writer's own) and doubtful pieces are never in the round.
Choice: claims in a fixed shuffled order (seed 390), one piece per claim per pass, so the round
spreads over as many claims as possible.
Outputs: round1.jsonl (one record per piece, record format of the vault page "Pieces the AIDA
checker is built for" plus label fields), round1_summary.json, label_sample_20.md (20 freshly
labelled pieces for the author).
Run: ./venv/bin/python3 benchmarks/card390/build_round1.py
"""
import collections
import json
import os
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BENCH = ROOT / "data" / "card191_verdict_bench_9b" / "expected.json"
N_EACH = 150
SEED = 390
SEPARATE = ("planted", "needs_arithmetic", "leans_on_earlier")


def read_jsonl(path):
    return [json.loads(l) for l in open(path) if l.strip()]


def pick(pool, n, seed):
    """One piece per claim per pass, claims and pieces in a fixed shuffled order."""
    rng = random.Random(seed)
    by_claim = collections.defaultdict(list)
    for p in pool:
        by_claim[p["row_id"]].append(p)
    claims = sorted(by_claim)
    rng.shuffle(claims)
    for c in claims:
        rng.shuffle(by_claim[c])
    out = []
    while len(out) < n and any(by_claim.values()):
        for c in claims:
            if by_claim[c] and len(out) < n:
                out.append(by_claim[c].pop())
    return out


def separate_groups(p):
    g = []
    if "planted" in p["tags"]:
        g.append("planted")
    if "needs_arithmetic" in p["tags"]:
        g.append("needs_arithmetic")
    if "no_standalone_sentence" in p["tags"] or "leans_filled" in p["tags"]:
        g.append("leans_on_earlier")
    return g


def record(p, rows, label, label_kind, label_source, extra=None):
    row = rows[p["row_id"]]
    rec = {
        "piece_id": p["piece_id"], "row_id": p["row_id"], "piece": p["piece"],
        "certainty": p["certainty"], "attribution": p["attribution"],
        "checkable": p["checkable"], "kind": p["kind"], "tags": p["tags"], "door": p["door"],
        "separate_groups": separate_groups(p),
        "cited_works": [{"key": s["name"], "file": s["file"],
                         "available": (ROOT / s["file"]).exists()
                         and os.path.getsize(ROOT / s["file"]) > 0} for s in row["sources"]],
        "claim_date": None,
        "original_sentence": p["original_sentence"], "earlier_sentence": p["earlier_sentence"],
        "label": label, "label_kind": label_kind, "label_source": label_source,
        "claim_label": p["label"]}
    if extra:
        rec.update(extra)
    return rec


def build():
    rows = {r["row_id"]: r for r in json.load(open(BENCH))["rows"]}
    pieces = {p["piece_id"]: p for p in read_jsonl(HERE / "pieces_262.jsonl")}
    labels = {l["piece_id"]: l for l in read_jsonl(HERE / "piece_labels.jsonl")}
    wrong, right = [], []
    for pid, p in pieces.items():
        if p["door"] == "skipped":
            continue
        if p["label"] == "supported":
            right.append(record(p, rows, "supported", None, "claim label: every part proven (card 34)"))
        elif pid in labels and labels[pid]["label"] in ("supported", "not_supported"):
            l = labels[pid]
            extra = {"label_quote": l["quote"], "label_reason": l["reason"]}
            rec = record(p, rows, l["label"], l["kind"], "piece label: decided and checked (card 390)", extra)
            (wrong if l["label"] == "not_supported" else right).append(rec)
    return pick(wrong, N_EACH, SEED), pick(right, N_EACH, SEED + 1), len(wrong), len(right)


def summary(chosen_w, chosen_r, n_w, n_r):
    allp = chosen_w + chosen_r
    s = collections.OrderedDict()
    s["wrong_available"], s["right_available"] = n_w, n_r
    s["wrong"], s["right"] = len(chosen_w), len(chosen_r)
    s["claims_covered"] = len({p["row_id"] for p in allp})
    s["by_label_and_door"] = {"%s/%s" % k: v for k, v in sorted(
        collections.Counter((p["label"], p["door"]) for p in allp).items())}
    s["separate_groups"] = {g: {"supported": sum(1 for p in chosen_r if g in p["separate_groups"]),
                                "not_supported": sum(1 for p in chosen_w if g in p["separate_groups"])}
                            for g in SEPARATE}
    s["in_none_of_the_separate_groups"] = {
        "supported": sum(1 for p in chosen_r if not p["separate_groups"]),
        "not_supported": sum(1 for p in chosen_w if not p["separate_groups"])}
    s["wrong_by_kind"] = dict(collections.Counter(p["label_kind"] for p in chosen_w))
    s["right_by_label_source"] = dict(collections.Counter(p["label_source"] for p in chosen_r))
    s["every_piece_has_a_source_file"] = all(all(w["available"] for w in p["cited_works"]) for p in allp)
    return s


def sample_page(chosen_w, chosen_r):
    fresh = [p for p in chosen_w + chosen_r if p["label_source"].startswith("piece label")]
    rng = random.Random(SEED)
    sample = sorted(rng.sample(fresh, 20), key=lambda p: p["piece_id"])
    lines = ["# Card 390: 20 freshly labelled pieces for the author to look at", "",
             "Each piece was cut from a wrong claim of the 262-claim benchmark. A Sonnet helper decided its label from the old label's parts and quotes, and a Haiku helper checked the reason (both through the subscription, no money). The pieces were drawn at random (seed 390) from the %d freshly labelled pieces of the first round." % len(fresh), ""]
    for i, p in enumerate(sample, 1):
        lab = p["label"] + (" (%s)" % p["label_kind"] if p["label_kind"] else "")
        lines += ["## %d. %s" % (i, p["piece_id"]), "",
                  "- Piece: %s" % p["piece"],
                  "- Certainty: %s" % p["certainty"],
                  "- The whole claim: %s" % p["original_sentence"].replace("\n", " "),
                  "- Label: %s" % lab,
                  "- Quote from the source: %s" % (p.get("label_quote") or "none"),
                  "- Why: %s" % p.get("label_reason"),
                  "- Source file: %s" % ", ".join(w["file"] for w in p["cited_works"]),
                  "- Your answer (agree / disagree, and why):", ""]
    return "\n".join(lines)


def main():
    w, r, n_w, n_r = build()
    with open(HERE / "round1.jsonl", "w") as f:
        for p in w + r:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    s = summary(w, r, n_w, n_r)
    json.dump(s, open(HERE / "round1_summary.json", "w"), indent=1)
    open(HERE / "label_sample_20.md", "w").write(sample_page(w, r))
    print(json.dumps(s, indent=1))


if __name__ == "__main__":
    main()
