"""Card 398: run shared/combine.py with a --chain option (shared/combine.py takes the rule file as its
first argument and has no --chain; changing it is card S1's job, outside this branch's folders).

  ./venv/bin/python3 benchmarks/sprint_checker/branch_b/combine_chain.py --chain benchmarks/sprint_checker/branch_b/chain.json
Default --out: <chain>.decisions.jsonl beside the chain file. Other options (--folds, --pieces) pass through.
"""
import argparse
import subprocess
import sys
from pathlib import Path

COMBINE = Path(__file__).resolve().parent.parent / "shared" / "combine.py"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--chain", required=True, help="chain (rule) file, JSON")
    ap.add_argument("--out", help="decisions file (jsonl); default <chain stem>.decisions.jsonl")
    args, rest = ap.parse_known_args(argv)
    if "--final" in rest:
        sys.exit("--final is card F's alone; not passed through")
    chain = Path(args.chain)
    out = args.out or str(chain.with_name(chain.stem + ".decisions.jsonl"))
    return subprocess.call([sys.executable, str(COMBINE), str(chain), "--out", out, *rest])


if __name__ == "__main__":
    sys.exit(main())
