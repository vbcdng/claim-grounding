"""Card 405 (branch A, round 5): the main-group tuning pieces a decisions file leaves undecided or
not checked, with the step that left them, the label and every step's first answer (answer and reason;
FAILED for a failed call, - for not asked). Used to test Fable's rules 2 and 3
(fable_answer_round5.md). Tuning part only (records.load_labels).

  pools_r5.py <decisions.jsonl>
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
FILES = {"g": "gemma_paid_po_e.jsonl", "h": "haiku_po_e.jsonl", "s": "sonnet_po_e.jsonl",
         "h2": "haiku_ss_po_e.jsonl", "s2": "sonnet_ss_po_e.jsonl"}


def first(path):
    out = {}
    for pid, reps in ask.load_answers(path).items():
        r0 = next((r for r in reps if r["repeat"] == 0), None)
        if r0:
            out[pid] = r0
    return out


def show(r):
    if r is None:
        return "-"
    if r.get("failed") or r.get("answer") is None:
        return "FAILED"
    return f"{r['answer']}/{r.get('reason_kind') or ''}"


def main(argv):
    labels = records.load_labels()
    ans = {k: first(A / f) for k, f in FILES.items()}
    dec = {}
    with open(argv[0]) as f:
        for line in f:
            d = json.loads(line)
            dec[d["piece_id"]] = d
    for pid in sorted(labels):
        lab = labels[pid]
        d = dec.get(pid)
        if lab["door"] != "in_scope" or not d or d["answer"] not in ("undecided", "not_checked"):
            continue
        cols = " ".join(f"{k}={show(ans[k].get(pid))}" for k in FILES)
        print(f"{d['step']:<42} {lab['label']:<14} {pid}  {cols}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
