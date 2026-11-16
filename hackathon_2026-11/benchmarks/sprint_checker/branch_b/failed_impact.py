"""Card 398 (branch B): which pieces with a failed own-question-1 (differs_v1) Gemma answer could change
round 2's decision once asked again. Tuning part only (labels through records.load_labels).
A piece can change when: Sonnet rejects it and Jev is at the lowest fitted u or above (a supported answer
from Gemma could veto the rejection), or it is not already rejected and Jev is under the highest fitted t
(a not-supported answer could reject it early). Usage: failed_impact.py R2_DECISIONS R5_DECISIONS"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

records = sibling("records")
score = sibling("score")
U_LOW, T_HIGH = 0.3, 0.25  # lowest u and highest t fitted in any fold of round 2


def first(step, key):
    out = {}
    for line in (HERE / "answers" / f"{step}.jsonl").read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        if d.get("repeat", 0) == 0:
            out[d["piece_id"]] = d if key is None else d.get(key)
    return out


def main():
    r2, r5 = score.read_decisions(sys.argv[1]), score.read_decisions(sys.argv[2])
    labels = records.load_labels(final=False)
    d, s, j = first("gemma_differs", None), first("sonnet", "answer"), first("jev", "jev_score")
    failed = sorted(p for p, a in d.items() if a.get("failed"))
    tun = [p for p in failed if p in labels]
    print(f"failed differs_v1 answers: {len(failed)}; in the tuning part: {len(tun)}")
    can = 0
    for p in tun:
        jv = j.get(p)
        veto = s.get(p) == "not_supported" and jv is not None and jv >= U_LOW
        early = r2[p].get("answer") != "not_supported" and (jv is None or jv < T_HIGH)
        can += veto or early
        print(p, labels[p]["label"], "r2:", r2[p].get("answer"), r2[p].get("step"), "r5:", r5[p].get("answer"),
              "sonnet:", s.get(p), "jev:", jv, "CAN CHANGE" if veto or early else "")
    print(f"could change round 2's decision: {can} of {len(tun)}")


if __name__ == "__main__":
    main()
