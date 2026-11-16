"""Card S1 (card 395): what a step is given for one piece of round 1, and the label loader.

- load_pieces(): the 300 pieces of benchmarks/card390/round1.jsonl WITHOUT any label field
  (asking needs every piece; a prompt never needs a label).
- load_labels(final=False): labels of the tuning part only, unless final=True (split.allowed_ids).
- step_input(rec, set_name, step): the one place a prompt's content is chosen. It copies only the
  fields of the named field set (field_sets.json); a set naming a field of never_shown stops the
  program. Step 0 without a model: a piece whose attribution is the writer's own is "skipped",
  a piece with a missing or unreadable cited work is "not_checked".
- Long works: the whole text when all cited works fit the step's limit (STEP_LIMIT_CHARS),
  otherwise the passages the tool's own word search ranks highest (long_source.split_into_pieces
  + rank_piece_indices, the IDF-weighted overlap of matcher._lex_scores), shared out over the
  works and put back in their order. Every input records per work "whole" or "passages".
- render(template, inp): fills a question file (questions/*.md).

`./venv/bin/python3 benchmarks/sprint_checker/shared/records.py sizes` prints the size table of
the cited works against every step's limit.
"""
import argparse
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from sprint_shared_load import sibling  # noqa: E402

split = sibling("split")

ROUND1 = split.ROUND1
FIELD_SETS = HERE / "field_sets.json"
LABEL_FIELDS = ("label", "label_kind", "label_quote", "label_reason", "label_source", "claim_label")
WRITER_OWN = "writer_own"

# Characters of cited-work text one prompt may carry, per step. gemma_free: the free Google seat
# refuses requests over about 52,000 characters (long_source.DEFAULT_PIECE_CHARS = 40,000 leaves
# room for the question). jev: about 32,000 tokens at about 4 characters a token, minus room.
# gemma_paid: a cost cap, not the model's window (the median piece cites 46,000 characters).
# Claude steps: well inside a 200,000-token window. Changing a number changes the prompt sha1,
# so a saved answer is asked again.
STEP_LIMIT_CHARS = {"stand_in": 40_000, "gemma_free": 40_000, "gemma_paid": 120_000,
                    "jev": 100_000, "haiku": 400_000, "sonnet": 400_000, "opus": 400_000}
PASSAGE_CHARS = 2_000
PASSAGE_OVERLAP = 200


class FieldSetRefused(SystemExit):
    pass


