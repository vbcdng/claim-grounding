"""Card 398 (branch B): Fable's third answer, check before change 3. For the pieces a decisions file
leaves not checked or undecided, print per step how the work was given (whole or passages), the input
size and the raw reply, to see whether "unsure" is the model's own answer or a sign of a cut-off text.
Usage: longlook.py DECISIONS [STATUS ...]   (default statuses: not_checked)"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEPS = ["gemma", "haiku", "sonnet", "opus_neutral"]


def load(p):
    out = {}
    for x in Path(p).read_text(encoding="utf-8").splitlines():
        if x.strip():
            d = json.loads(x)
            if d.get("repeat", 0) == 0:
                out[d["piece_id"]] = d
    return out


def main():
    dec = [json.loads(x) for x in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines() if x.strip()]
    want = set(sys.argv[2:]) or {"not_checked"}
    ans = {s: load(HERE / "answers" / f"{s}.jsonl") for s in STEPS}
    for d in dec:
        if d.get("answer") not in want:
            continue
        pid = d["piece_id"]
        print(f"== {pid} decision={d.get('answer')} rule={d.get('step')}")
        for s in STEPS:
            a = ans[s].get(pid)
            if a is None:
                print(f"  {s}: no answer")
                continue
            print(f"  {s}: {a.get('answer')} given={a.get('given')} in={a.get('input_tokens')} "
                  f"failed={a.get('failed')} reply={str(a.get('reply'))[:160]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
