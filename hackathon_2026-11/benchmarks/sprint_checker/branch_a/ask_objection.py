"""Card 405, change 1 of Fable 5.2's second answer (fable_answer_2.md): show an objection already on
record to Opus and save its judgement (question questions/objection_v1.md, field set objection_v1).

Picked pieces (no label read; every one of the 300 pieces, both halves):
- lifted:     reworded Haiku (h2) not supported, reworded Sonnet (s2) supported; objection = h2 detail
- not_lifted: h2 not supported, s2 not supported/unsure/missing; objection = h2 detail
- veto:       neither of the above; Sonnet (s) supported and neutral Haiku (h) not supported with a
              named difference (differs in a detail, detail given) or a contradiction backed by a quote
              from the work (the chain's "veto" rule); objection = h detail
Pieces where Sonnet's neutral answer is "not supported" are not picked (that answer is final), nor
pieces whose objection text is empty.

  ./venv/bin/python3 benchmarks/sprint_checker/branch_a/ask_objection.py list --out <ids file>
  ./venv/bin/python3 benchmarks/sprint_checker/branch_a/ask_objection.py trial --out <ids file>
  ./venv/bin/python3 benchmarks/sprint_checker/branch_a/ask_objection.py ask --pieces <ids file> --out <answers>
  add --pool sonnet_no to list or ask: change 1 of fable_answer_3.md (see pick_sonnet_no)

`trial` (tuning part only; reads tuning labels) writes Fable's 14 pieces: the 7 wrong lifted holds it
named and the first 7 right lifted holds by piece id. `ask` uses the subscription (claude-code/opus).
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent / "shared"
sys.path.insert(0, str(SHARED))
from sprint_shared_load import sibling  # noqa: E402

ask = sibling("ask")
records = sibling("records")
split = sibling("split")
quote_check = sibling("quote_check")

QUESTION = HERE / "questions" / "objection_v1.md"
FIELD_SET = "objection_v1"
FIELD_SETS_A = HERE / "field_sets_a.json"
ANSWERS = {"h": "answers/haiku_po_e.jsonl", "s": "answers/sonnet_po_e.jsonl",
           "h2": "answers/haiku_ss_po_e.jsonl", "s2": "answers/sonnet_ss_po_e.jsonl"}
FABLE_WRONG7 = ["c312_vb086.r0.p0", "c312_vb119.r0.p0", "c312_vb322.r0.p0", "c312_vb309.r0.p0",
                "c312_vb215.r0.p5", "c312_vb309.r0.p1", "vb007.r0.p1"]

_orig_load_field_set = records.load_field_set


def _load_field_set(name, path=None):
    """Branch A's own field set file first (it adds objection_v1), else the shared one."""
    if path is None:
        own = json.loads(FIELD_SETS_A.read_text(encoding="utf-8"))
        path = FIELD_SETS_A if name in own["sets"] else records.FIELD_SETS
    return _orig_load_field_set(name, path)


records.load_field_set = _load_field_set


def first(answers, step, pid):
    r0 = next((r for r in answers[step].get(pid) or [] if r["repeat"] == 0), None)
    if r0 is None or r0.get("failed") or r0.get("answer") is None:
        return None
    return r0


def load_all():
    return {k: ask.load_answers(HERE / v, label_free=True) for k, v in ANSWERS.items()}


def pick(answers, pieces):
    """piece_id -> (kind, objection text); reads no label."""
    out = {}
    by_id = {r["piece_id"]: r for r in pieces}
    for pid in sorted(by_id):
        s, h, h2, s2 = (first(answers, k, pid) for k in ("s", "h", "h2", "s2"))
        if s is not None and s["answer"] == "not_supported":
            continue
        veto = s is not None and s["answer"] == "supported" and h is not None and h["answer"] == "not_supported" and (
            (h.get("reason_kind") == "differs_in_detail" and (h.get("detail") or "").strip())
            or (h.get("reason_kind") == "contradicted"
                and quote_check.quote_in_work(
                    h.get("quote"), [records.work_text(w["file"]) for w in by_id[pid]["cited_works"]])))
        h_text = ((h or {}).get("detail") or "").strip() or f'The work says the opposite: "{(h or {}).get("quote")}"'
        if h2 is not None and h2["answer"] == "not_supported":
            kind = "lifted" if (s2 is not None and s2["answer"] == "supported") else "not_lifted"
            text = (h2.get("detail") or "").strip()
            if veto:                      # both Haiku objections are on record: show both
                kind += "_veto"
                text = (text + "\n" + h_text).strip() if h_text != text else text
        elif veto:
            kind, text = "veto", h_text
        else:
            continue
        if text:
            out[pid] = (kind, text)
    return out


