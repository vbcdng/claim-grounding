"""Card 397 (branch A): one answers file per step for the field set of card S2's field decision
(piece_only), with the 6 earlier-sentence pieces taken from branch A's own asks that give the
earlier sentence (orchestrating session's work order of 14 Nov 16:41). No label is read.

For each step: the base files give every piece except the 6; the "earlier" files give the 6 (and
any piece the base lacks). The last usable line of a (piece, repeat) wins; a failed line is kept
only when no usable line exists. Writes answers/<step>_po_e.jsonl and prints the counts.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAVED = HERE.parent / "saved_answers"
B = Path("./benchmarks/sprint_checker/branch_b/answers")
A = HERE / "answers"

STEPS = {
    # "earlier" files are read in order and the last usable line wins: branch B's asks of the same
    # missing pieces first (compare_b.py: the same prompt_sha1 on every piece both asked), branch A's own last
    "haiku": {"base": [SAVED / "haiku_piece_only.jsonl"],
              "earlier": [B / "haiku_piece_only_rest.jsonl", B / "haiku_six_earlier.jsonl",
                          A / "haiku_po_e_0.jsonl", A / "haiku_po_e_1.jsonl"]},
    "sonnet": {"base": [SAVED / "sonnet_piece_only_fieldtest.jsonl", SAVED / "sonnet_piece_only_p0.jsonl",
                        SAVED / "sonnet_piece_only_p1.jsonl"],
               "earlier": [B / "sonnet_piece_only_rest.jsonl", B / "sonnet_six_earlier.jsonl"]
               + [A / f"sonnet_po_e_{k}.jsonl" for k in range(3)]},
    # Haiku rewording 1 (questions/scope_strength_v1.md, fields_v1, so the 6 get the earlier sentence):
    # asked on the trial tenth and on every piece where Haiku or Sonnet said supported (pick_yes.py)
    "haiku_ss": {"base": [], "earlier": [A / "haiku_ss_v1.jsonl"] + [A / f"haiku_ss_v1_{k}.jsonl" for k in range(4)]},
    "sonnet_ss": {"base": [], "earlier": [A / f"sonnet_ss_v1_{k}.jsonl" for k in range(3)]},
    # branch B's paid Gemma piece_only job p184, read from branch B's working copy (a session may not
    # copy files there by hand); the merged file written here is the copy this branch commits
    "gemma_paid": {"base": [B / "gemma_paid_piece_only.jsonl"], "earlier": [A / "gemma_paid_earlier6.jsonl"]},
    # Jev: fields_v1 sends Jev the same input as piece_only plus the earlier sentence for every piece
    # whose certainty is "stated" (ask.jev_state); the hedged pieces come from branch B's job p185.
    "jev": {"base": [SAVED / "jev_fields_merged_check.jsonl"], "earlier": [B / "jev_piece_only_hedged.jsonl"]},
}


def lines(paths):
    for p in paths:
        if p.exists():
            with open(p, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        yield json.loads(line)


def merge(step, spec, earlier_ids):
    best = {}

    def put(r):
        k = (r["piece_id"], r["repeat"])
        if k not in best or best[k].get("failed") or not r.get("failed"):
            best[k] = r

    for r in lines(spec["base"]):
        if r["piece_id"] not in earlier_ids or step == "jev":
            put(r)
    over = set()
    for r in lines(spec["earlier"]):
        if step == "jev":
            if not r.get("failed"):
                best[(r["piece_id"], r["repeat"])] = r  # hedged pieces: the piece_only answer replaces fields_v1
                over.add(r["piece_id"])
        else:
            put(r)
    out = A / f"{step}_po_e.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for k in sorted(best):
            f.write(json.dumps(best[k], ensure_ascii=False) + "\n")
    pieces = {k[0] for k in best}
    failed = {k[0] for k, r in best.items() if r.get("failed") and k[1] == 0}
    print(f"{step}: {len(pieces)} pieces, {len(failed)} with a failed first answer, "
          f"{len(over) if step == 'jev' else len(pieces & earlier_ids)} from branch A's or B's own asks; {out.name}")


def main():
    earlier_ids = set((A / "earlier6.txt").read_text().split())
    for step, spec in STEPS.items():
        merge(step, spec, earlier_ids)
    return 0


if __name__ == "__main__":
    sys.exit(main())
