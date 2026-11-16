"""Card 397 (branch A), step 2 of the card: of the pieces a step calls "supported", how many are
wrong pieces, split by the signals of the plan page. Tuning part only (labels through
records.load_labels, answers through ask.load_answers; never --final).

  signals.py                     the table for every step in STEPS
  signals.py --list gemma        every wrongly passed piece of one step, for reading by cause
  signals.py --cheap             the cheap approval rule (Gemma + quote check + Jev line) per line
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent / "shared"
SAVED = HERE.parent / "saved_answers"
sys.path.insert(0, str(SHARED))

from sprint_shared_load import sibling  # noqa: E402

ask = sibling("ask")
quote_check = sibling("quote_check")
records = sibling("records")

STEPS = {
    "gemma": [SAVED / "gemma_paid_fields_v1.jsonl"],
    "jev": [SAVED / "jev_fields_merged_check.jsonl"],
    "haiku_v1": [SAVED / "haiku_fields_v1_merged.jsonl"],
    "haiku_po": [SAVED / "haiku_piece_only.jsonl"],
    "sonnet_v1": [SAVED / "sonnet_fields_v1_merged.jsonl"],
    "sonnet_po": [SAVED / "sonnet_piece_only_fieldtest.jsonl", SAVED / "sonnet_piece_only_p0.jsonl",
                  SAVED / "sonnet_piece_only_p1.jsonl"],
    # the merged piece_only answers the chain uses (merge.py); --po-e takes Gemma and Jev from these too
    "gemma_pe": [HERE / "answers" / "gemma_paid_po_e.jsonl"],
    "jev_pe": [HERE / "answers" / "jev_po_e.jsonl"],
    "haiku_pe": [HERE / "answers" / "haiku_po_e.jsonl"],
    "sonnet_pe": [HERE / "answers" / "sonnet_po_e.jsonl"],
    "haiku_ss": [HERE / "answers" / "haiku_ss_po_e.jsonl"],  # Haiku rewording 1, asked on "yes" pieces only
}
JEV_LINES = [0.5, 0.6, 0.7, 0.8, 0.9, 0.95]


def answers(step):
    out = {}
    for p in STEPS[step]:
        if p.exists():
            for pid, reps in ask.load_answers(p).items():
                out[pid] = reps
    return out


def first(reps):
    r0 = next((r for r in reps or [] if r["repeat"] == 0), None)
    if r0 is None or r0.get("failed") or r0.get("answer") is None:
        return None
    return r0


class Ctx:
    def __init__(self):
        self.labels = records.load_labels()
        self.pieces = {r["piece_id"]: r for r in records.load_pieces()}
        self.ids = sorted(p for p in self.labels if records.step0(self.pieces[p]) is None)
        self._w = {}

    def works(self, pid):
        if pid not in self._w:
            self._w[pid] = [records.work_text(w["file"]) for w in self.pieces[pid]["cited_works"]]
        return self._w[pid]

    def qc(self, pid, r):
        return quote_check.check(self.pieces[pid]["piece"], r.get("quote") or "", self.works(pid))

    def wrong(self, pid):
        return self.labels[pid]["label"] == "not_supported"

    def main_group(self, pid):
        return self.labels[pid]["door"] == "in_scope"


def table(ctx, step, jev, gem):
    ans = answers(step)
    rows = Counter()
    for pid in ctx.ids:
        r = first(ans.get(pid))
        if r is None or r["answer"] != "supported":
            continue
        w = ctx.wrong(pid)
        sig = []
        if step.startswith("jev"):
            sig =[f"jev>={t}" for t in JEV_LINES if (r.get("jev_score") or 0) >= t]
        else:
            q = ctx.qc(pid, r)
            sig.append("quote in work" if q["quote_in_work"] else "quote NOT in work")
            sig.append("numbers+names in quote" if q["covers_piece"] else "numbers/names missing")
            if q["passed"]:
                sig.append("quote check passes")
            reps = [x for x in ans.get(pid) or [] if not x.get("failed")]
            if len(reps) > 1:
                sig.append("repeats agree" if len({x["answer"] for x in reps}) == 1 else "repeats differ")
            j = first(jev.get(pid))
            if j is not None and j.get("jev_score") is not None:
                sig.append("jev>=0.5" if j["jev_score"] >= 0.5 else "jev<0.5")
            else:
                sig.append("no jev")
            if not step.startswith("gemma"):
                g = first(gem.get(pid))
                sig.append("gemma also supported" if g and g["answer"] == "supported" else "gemma not supported")
        for s in ["all"] + sig:
            rows[(s, "n")] += 1
            rows[(s, "wrong")] += w
            if ctx.main_group(pid):
                rows[(s, "n_main")] += 1
                rows[(s, "wrong_main")] += w
            if w:
                rows[(s, "kind:" + str(ctx.labels[pid].get("label_kind")))] += 1
    names = sorted({k[0] for k in rows}, key=lambda s: (s != "all", s))
    print(f"\n== {step}: answers 'supported' on the tuning part (checked pieces; main group in brackets)")
    for s in names:
        kinds = ", ".join(f"{k[5:]} {v}" for (n, k), v in sorted(rows.items()) if n == s and k.startswith("kind:"))
        print(f"  {s:28s} {rows[(s, 'wrong')]:3d} wrong of {rows[(s, 'n')]:3d}"
              f"  ({rows[(s, 'wrong_main')]} of {rows[(s, 'n_main')]})  {kinds}")


def cheap(ctx, gem, jev):
    print("\n== cheap approval: Gemma supported (first repeat) + quote check passes + Jev >= line")
    for t in JEV_LINES:
        n = w = nm = wm = 0
        for pid in ctx.ids:
            g, j = first(gem.get(pid)), first(jev.get(pid))
            if not (g and g["answer"] == "supported" and j and (j.get("jev_score") or 0) >= t):
                continue
            if not ctx.qc(pid, g)["passed"]:
                continue
            n += 1
            w += ctx.wrong(pid)
            if ctx.main_group(pid):
                nm += 1
                wm += ctx.wrong(pid)
        print(f"  line {t}: approves {n} ({nm} main), wrong {w} ({wm} main)")


def listing(ctx, step):
    ans = answers(step)
    for pid in ctx.ids:
        r = first(ans.get(pid))
        if r is None or r["answer"] != "supported" or not ctx.wrong(pid):
            continue
        lab = ctx.labels[pid]
        q = ctx.qc(pid, r) if not step.startswith("jev") else {}
        print(json.dumps({"piece_id": pid, "door": lab["door"], "label_kind": lab.get("label_kind"),
                          "piece": ctx.pieces[pid]["piece"], "label_reason": lab.get("label_reason"),
                          "label_quote": (lab.get("label_quote") or "")[:300],
                          "model_quote": (r.get("quote") or "")[:300], "jev": r.get("jev_score"),
                          "quote_check": {k: q.get(k) for k in ("quote_in_work", "missing")}},
                         ensure_ascii=False))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", choices=sorted(STEPS))
    ap.add_argument("--cheap", action="store_true")
    ap.add_argument("--steps", nargs="*", default=sorted(STEPS))
    ap.add_argument("--po-e", action="store_true", help="Gemma and Jev signals from the piece_only files")
    a = ap.parse_args(argv)
    ctx = Ctx()
    if a.list:
        listing(ctx, a.list)
        return 0
    gem, jev = (answers("gemma_pe"), answers("jev_pe")) if a.po_e else (answers("gemma"), answers("jev"))
    if a.cheap:
        cheap(ctx, gem, jev)
        return 0
    for s in a.steps:
        table(ctx, s, jev, gem)
    return 0


if __name__ == "__main__":
    sys.exit(main())
