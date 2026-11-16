"""Card 398 (branch B), step 2: of the pieces a step calls "not supported", how many are right pieces
(label supported), split by the signals of the plan page. Tuning part only (records.load_labels).

Usage: analyse.py [--step gemma|haiku|sonnet|jev|gemma_f1] [--list FILE]
  --list writes every wrongly rejected right piece of the step (piece, answer, reason, detail, quote,
  quote checks, Jev score) as jsonl, for reading the causes (step 3).
Signals: reason kind; detail named; quote found word for word in the work; quote check passed
(numbers and names of the piece in the quote); Jev's number; the three fields_v1 Gemma repeats agree.
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

ask = sibling("ask")
records = sibling("records")
quote_check = sibling("quote_check")
SA = HERE.parent / "saved_answers"
ANS = HERE / "answers"
FILES = {"gemma": ANS / "gemma.jsonl", "haiku": ANS / "haiku.jsonl", "sonnet": ANS / "sonnet.jsonl",
         "jev": ANS / "jev.jsonl", "gemma_f1": SA / "gemma_paid_fields_v1.jsonl"}


def first(ans, pid):
    reps = ans.get(pid) or []
    r = next((x for x in reps if x["repeat"] == 0), None)
    return None if r is None or r.get("failed") or r.get("answer") is None else r


def jev_band(s):
    if s is None:
        return "no Jev"
    return "Jev <0.2" if s < 0.2 else "Jev 0.2-0.5" if s < 0.5 else "Jev >=0.5"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", default="gemma")
    ap.add_argument("--list")
    a = ap.parse_args(argv)
    labels = records.load_labels(final=False)
    pieces = {r["piece_id"]: r for r in records.load_pieces()}
    ans = ask.load_answers(FILES[a.step])
    jev = ask.load_answers(FILES["jev"]) if FILES["jev"].exists() else {}
    g3 = ask.load_answers(FILES["gemma_f1"])
    ids = sorted(p for p in labels if p in pieces and records.step0(pieces[p]) is None)
    tot = Counter()
    by = defaultdict(lambda: Counter())
    rows = []
    for p in ids:
        r = first(ans, p)
        lab = labels[p]["label"]
        tot[(lab, r["answer"] if r else "no answer")] += 1
        if not r or r["answer"] != "not_supported":
            continue
        works = [records.work_text(w["file"]) for w in pieces[p]["cited_works"]]
        qin = quote_check.quote_in_work(r.get("quote"), works)
        qc = quote_check.check(pieces[p]["piece"], r.get("quote") or "", works)
        j = first(jev, p)
        js = j.get("jev_score") if j else None
        reps = [x for x in g3.get(p) or [] if not x.get("failed") and x.get("answer")]
        agree = "Gemma3 agree" if len(reps) > 1 and len({x["answer"] for x in reps}) == 1 else "Gemma3 differ"
        sig = {"reason": r.get("reason_kind") or "none", "detail": "detail" if (r.get("detail") or "").strip()
               else "no detail", "quote": "quote in work" if qin else "quote not in work",
               "qcheck": "quote check passed" if qc["passed"] else "quote check failed",
               "jev": jev_band(js), "g3": agree}
        for k, v in sig.items():
            by[k][(v, lab)] += 1
        if lab == "supported":
            rows.append({"piece_id": p, "piece": pieces[p]["piece"], "reason_kind": r.get("reason_kind"),
                         "detail": r.get("detail"), "quote": r.get("quote"), "quote_in_work": qin,
                         "quote_check": qc, "jev_score": js, "label_kind": labels[p].get("label_kind"),
                         "door": labels[p].get("door"), "tags": labels[p].get("tags"),
                         "gemma3": [x["answer"] for x in reps]})
    print(f"step {a.step}, tuning pieces {len(ids)}")
    print("label x answer:", dict(sorted(tot.items())))
    for k in by:
        print(f"-- not_supported answers by {k} (wrong = label not_supported, RIGHT = wrongly rejected)")
        for v in sorted({v for v, _ in by[k]}):
            print(f"   {v:22s} wrong {by[k][(v, 'not_supported')]:3d}  RIGHT {by[k][(v, 'supported')]:3d}")
    if a.list:
        with open(a.list, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"{len(rows)} wrongly rejected right pieces listed in {a.list}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
