"""Card 390: combine the decide and check steps into one label per piece of the 128 wrong claims.

Inputs: label_batches/label_NN.jsonl (make_label_batches.py), label_out/label_NN.decide.jsonl
(Sonnet helpers) and label_out/label_NN.check.jsonl (Haiku helpers), card 390 session 2026-11-13,
subscription, no money; rules in label_instructions.md.
A piece's label is final only when all of these hold; otherwise it is "doubtful" and stays out of
the round:
- the checker says the reason holds and gives the same label;
- the decider's quote (if any) is one of the old parts' quotes that the old run found in the source
  (quote_found_in), or is found in a text source file (check_quotes);
- the label is supported or not_supported (unsure stays out);
- not a hedged piece labelled differs_in_detail: the decide run was made before the certainty rule
  was corrected (13 Nov), so those labels may rest on the wrong rule.
Output: piece_labels.jsonl.
Run: ./venv/bin/python3 benchmarks/card390/merge_labels.py
"""
import collections
import glob
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DECIDER = "Sonnet (claude-sonnet-5-5), subscription, card 390 helpers, 2026-11-13"
CHECKER = "Haiku (claude-haiku-5-5), subscription, card 390 helpers, 2026-11-13"


def read_jsonl(path):
    return [json.loads(l) for l in open(path) if l.strip()]


def norm(s):
    return re.sub(r"\W+", " ", (s or "").lower()).strip()


def old_quotes(item):
    """Old quotes that the old run found in a source, normalised."""
    out = set()
    for p in item["old_parts"]:
        if p.get("quote"):
            out.add(norm(p["quote"]))
    return out


def check_quotes(quote, item, found_old):
    """True when the quote is empty, is an old found quote, or occurs in a text source file."""
    if not quote:
        return True
    q = norm(quote)
    if any(q in o or o in q for o in found_old if o):
        return True
    for f in item["source_files"]:
        if f.endswith(".txt") and (ROOT / f).exists():
            if q in norm((ROOT / f).read_text(errors="replace")):
                return True
    return False


def final(d, c, piece, quote_ok):
    why = []
    if not c.get("reason_holds"):
        why.append("checker: reason does not hold")
    if c.get("own_label") != d["label"]:
        why.append("checker's own label %s" % c.get("own_label"))
    if not quote_ok:
        why.append("quote not found")
    if d["label"] not in ("supported", "not_supported"):
        why.append("decider unsure")
    if (piece["certainty"] != "stated" and d["label"] == "not_supported"
            and d.get("kind") == "differs_in_detail"):
        why.append("hedged piece judged before the certainty rule was corrected")
    return ("doubtful" if why else d["label"]), why


def build():
    out = []
    for b in sorted(glob.glob(str(HERE / "label_batches" / "label_*.jsonl"))):
        stem = Path(b).stem
        items = read_jsonl(b)
        dec = read_jsonl(HERE / "label_out" / (stem + ".decide.jsonl"))
        chk = read_jsonl(HERE / "label_out" / (stem + ".check.jsonl"))
        want = [p["piece_id"] for it in items for p in it["pieces"]]
        if [d["piece_id"] for d in dec] != want or [c["piece_id"] for c in chk] != want:
            raise SystemExit("%s: decide/check piece ids differ from the input" % stem)
        by_piece = {p["piece_id"]: (it, p) for it in items for p in it["pieces"]}
        for d, c in zip(dec, chk):
            it, p = by_piece[d["piece_id"]]
            q_ok = check_quotes(d.get("quote"), it, old_quotes(it))
            lab, why = final(d, c, p, q_ok)
            out.append({"piece_id": d["piece_id"], "row_id": it["row_id"], "label": lab,
                        "kind": d.get("kind") if lab == "not_supported" else None,
                        "decided": d["label"], "decided_kind": d.get("kind"),
                        "checker_label": c.get("own_label"), "reason_holds": c.get("reason_holds"),
                        "quote": d.get("quote"), "quote_ok": q_ok, "reason": d.get("reason"),
                        "check_problem": c.get("problem", ""), "doubtful_because": why,
                        "decided_by": DECIDER, "checked_by": CHECKER})
    return out


def main():
    out = build()
    with open(HERE / "piece_labels.jsonl", "w") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("pieces:", len(out), dict(collections.Counter(r["label"] for r in out)))
    print("doubtful because:", dict(collections.Counter(w for r in out for w in r["doubtful_because"])))
    print("wrong-claim rows with at least one final not_supported piece:",
          len({r["row_id"] for r in out if r["label"] == "not_supported"}))


if __name__ == "__main__":
    main()
