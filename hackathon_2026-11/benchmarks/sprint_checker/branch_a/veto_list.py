"""Card 397 (branch A): every tuning piece where Haiku rewording 1 says not supported while Haiku or
Sonnet said supported, with its label, the rewording's reason and detail, and the Sonnet answer.
Tuning part only (records.load_labels), to read why the veto holds back right pieces.

  veto_list.py [--right]   (--right: only the pieces labelled supported)
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

ask = sibling("ask")
records = sibling("records")
A = HERE / "answers"


def first(path):
    out = {}
    for pid, reps in ask.load_answers(path).items():
        r0 = next((r for r in reps if r["repeat"] == 0 and not r.get("failed")), None)
        if r0:
            out[pid] = r0
    return out


def main(argv):
    labels = records.load_labels()
    pieces = {r["piece_id"]: r for r in records.load_pieces()}
    h2, h, s = first(A / "haiku_ss_po_e.jsonl"), first(A / "haiku_po_e.jsonl"), first(A / "sonnet_po_e.jsonl")
    for pid in sorted(labels):
        r = h2.get(pid)
        if not r or r["answer"] != "not_supported":
            continue
        if "--right" in argv and labels[pid]["label"] != "supported":
            continue
        print(json.dumps({"piece_id": pid, "label": labels[pid]["label"], "door": labels[pid]["door"],
                          "piece": pieces[pid]["piece"], "sonnet": (s.get(pid) or {}).get("answer"),
                          "haiku": (h.get(pid) or {}).get("answer"), "reason": r.get("reason_kind"),
                          "detail": (r.get("detail") or "")[:240]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
