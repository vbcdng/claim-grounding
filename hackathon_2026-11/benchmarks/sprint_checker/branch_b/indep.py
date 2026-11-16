"""Card 398 (branch B), check (b) of ruling 20: how often Jev repeats a step's wrong rejection.

For a step's "not supported" answers on the tuning part: of those on RIGHT pieces (wrong rejections),
how many Jev also scores below the line t (Jev repeats the mistake); of those on WRONG pieces (right
rejections), how many Jev keeps below t. Also the same inside the cheap rule's filter (reason differs
or contradicted, detail named, quote word for word in the work). Usage: indep.py [--step gemma]
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

ask = sibling("ask")
records = sibling("records")
quote_check = sibling("quote_check")
ANS = HERE / "answers"
SA = HERE.parent / "saved_answers"


def first(ans, pid):
    r = next((x for x in ans.get(pid) or [] if x["repeat"] == 0), None)
    return None if r is None or r.get("failed") or r.get("answer") is None else r


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", default="gemma")
    a = ap.parse_args(argv)
    path = SA / "gemma_paid_fields_v1.jsonl" if a.step == "gemma_f1" else ANS / f"{a.step}.jsonl"
    labels = records.load_labels(final=False)
    pieces = {r["piece_id"]: r for r in records.load_pieces()}
    ans, jev = ask.load_answers(path), ask.load_answers(ANS / "jev.jsonl")
    rows = []
    for p in labels:
        r = first(ans, p)
        if not r or r["answer"] != "not_supported":
            continue
        works = [records.work_text(w["file"]) for w in pieces[p]["cited_works"]]
        filt = (r.get("reason_kind") in ("differs_in_detail", "contradicted") and bool((r.get("detail") or "").strip())
                and quote_check.quote_in_work(r.get("quote"), works))
        j = first(jev, p)
        rows.append((labels[p]["label"], filt, j.get("jev_score") if j else None))
    for name, sel in (("all rejections", lambda f: True), ("inside the cheap filter", lambda f: f)):
        wr = [s for lab, f, s in rows if lab == "supported" and sel(f)]
        ok = [s for lab, f, s in rows if lab == "not_supported" and sel(f)]
        print(f"{a.step}, {name}: {len(wr)} wrong rejections, {len(ok)} right rejections "
              f"(no Jev answer: {sum(s is None for s in wr)} / {sum(s is None for s in ok)})")
        for t in (0.1, 0.2, 0.3, 0.4, 0.5):
            print(f"   line {t}: Jev repeats {sum(1 for s in wr if s is not None and s < t)} of {len(wr)} "
                  f"wrong rejections; keeps {sum(1 for s in ok if s is not None and s < t)} of {len(ok)} right ones")


if __name__ == "__main__":
    sys.exit(main())
