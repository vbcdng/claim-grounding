# deep_check.py — stronger-model source-context audit (testing aid)

Owner request 2026-07-10: reviewing every claim card by hand is too slow. A
STRONGER model than the production judge (default `claude-code/sonnet`, $0
API on the Claude subscription) re-reads each judged claim **with source
context** and writes an independent verdict + **commentary** onto every card
in the viewer, so a human can skim instead of re-deriving each judgment.

## Why a stronger model, and only for testing
A same-strength checker shares the production judge's failure modes
(paraphrase, numeric inference) and mostly rubber-stamps it. A stronger model
(Sonnet vs the flash-lite production judge) can actually catch flash-lite's
mistakes. It stays OFF the production/gate path deliberately — this is an
audit lens, never a verdict source. **Two models agreeing ≠ verified**
(deprioritize for review); **disagreeing = look here first**.

## What it sees (more than `--second-opinion`)
`--second-opinion` re-reads the SAME evidence sentence → checks the *judge*.
deep_check also feeds the **source context**: the tool's evidence sentence,
its ±4-sentence window, and the most lexically-relevant ~1200-word chunk of
each cited source. So it also catches **retrieval misses** ("the paper
supports this three sentences later") and **out-of-context quotes**. It
ALWAYS comments (agree or disagree) and is told to quote the verbatim source
text its judgment rests on — so the commentary itself is spot-checkable in
seconds.

## Run
```
venv/bin/python3 deep_check.py <run_dir> [--model claude-code/sonnet] \
    [--workers 4] [--limit N] [--no-viewer]
```
Writes `<run_dir>/deep_check.json` and regenerates `viewer.html` with the
commentary injected (in-memory only — analysis.json verdicts are untouched).

## In the viewer
Every judged card gets a chip — `✓ deep check agrees` (blue) or
`⚠ deep check disagrees` (orange) — and a note with the model's commentary,
its anchoring quote, and any `better_sentence` it found. Never a veto; the
verdict above is unchanged.

## Not on the verdict path → no gate
deep_check.py is a standalone read-only consumer of a finished run; the only
shared-code change is the viewer's render block (additive) + CSS. The 3-paper
gate is about verdict-path changes; this touches none. Still runs on the full
suite (616 tests).

## Feeds decomp_bench
deep_check's per-claim verdict can populate the `L` (LLM) column of the
evidence-mode review files; the human's spot-check fills `H`; `metrics.py`
then reports agreement/kappa between the strong model and the human — the
loop the owner described.
