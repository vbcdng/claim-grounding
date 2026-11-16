"""Card 398: decide the tuning part with a rule file for each value of its fitted parameter, and print
the three numbers (all checked pieces) per value. A look at the whole curve, not a fit.
Usage: scan.py RULE"""
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

combine = sibling("combine")
records = sibling("records")
score = sibling("score")

rule_path = Path(sys.argv[1])
rule = json.loads(rule_path.read_text(encoding="utf-8"))
chain = combine.Chain(rule, rule_path.parent)
labels = records.load_labels(final=False)
ids = [p for p in chain.piece_ids() if p in labels]
fit = rule["fit"]
for v in fit["grid"]:
    dec = {p: chain.decide(p, {fit["param"]: v}) for p in ids}
    r = score.rates(ids, labels, dec)
    steps = Counter(d["step"] for d in dec.values())
    print(f"{fit['param']}={v}: wrongly passed {r['wrongly_passed'][0]}/{r['wrongly_passed'][1]}, wrongly rejected "
          f"{r['wrongly_rejected'][0]}/{r['wrongly_rejected'][1]}, undecided {r['undecided'][0]}/{r['n']}; {dict(steps)}")
