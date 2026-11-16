# Sprint checker (two-branch claim grounder)

This version was made during the Apart Research hackathon (November 2026). Code only; the benchmark is not included.

## What it does
It checks small claim pieces (AIDA format, one statement each) against the work they cite.
- Branch A guards the answer "supported": it tries not to pass a piece that is wrong.
- Branch B guards the answer "not supported": it tries not to reject a piece that is right.
Both are escalating chains: a cheap model answers first, a stronger model is asked when the answer is unsure.
The chains are frozen in `benchmarks/sprint_checker/branch_a/chain.json` and `branch_b/chain.json`.

## Folder map
- `benchmarks/sprint_checker/shared/` : prompt template (`questions/neutral_v1.md`), field sets, asking models (`ask.py`), combining a chain (`combine.py`), scoring (`score.py`), quote check, split.
- `benchmarks/sprint_checker/branch_a/`, `branch_b/` : chain files, per-branch scripts, round notes.
- `benchmarks/sprint_checker/label_review/`, `saved_answers*/` : scripts and notes only; data removed.
- `benchmarks/card390/` : scripts and instructions used to cut claims into pieces and label them.

## How to run
Examples taken from the file docs:
- Branch B: `python3 benchmarks/sprint_checker/branch_b/combine_chain.py --chain benchmarks/sprint_checker/branch_b/chain.json`
- Branch A: `python3 benchmarks/sprint_checker/branch_a/repaired_r8b.py combine benchmarks/sprint_checker/branch_a/chain.json --out <decisions file>`
- Score: `shared/score.py`.
The scripts need data files (answers, split, cited works) that are not included, so they will not run end to end without your own data.

## Dependencies
Python 3 and the claim-grounding tool (`modules/papertrail/...`: `long_source`, `claude_code_backend`, `jev_client`, `aida.record`; also `card160`, `card183_haiku_arbiter`, `ladder_ask` from its benchmarks folder). Needs claim-grounding at commit a4106c0a (private history; use the current public release). Models are called through the Claude Code subscription backend, a Gemma key and a Jev account where used.

## Licence
MIT, same as the main repository (see `LICENSE`).
