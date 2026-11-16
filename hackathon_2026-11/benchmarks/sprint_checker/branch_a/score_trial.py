"""Card 405: Fable's test of the 14-piece trial of change 1 (fable_answer_2.md). Tuning labels only.
Passes when Opus confirms the objection ("not supported") on at least 4 of the 7 wrong pieces and on at
most 1 of the 7 right pieces. Failed calls are listed, never counted as an answer.

  ./venv/bin/python3 benchmarks/sprint_checker/branch_a/score_trial.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

ask = sibling("ask")
records = sibling("records")


def main():
    ids = [x.strip() for x in (HERE / "answers" / "objection_trial14.txt").read_text().splitlines() if x.strip()]
    labels = records.load_labels(final=False)
    ans = ask.load_answers(HERE / "answers" / "opus_objection.jsonl")
    hit = {"not_supported": 0, "supported": 0}
    tally = {"wrong": dict(hit), "right": dict(hit)}
    failed = []
    for pid in ids:
        r0 = next((r for r in ans.get(pid, []) if r["repeat"] == 0), None)
        group = "wrong" if labels[pid]["label"] == "not_supported" else "right"
        if r0 is None or r0.get("failed"):
            failed.append(pid)
            continue
        tally[group][r0["answer"]] = tally[group].get(r0["answer"], 0) + 1
        diff = (r0.get("reply") or "").split('"difference"')[1][:8] if '"difference"' in (r0.get("reply") or "") else "?"
        print(f"{pid:22} {group:5} opus={r0['answer']:13} difference{diff.strip()} | {(r0.get('detail') or '')[:110]}")
    ok = tally["wrong"]["not_supported"] >= 4 and tally["right"]["not_supported"] <= 1 and not failed
    print(f"wrong confirmed {tally['wrong']['not_supported']} of 7; right hit {tally['right']['not_supported']} of 7;"
          f" failed {failed}; Fable's test {'PASSED' if ok else 'NOT PASSED'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
