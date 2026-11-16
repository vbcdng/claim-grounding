"""Card 405, change 2 of Fable 5.2's second answer: the tuning pieces where Sonnet's neutral answer
(answers/sonnet_po_e.jsonl, repeat 0) is "unsure". No label is read. Writes one id per line.

  ./venv/bin/python3 benchmarks/sprint_checker/branch_a/pick_unsure.py <out file>
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

ask = sibling("ask")


def unsure_ids(path=HERE / "answers" / "sonnet_po_e.jsonl"):
    out = []
    for pid, reps in ask.load_answers(path).items():          # tuning part only
        r0 = next((r for r in reps if r["repeat"] == 0), None)
        if r0 and not r0.get("failed") and r0.get("answer") == "unsure":
            out.append(pid)
    return sorted(out)


if __name__ == "__main__":
    ids = unsure_ids()
    Path(sys.argv[1]).write_text("".join(i + "\n" for i in ids), encoding="utf-8")
    print(f"{len(ids)} tuning pieces where Sonnet is unsure -> {sys.argv[1]}")