def pick_sonnet_no(answers, pieces):
    """Change 1 of Fable 5.2's third answer (fable_answer_3.md): piece_id -> ("sonnet_no", objection).
    Picked: Sonnet (s) not supported, neutral Haiku (h) supported, reworded Haiku (h2) answered and not
    'not supported'. Objection = Sonnet's own detail, or for a contradiction with no detail its quote.
    Pieces with no objection text are not picked. Reads no label."""
    out = {}
    for pid in sorted(r["piece_id"] for r in pieces):
        s, h, h2 = (first(answers, k, pid) for k in ("s", "h", "h2"))
        if s is None or s["answer"] != "not_supported" or h is None or h["answer"] != "supported":
            continue
        if h2 is None or h2["answer"] == "not_supported":
            continue
        text = (s.get("detail") or "").strip()
        if not text and s.get("reason_kind") == "contradicted" and (s.get("quote") or "").strip():
            text = f'The work says the opposite: "{s["quote"].strip()}"'
        if text:
            out[pid] = ("sonnet_no", text)
    return out


def write_ids(path, ids):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("".join(i + "\n" for i in ids), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("what", choices=["list", "trial", "ask"])
    ap.add_argument("--pieces", help="ask: file of piece ids")
    ap.add_argument("--out", required=True)
    ap.add_argument("--pool", choices=["objection", "sonnet_no"], default="objection",
                    help="sonnet_no: change 1 of fable_answer_3.md (Sonnet's no where Haiku disagrees)")
    a = ap.parse_args(argv)
    pieces = records.load_pieces()
    picks = (pick_sonnet_no if a.pool == "sonnet_no" else pick)(load_all(), pieces)
    tuning = split.allowed_ids(False)
    if a.what == "trial" and a.pool != "objection":
        raise SystemExit("refused: trial is only for the objection pool")
    if a.what == "list":
        for half, ids in (("tuning", tuning), ("held-back", set(p["piece_id"] for p in pieces) - tuning)):
            kinds = [k for pid, (k, _) in picks.items() if pid in ids]
            print(half, len(kinds), {k: kinds.count(k) for k in sorted(set(kinds))})
        write_ids(a.out, sorted(picks))
        print(f"{len(picks)} piece ids -> {a.out}")
        return 0
    if a.what == "trial":
        labels = records.load_labels(final=False)
        missing = [p for p in FABLE_WRONG7 if not picks.get(p, ("",))[0].startswith("lifted")]
        if missing:
            print(f"not lifted holds with an objection: {missing}")
        wrong = [p for p in FABLE_WRONG7 if p not in missing]
        right = [p for p in sorted(picks) if p in tuning and picks[p][0].startswith("lifted")
                 and labels[p]["label"] == "supported"][:7]
        print("kinds:", {p: picks[p][0] for p in wrong + right})
        write_ids(a.out, wrong + right)
        print(f"trial: {len(wrong)} wrong + {len(right)} right -> {a.out}")
        return 0
    want = [x.strip() for x in Path(a.pieces).read_text(encoding="utf-8").splitlines() if x.strip()]
    unknown = [p for p in want if p not in picks]
    if unknown:
        raise SystemExit(f"refused: not picked pieces {unknown[:5]}")
    recs = [dict(r, objection=picks[r["piece_id"]][1]) for r in pieces if r["piece_id"] in set(want)]
    out = Path(a.out)
    caller = ask.claude_caller("opus", out.parent)
    try:
        n = ask.ask_pieces(recs, "opus", str(QUESTION), FIELD_SET, out, 1, caller)
    except ask.CallsKeepFailing as e:
        print(f"stopped: {e}")
        return 5
    lines = ask.read_lines(out)
    print(f"opus / {FIELD_SET}: {n} new lines; file holds {len(lines)} lines, "
          f"{sum(1 for r in lines if r.get('failed'))} failed; {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
