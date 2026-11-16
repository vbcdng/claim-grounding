"""Card 397 (branch A): which of branch A's still-missing asks branch B already answered with the
SAME prompt (same prompt_sha1), so they need not be asked twice. No label is read.

  compare_b.py            prints, per step, how many of branch A's todo pieces B has with an equal prompt
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
A = HERE / "answers"
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

ask = sibling("ask")
B = Path("./benchmarks/sprint_checker/branch_b/answers")


def read(paths):
    out = {}
    for p in paths:
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    if r.get("repeat", 0) == 0 and not r.get("failed"):
                        out[r["piece_id"]] = r
    return out


def main():
    for step, mine, theirs, todo in (
            ("haiku", [A / "haiku_po_e_0.jsonl", A / "haiku_po_e_1.jsonl"],
             [B / "haiku_piece_only_rest.jsonl", B / "haiku_six_earlier.jsonl"], A / "haiku_todo.txt"),
            ("sonnet", [A / f"sonnet_po_e_{k}.jsonl" for k in range(3)],
             [B / "sonnet_piece_only_rest.jsonl", B / "sonnet_six_earlier.jsonl"], A / "sonnet_todo.txt")):
        a, b = read(mine), read(theirs)
        want = set(todo.read_text().split())
        both = [p for p in want if p in a and p in b]
        same = [p for p in both if a[p]["prompt_sha1"] == b[p]["prompt_sha1"]]
        ans = lambda r: (ask.parse_reply(r["reply"]) or {}).get("answer")  # noqa: E731
        agree = [p for p in same if ans(a[p]) == ans(b[p])]
        b_only = [p for p in want if p in b and p not in a]
        print(f"{step}: todo {len(want)}, A has {len(want & set(a))}, B has {len(want & set(b))}; "
              f"both {len(both)}, same prompt {len(same)}, same answer {len(agree)}; B only {len(b_only)}")
        if "--ids" in sys.argv:
            print(" B only:", " ".join(sorted(b_only)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
