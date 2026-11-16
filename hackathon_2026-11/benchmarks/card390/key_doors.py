"""Card 390: the answer key's door counts after Fable 5.2's changes of 13 Nov (D1848).

Changes to card 389's counting (benchmarks/card389/count_piece_types.py, which stays as it was):
- kind_overrides.jsonl: the four pool_0429 pieces ("The book examines ...") and piece k36 of the
  sample page (pool_0019_twin.r0.p1) are facts, not announcement / opinion.
- door rule 1: a pointer or an announcement is skipped only when it is the writer's own; a credited
  one is checked like any other credited piece.
- two-reading pieces: one record per distinct text (count of distinct texts printed).
The answer key and kind_tags.jsonl are only read.
Run: ./venv/bin/python3 benchmarks/card390/key_doors.py
"""
import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "card389"))
import count_piece_types as cpt  # noqa: E402

OVERRIDES = HERE / "kind_overrides.jsonl"


def load_kinds():
    kinds = cpt.load_kinds()
    for line in open(OVERRIDES):
        if line.strip():
            o = json.loads(line)
            if kinds.get(o["id"]) != o["was"]:
                raise SystemExit("override %s: tag file says %r, override expects %r"
                                 % (o["id"], kinds.get(o["id"]), o["was"]))
            kinds[o["id"]] = o["kind"]
    return kinds


def door(f):
    """Card 389's door with Fable's rule 1: pointer/announcement skipped only when the writer's own."""
    if "writer_own" in f:
        return "skipped"
    if f & cpt.TAG_FEATURES:
        return "tagged"
    return "in_scope"


def count(rows, kinds):
    doors = collections.Counter()
    moved = collections.Counter()
    old_kinds = cpt.load_kinds()
    texts = collections.defaultdict(set)
    n_two = 0
    for row, p in cpt.pieces(rows):
        f = cpt.features(row, p, kinds.get(p["id"]))
        d = door(f)
        doors[d] += 1
        old = cpt.door(cpt.features(row, p, old_kinds.get(p["id"])))
        if old != d:
            moved["%s -> %s" % (old, d)] += 1
        if "two_readings" in f:
            n_two += 1
            texts[row["entry_id"]].add(p["text"].strip())
    return doors, moved, n_two, sum(len(v) for v in texts.values())


def main():
    rows = cpt.load_key()
    kinds = load_kinds()
    doors, moved, n_two, n_distinct = count(rows, kinds)
    for d in cpt.DOORS:
        print("%5d  door %s" % (doors[d], d))
    print("%5d  total" % sum(doors.values()))
    print("pieces whose door changed from card 389:", dict(moved))
    print("two-reading pieces: %d, distinct texts (records sent): %d" % (n_two, n_distinct))
    print("kinds after overrides:", dict(collections.Counter(kinds.values())))


if __name__ == "__main__":
    main()
