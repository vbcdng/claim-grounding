"""Card 405, round 8b: round 8 (chain.json) on the repaired cited works of card 398 (branch B).

Card 398 found 19 cited works stored as raw PDF bytes and turned them into text (day/398 commits
8a6dd927 and f29d49ad: branch_b/repaired_texts/, map.json, raw_pdf_works.json, answers/*_repaired.jsonl).
They are read from the day_398 working copy by full path (copying them in was refused). On the 24
pieces citing them (13 tuning, 11 held-back; none has an earlier sentence) every step round 8 reads
is replaced by answers given on the repaired texts; the old answers of those pieces are dropped, all
other pieces keep their old answers. Question files and field sets are unchanged. No label is read.

Steps of round 8 and where the new answers of the 24 pieces come from:
  h   Haiku neutral (neutral_v1, piece_only)     branch B haiku_repaired.jsonl (same question and field set)
  s   Sonnet neutral (same)                      branch B sonnet_repaired.jsonl + answers/sonnet_repaired_a.jsonl
  on  Opus neutral where s is unsure (same)      branch B opus_repaired.jsonl, kept only where the new s is unsure
  h2  Haiku reworded (scope_strength_v1, fields_v1) where h or s supported      answers/haiku_ss_repaired.jsonl
  s2  Sonnet reworded where h2 not supported and s not not supported           answers/sonnet_ss_repaired.jsonl
  o   Opus objection (objection_v1) on ask_objection.pick's pieces              answers/opus_objection_repaired.jsonl
  h2u Haiku reworded where s unsure, on supported and h2 never asked            answers/haiku_ss_unsure_repaired.jsonl

  repaired_r8b.py build                  join, write answers/r8b/*.jsonl and the todo lists answers/r8b_todo_<step>.txt
  repaired_r8b.py ask <ask.py args>      shared/ask.py with the repaired texts
  repaired_r8b.py objection <args>       ask_objection.py with the repaired texts and the r8b answer files
  repaired_r8b.py combine <combine.py args>   combine.py with the repaired texts (its quote checks read the works)
Score: repaired_r8b.py combine rounds/r8b_repaired.json --out <file>, then score.py <file> (tuning part).
Plain combine.py would check quotes of the 24 pieces against the raw PDF bytes.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

records = sibling("records")
ask = sibling("ask")

B_ROOT = Path(".")
B = B_ROOT / "benchmarks/sprint_checker/branch_b"
A = HERE / "answers"
OUT = A / "r8b"
OLD = {"h": "haiku_po_e.jsonl", "s": "sonnet_po_e.jsonl", "h2": "haiku_ss_po_e.jsonl",
       "s2": "sonnet_ss_po_e.jsonl", "o": "opus_objection.jsonl", "on": "opus_po_unsure_all.jsonl",
       "h2u": "haiku_ss_unsure.jsonl"}
NEW = {"h": [B / "answers/haiku_repaired.jsonl"],
       "s": [B / "answers/sonnet_repaired.jsonl", A / "sonnet_repaired_a.jsonl"],
       "on": [B / "answers/opus_repaired.jsonl"],
       "h2": [A / "haiku_ss_repaired.jsonl"], "s2": [A / "sonnet_ss_repaired.jsonl"],
       "o": [A / "opus_objection_repaired.jsonl"], "h2u": [A / "haiku_ss_unsure_repaired.jsonl"]}


def install():
    """Cited works listed in branch B's map are read from its repaired text files."""
    mapping = json.loads((B / "repaired_texts/map.json").read_text(encoding="utf-8"))
    old = records.work_text

    def work_text(rel):
        return old(str(B_ROOT / mapping[rel]) if rel in mapping else rel)

    records.work_text = work_text
    return mapping


def repaired_ids():
    listed = json.loads((B / "raw_pdf_works.json").read_text(encoding="utf-8"))
    return set(listed["tuning_pieces"]) | set(listed["held_back_pieces"])


def best_lines(paths, keep):
    """(piece, repeat) -> last usable line of the pieces in `keep`; a failed line only if none is usable."""
    best = {}
    for r in (r for p in paths for r in ask.read_lines(p)):
        if r["piece_id"] in keep:
            k = (r["piece_id"], r["repeat"])
            if k not in best or best[k].get("failed") or not r.get("failed"):
                best[k] = r
    return best


def first(best, pid):
    r = best.get((pid, 0))
    return None if r is None or r.get("failed") or r.get("answer") is None else r["answer"]


def write(step, rep, rep_ids):
    lines = [r for r in ask.read_lines(A / OLD[step]) if r["piece_id"] not in rep_ids]
    lines += [rep[k] for k in sorted(rep)]
    OUT.mkdir(exist_ok=True)
    with open(OUT / OLD[step], "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in lines)


def todo(name, ids, have):
    left = sorted(set(ids) - {k[0] for k, r in have.items() if not r.get("failed")})
    (A / f"r8b_todo_{name}.txt").write_text("".join(p + "\n" for p in left), encoding="utf-8")
    print(f"  {name}: {len(ids)} pieces should have an answer, {len(left)} still to ask -> answers/r8b_todo_{name}.txt")


def build():
    ids = repaired_ids()
    rep = {k: best_lines(v, ids) for k, v in NEW.items()}
    s = {p: first(rep["s"], p) for p in ids}
    h = {p: first(rep["h"], p) for p in ids}
    rep["on"] = {k: r for k, r in rep["on"].items() if s[k[0]] == "unsure"}
    want_h2 = [p for p in ids if "supported" in (h[p], s[p])]
    h2 = {p: first(rep["h2"], p) for p in ids}
    want_s2 = [p for p in want_h2 if h2[p] == "not_supported" and s[p] != "not_supported"]
    rep["s2"] = {k: r for k, r in rep["s2"].items() if k[0] in want_s2}
    on = {p: first(rep["on"], p) for p in ids}
    want_h2u = [p for p in ids if s[p] == "unsure" and on[p] == "supported" and p not in want_h2]
    print(f"{len(ids)} repaired pieces; new first answers: h {sum(v is not None for v in h.values())}, "
          f"s {sum(v is not None for v in s.values())} (unsure {sum(v == 'unsure' for v in s.values())}), "
          f"on {sum(v is not None for v in on.values())}")
    for step in OLD:
        write(step, rep[step], ids)
    todo("s", ids, rep["s"])
    todo("h2", want_h2, rep["h2"])
    todo("s2", want_s2, rep["s2"])
    todo("h2u", want_h2u, rep["h2u"])
    obj = sibling_objection()
    picks = obj.pick(obj.load_all(), records.load_pieces())
    want_o = [p for p in picks if p in ids]
    stale = sorted({k[0] for k in rep["o"]} - set(want_o))
    if stale:
        print(f"  o: answers for pieces no longer picked are left out: {stale}")
    rep["o"] = {k: r for k, r in rep["o"].items() if k[0] in want_o}
    write("o", rep["o"], ids)
    todo("o", want_o, rep["o"])
    return 0


def sibling_objection():
    sys.path.insert(0, str(HERE))
    import ask_objection
    ask_objection.ANSWERS = {k: str(OUT / OLD[k]) for k in ("h", "s", "h2", "s2")}
    return ask_objection


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["build"]:
        return build()
    install()
    if argv[:1] == ["ask"]:
        return ask.main(argv[1:])
    if argv[:1] == ["objection"]:
        return sibling_objection().main(argv[1:])
    if argv[:1] == ["combine"]:          # the chain's quote checks read the repaired texts too
        return sibling("combine").main(argv[1:])
    raise SystemExit(__doc__)


if __name__ == "__main__":
    sys.exit(main())
