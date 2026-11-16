"""Card S1 (card 395): compare two answer files of the field test (card S2), per field group.

Without labels (the default): for every piece both files answered (first repeat, call not
failed), how many answers differ, per field group (certainty possible or likely; an earlier
sentence stored; all others). This reads no label, so it uses every piece (plan page: "how many
answers change between two runs needs no label").
With --labels: also the mistakes of each file per field group, on the tuning part only (a wrong
piece answered "supported", a right piece answered "not supported"; "unsure" counted apart).

Run: ./venv/bin/python3 benchmarks/sprint_checker/shared/compare_fields.py A.jsonl B.jsonl [--only-field-test] [--labels]
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from sprint_shared_load import sibling  # noqa: E402

ask = sibling("ask")
records = sibling("records")
split = sibling("split")

GROUPS = ("certainty possible or likely", "earlier sentence stored", "all others")


def field_groups(rec):
    out = []
    if rec.get("certainty") in split.HEDGED:
        out.append(GROUPS[0])
    if rec.get("earlier_sentence"):
        out.append(GROUPS[1])
    return out or [GROUPS[2]]


def first_answers(answers):
    out = {}
    for pid, reps in answers.items():
        r0 = next((r for r in reps if r["repeat"] == 0), None)
        if r0 and not r0.get("failed") and r0.get("answer") is not None:
            out[pid] = r0["answer"]
    return out


def differ(path_a, path_b, only=None):
    pieces = {r["piece_id"]: r for r in records.load_pieces()}
    a = first_answers(ask.load_answers(path_a, label_free=True))
    b = first_answers(ask.load_answers(path_b, label_free=True))
    both = sorted(set(a) & set(b) & (set(only) if only else set(a)))
    n, d = Counter(), Counter()
    for p in both:
        for g in field_groups(pieces[p]):
            n[g] += 1
            d[g] += a[p] != b[p]
    return n, d


def mistakes(path, split_path=split.SPLIT, only=None):
    labels = records.load_labels(final=False, split_path=split_path)
    ans = first_answers(ask.load_answers(path, split_path=split_path))
    out = {g: Counter() for g in GROUPS}
    for p, lab in labels.items():
        if only and p not in only or p not in ans:
            continue
        for g in field_groups(lab):
            c = out[g]
            c["pieces"] += 1
            if lab["label"] == "not_supported" and ans[p] == "supported":
                c["wrongly passed"] += 1
            elif lab["label"] == "supported" and ans[p] == "not_supported":
                c["wrongly rejected"] += 1
            elif ans[p] == "unsure":
                c["unsure"] += 1
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Card S1: how many answers differ between two answer files, per "
                                             "field group (no labels); with --labels also mistakes on the "
                                             "tuning part.")
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--only-field-test", action="store_true", help="only the 65 pieces of field_test_ids.json")
    ap.add_argument("--labels", action="store_true", help="also mistakes per field group, tuning part only")
    a = ap.parse_args(argv)
    only = None
    if a.only_field_test:
        wrong_fields = sibling("wrong_fields")
        only = wrong_fields.load_ids()["pieces"]
    n, d = differ(a.a, a.b, only)
    print(f"answers that differ between {Path(a.a).name} and {Path(a.b).name} (first repeat, no label read):")
    for g in GROUPS:
        print(f"  {g}: {d[g]} of {n[g]} pieces differ")
    if a.labels:
        for path in (a.a, a.b):
            print(f"mistakes of {Path(path).name}, tuning part only:")
            for g, c in mistakes(path, only=only).items():
                print(f"  {g}: {c['pieces']} pieces; wrongly passed {c['wrongly passed']}, "
                      f"wrongly rejected {c['wrongly rejected']}, unsure {c['unsure']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
