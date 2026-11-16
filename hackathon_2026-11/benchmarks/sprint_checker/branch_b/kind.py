"""Card 398 (branch B): own question 2, "what kind of statement is the piece" (questions/kind_v1.md;
change 2 of fable_answer.md). Runs shared/ask.py unchanged except for the reply reader: the answer
field holds the kind A to D (A the work's finding, B the writer's judgement, C another work or several,
D not understandable alone). Tuning part only (split.allowed_ids()), field set fields_v1_orig, no works
in the prompt.

  kind.py --step haiku [--limit N]     ask (a restart skips saved answers); out answers/<step>_kind_v1.jsonl
load_kinds(path) gives {piece_id: kind} for the scoring scripts.
"""
import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

ask = sibling("ask")
split = sibling("split")
QUESTION = HERE / "questions" / "kind_v1.md"
IDS = HERE / "answers" / "tuning_ids.txt"
KINDS = ("A", "B", "C", "D")


def parse_kind(text):
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    k = str(d.get("kind", "")).strip().upper()[:1] if isinstance(d, dict) else ""
    if k not in KINDS:
        return None
    return {"answer": k, "quote": "", "reason_kind": None, "detail": str(d.get("why") or ""), "sureness": None}


def write_ids(limit=None, every=False):
    # every=True: both parts (the frozen chain needs the kind on the held-back part for card F's one
    # final score); this reads ids only, never a label.
    ids = sorted(split.allowed_ids(final=every))
    if limit:
        ids = ids[:limit]
    path = IDS.with_name("all_ids.txt") if every else IDS
    path.write_text("\n".join(ids) + "\n", encoding="utf-8")
    return len(ids), path


def load_kinds(path):
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r.get("repeat", 0) == 0 and not r.get("failed"):
            out[r["piece_id"]] = r["answer"]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", default="haiku", choices=["haiku", "sonnet", "stand_in"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out")
    ap.add_argument("--all", action="store_true", help="all 300 pieces (tuning and held-back)")
    a = ap.parse_args(argv)
    n, ids = write_ids(a.limit, a.all)
    out = a.out or str(HERE / "answers" / f"{a.step}_kind_v1.jsonl")
    ask.parse_reply = parse_kind
    print(f"{n} pieces ({'both parts' if a.all else 'tuning part'}); question {QUESTION.name}; out {out}")
    return ask.main(["--step", a.step, "--question", str(QUESTION), "--field-set", "fields_v1_orig",
                     "--pieces", str(ids), "--out", out])


if __name__ == "__main__":
    sys.exit(main())
