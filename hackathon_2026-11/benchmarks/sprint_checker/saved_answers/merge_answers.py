"""Card 396: merge the chunk files of one step into one answers file and count good and failed lines.

Usage: merge_answers.py OUT CHUNK [CHUNK ...]
A line is kept per (piece_id, repeat): a good line wins over a failed one, a later line over an earlier one.
"""
import json
import sys
from pathlib import Path


def main(out, chunks):
    best = {}
    for c in chunks:
        for line in Path(c).read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            key = (r["piece_id"], r.get("repeat", 0))
            old = best.get(key)
            if old is None or old.get("failed") or not r.get("failed"):
                best[key] = r
    with open(out, "w") as f:
        for r in best.values():
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    failed = sum(1 for r in best.values() if r.get("failed"))
    pieces = {k[0] for k in best}
    print(f"{out}: {len(best)} lines, {len(pieces)} pieces, {failed} failed")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
