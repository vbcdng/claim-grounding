"""Card 398 (branch B), step 7: how the veto against Sonnet's "not supported" acts on the tuning part.

For every piece Sonnet rejects: does an earlier step say "supported" with a quote that (a) stands in
the work word for word, (b) passes the quote check (in the work, and the piece's numbers and names in
it)? Counted separately for wrong pieces (a veto there adds an undecided piece) and right pieces (a
veto there removes a wrong rejection). Usage: veto.py [--gemma FILE]
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

ask = sibling("ask")
records = sibling("records")
quote_check = sibling("quote_check")
ANS = HERE / "answers"


def first(ans, pid):
    r = next((x for x in ans.get(pid) or [] if x["repeat"] == 0), None)
    return None if r is None or r.get("failed") or r.get("answer") is None else r


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--gemma", default=str(ANS / "gemma.jsonl"))
    a = ap.parse_args(argv)
    labels = records.load_labels(final=False)
    pieces = {r["piece_id"]: r for r in records.load_pieces()}
    steps = {"g": ask.load_answers(a.gemma), "h": ask.load_answers(ANS / "haiku.jsonl")}
    s = ask.load_answers(ANS / "sonnet.jsonl")
    c = Counter()
    for p in labels:
        rs = first(s, p)
        if not rs or rs["answer"] != "not_supported":
            continue
        lab = labels[p]["label"]
        works = [records.work_text(w["file"]) for w in pieces[p]["cited_works"]]
        sig = {}
        for name, ans in steps.items():
            r = first(ans, p)
            sup = bool(r and r["answer"] == "supported")
            sig[name + "_sup"] = sup
            sig[name + "_in"] = sup and quote_check.quote_in_work(r.get("quote"), works)
            sig[name + "_pass"] = sup and quote_check.check(pieces[p]["piece"], r.get("quote") or "", works)["passed"]
        for k, v in sig.items():
            c[(k, lab)] += v
        c[("any_pass", lab)] += sig["g_pass"] or sig["h_pass"]
        c[("any_in", lab)] += sig["g_in"] or sig["h_in"]
        c[("both_in", lab)] += sig["g_in"] and sig["h_in"]
        c[("n", lab)] += 1
    print("Sonnet 'not supported' on the tuning part; per signal: wrong pieces (veto costs) / right pieces (veto helps)")
    for k in sorted({k for k, _ in c}):
        print(f"  {k:10s} wrong {c[(k, 'not_supported')]:3d}  right {c[(k, 'supported')]:3d}")


if __name__ == "__main__":
    sys.exit(main())
