# Branch B of the sprint checker 

Branch B is the chain of model steps that guards the answer "not supported": it tries to avoid rejecting a piece that is in fact right.

The frozen chain is `chain.json`: round 12 (`drafts/r12.json`), frozen on 15 November 2026. On the 150 tuning pieces, with the cited texts repaired (`repaired_texts.md`), it wrongly rejects 3 of 75 right pieces, wrongly passes 14 of 75 wrong pieces and leaves 23 of 150 undecided. Sonnet alone, on the same answers, gives 13, 14 and 8. Why round 12 and not round 9 is written in `rounds.md`, section "Frozen choice".

To get the chain's decisions on the tuning part:

`./venv/bin/python3 benchmarks/sprint_checker/branch_b/combine_chain.py --chain benchmarks/sprint_checker/branch_b/chain.json`

This runs the shared program `shared/combine.py`, which takes the chain file as its first argument (it has no `--chain` option of its own), and writes `chain.decisions.jsonl`. Score it with `shared/score.py`. Every round scored is one row of `rounds.md`; the card's notes are in `docs/architecture_cards/card398.md`.
