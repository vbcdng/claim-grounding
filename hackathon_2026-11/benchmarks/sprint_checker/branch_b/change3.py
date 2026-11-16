"""Card 398 (branch B): Fable's change 3 (fable_answer.md). On the differs_v1 answers already in:
how often its "unsure" (or a failed/no-close-sentence answer) falls on the right pieces a round still
rejects versus the wrong pieces it rejects correctly; and which pieces still lack a differs_v1 answer
although some model said not supported (the only pieces where its answer can matter).
Tuning part labels only through records.load_labels. Usage: change3.py DECISIONS [--write-rest FILE]"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

records = sibling("records")
score = sibling("score")
A = HERE / "answers"


def first(path, key="answer"):
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        if d.get("repeat", 0) == 0:
            out[d["piece_id"]] = None if d.get("failed") else d.get(key)
    return out


def main():
    dec = score.read_decisions(sys.argv[1])
    labels = records.load_labels(final=False)
    d = first(A / "gemma_differs_v1.jsonl")
    rej = {k: first(A / f"{k}.jsonl") for k in ("gemma", "haiku", "sonnet")}
    groups = {"right pieces rejected": [], "wrong pieces rejected": []}
    for p, x in dec.items():
        if p in labels and x.get("answer") == "not_supported":
            groups["right pieces rejected" if labels[p]["label"] == "supported" else "wrong pieces rejected"].append(p)
    for name, ps in groups.items():
        have = [p for p in ps if p in d]
        unsure = [p for p in have if d[p] in ("unsure", None)]
        sup = [p for p in have if d[p] == "supported"]
        print(f"{name}: {len(ps)}; differs_v1 answered {len(have)}; unsure or failed {len(unsure)}; supported {len(sup)}"
              + (f" {sorted(unsure + sup)}" if name.startswith("right") else ""))
    all_ids = {r["piece_id"] for r in records.load_pieces()}
    any_rej = {p for p in all_ids if any(rej[k].get(p) == "not_supported" for k in rej)}
    rest = sorted(p for p in any_rej if p not in d)
    print(f"all 300: some model said not supported on {len(any_rej)}; of these {len(rest)} have no differs_v1 answer yet; "
          f"pieces without one in all: {len(all_ids - set(d))}")
    if "--write-rest" in sys.argv:
        Path(sys.argv[sys.argv.index("--write-rest") + 1]).write_text("\n".join(rest) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