def _read(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_pieces(path=ROUND1):
    """All pieces, label fields removed."""
    return [{k: v for k, v in r.items() if k not in LABEL_FIELDS} for r in _read(path)]


def load_labels(final=False, path=ROUND1, split_path=split.SPLIT):
    """piece_id -> label record (label fields and the grouping fields), tuning part only
    unless final=True."""
    ok = split.allowed_ids(final, split_path)
    keep = LABEL_FIELDS + ("row_id", "door", "tags", "separate_groups", "certainty", "earlier_sentence",
                           "attribution", "kind")
    return {r["piece_id"]: {k: r.get(k) for k in keep} for r in _read(path) if r["piece_id"] in ok}


def field_sets(path=FIELD_SETS):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_field_set(name, path=FIELD_SETS):
    fs = field_sets(path)
    if name not in fs["sets"]:
        raise FieldSetRefused(f"refused: no field set {name!r}; known: {sorted(fs['sets'])}")
    s = fs["sets"][name]
    bad = sorted(set(s["fields"]) & set(fs["never_shown"]))
    if bad:
        raise FieldSetRefused(f"refused: field set {name!r} names {bad}, which no prompt may hold "
                              "(never_shown in field_sets.json)")
    if "piece" not in s["fields"]:
        raise FieldSetRefused(f"refused: field set {name!r} has no piece")
    return s


def step0(rec):
    """The answer of step 0 (no model), or None when the piece goes to a model."""
    if rec.get("attribution") == WRITER_OWN:
        return "skipped"
    works = rec.get("cited_works") or []
    if not works:
        return "not_checked"
    for w in works:
        if not w.get("available") or not w.get("file") or not (REPO / w["file"]).is_file():
            return "not_checked"
        if not work_text(w["file"]).strip():
            return "not_checked"
    return None


@lru_cache(maxsize=512)
def work_text(rel):
    return (REPO / rel).read_text(encoding="utf-8", errors="replace")


def passages(piece, text, budget):
    """The best-ranked passages of one work that fit `budget` characters, in work order."""
    from modules.papertrail import long_source
    parts = long_source.split_into_pieces(text, PASSAGE_CHARS, PASSAGE_OVERLAP)
    chosen, used = [], 0
    for i in long_source.rank_piece_indices(piece, parts):
        n = len(parts[i]) + 7
        if used + n > budget:
            continue
        chosen.append(i)
        used += n
    return "\n[...]\n".join(parts[i] for i in sorted(chosen))


def fit_works(piece, works, limit):
    """[{key, text, given}] for the cited works within `limit` characters in all."""
    texts = [work_text(w["file"]) for w in works]
    if sum(len(t) for t in texts) <= limit:
        return [{"key": w["key"], "text": t, "given": "whole"} for w, t in zip(works, texts)]
    # works that fit their equal share stay whole; the rest share what is left
    share = limit // len(works)
    small = [len(t) <= share for t in texts]
    left = limit - sum(len(t) for t, s in zip(texts, small) if s)
    n_big = sum(1 for s in small if not s)
    out = []
    for w, t, s in zip(works, texts, small):
        if s:
            out.append({"key": w["key"], "text": t, "given": "whole"})
        else:
            out.append({"key": w["key"], "text": passages(piece, t, left // n_big), "given": "passages"})
    return out


def step_input(rec, set_name, step, wrong=None):
    """What `step` is given for one piece under field set `set_name`. Only the fields of the
    set are copied; the rest of the record never leaves this function."""
    fs = load_field_set(set_name)
    out = {"piece_id": rec["piece_id"], "field_set": set_name, "step0": step0(rec), "fields": {}, "works": []}
    if out["step0"]:
        return out
    values = {f: rec.get(f) for f in fs["fields"] if f != "cited_works"}
    if fs.get("wrong_values"):
        if wrong is None:
            wrong_fields = sibling("wrong_fields")
            wrong = wrong_fields.wrong_values()
        if rec["piece_id"] not in wrong:
            raise FieldSetRefused(f"refused: {rec['piece_id']} is not one of the field-test pieces; "
                                  f"field set {set_name!r} has wrong values only for them")
        for f, v in wrong[rec["piece_id"]].items():
            if f in values:
                values[f] = v
    out["fields"] = {f: v for f, v in values.items() if v not in (None, "")}
    if "cited_works" in fs["fields"]:
        out["works"] = fit_works(rec["piece"], rec["cited_works"], STEP_LIMIT_CHARS[step])
    return out


_BLOCK = re.compile(r"\{\{#(\w+)\}\}\n?(.*?)\{\{/\1\}\}\n?", re.S)
_COMMENT = re.compile(r"\A<!--.*?-->\n", re.S)


def load_question(path):
    return _COMMENT.sub("", Path(path).read_text(encoding="utf-8"), count=1)


def render_works(works):
    n = len(works)
    blocks = []
    for i, w in enumerate(works, 1):
        head = f"CITED WORK {i} of {n}:"
        if w["given"] == "passages":
            head = (f"CITED WORK {i} of {n} (too long to show whole: the passages a word search found "
                    "closest to the statement, in their order; [...] marks a gap):")
        blocks.append(f"{head}\n{w['text'].strip()}")
    return "\n\n".join(blocks)


def render(template, inp):
    """Fill a question file for one step input. Values are put in last, so text inside a
    field or a work can never open or close a block."""
    fields = inp["fields"]
    text = _BLOCK.sub(lambda m: m.group(2) if fields.get(m.group(1)) else "", template)
    names = set(re.findall(r"\{\{(\w+)\}\}", text))
    unknown = names - set(fields) - {"works"}
    if unknown:
        raise ValueError(f"question file needs {sorted(unknown)}, which this field set does not give")
    out = []
    for chunk in re.split(r"(\{\{\w+\}\})", text):
        m = re.fullmatch(r"\{\{(\w+)\}\}", chunk)
        if not m:
            out.append(chunk)
        elif m.group(1) == "works":
            out.append(render_works(inp["works"]))
        else:
            out.append(str(fields[m.group(1)]))
    return "".join(out)


def size_table(recs=None):
    recs = recs if recs is not None else load_pieces()
    rows = []
    for step, lim in STEP_LIMIT_CHARS.items():
        if step == "stand_in":
            continue
        over = [r for r in recs if not step0(r)
                and sum(len(work_text(w["file"])) for w in r["cited_works"]) > lim]
        rows.append((step, lim, len(over)))
    totals = sorted(sum(len(work_text(w["file"])) for w in r["cited_works"]) for r in recs if not step0(r))
    return rows, totals


def main(argv=None):
    ap = argparse.ArgumentParser(description="Card S1: size table of the cited works, or one prompt.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sizes")
    p = sub.add_parser("prompt", help="print the prompt of one piece")
    p.add_argument("piece_id")
    p.add_argument("--field-set", default="fields_v1")
    p.add_argument("--step", default="sonnet")
    p.add_argument("--question", default=str(HERE / "questions" / "neutral_v1.md"))
    p.add_argument("--shorten", type=int, default=0, help="cut each work to this many characters")
    a = ap.parse_args(argv)
    if a.cmd == "sizes":
        rows, totals = size_table()
        n = len(totals)
        print(f"{n} pieces go to a model; cited text per piece (all its works), characters:")
        for q in (0.1, 0.25, 0.5, 0.75, 0.9):
            print(f"  {int(q * 100)}% of pieces at or under {totals[min(n - 1, int(q * n))]:,}")
        print(f"  largest {totals[-1]:,}")
        for step, lim, over in rows:
            print(f"  {step}: limit {lim:,}; {over} of {n} pieces get passages, {n - over} the whole text")
        return 0
    rec = next(r for r in load_pieces() if r["piece_id"] == a.piece_id)
    inp = step_input(rec, a.field_set, a.step)
    if a.shorten:
        for w in inp["works"]:
            if len(w["text"]) > a.shorten:
                w["text"] = w["text"][:a.shorten] + f" [... shortened here; {len(w['text']):,} characters given]"
    print(render(load_question(a.question), inp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
