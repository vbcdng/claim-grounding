# own_split_demo — a fast fixture for the own-split "citation needed?" chip

A ~6-sentence essay that exercises every uncited-claim class in seconds, so the
amber **"citation needed?"** chip (own-split, `modules/papertrail/own_claims.py`)
is visible without a large run. Also a permanent regression fixture for own-split.

The six sentences, and what each should produce:

| # | sentence | verdict | own-split tag | chip |
|---|----------|---------|---------------|------|
| 1 | "This essay argues that protecting sleep is the most underrated lever…" | own | structural (thesis) | none |
| 2 | "Consider the underlying mechanism." | own | structural (transition) | none |
| 3 | "During deep sleep the brain clears metabolic waste through the glymphatic system…" `[[sleep]]` | supported | — (cited) | confidence chip |
| 4 | "Adults who sleep fewer than six hours a night score roughly 30% worse…" | own | **fact** | **citation needed?** |
| 5 | "In my experience, the mid-afternoon slump is the clearest signal…" | own | opinion | none |
| 6 | "Therefore, guarding a full night's sleep is not an indulgence but…" | own | structural (conclusion) | none |

Sentence 4 is the point of the fixture: a bare, checkable, uncited fact that
own-split should tag `fact` → amber "citation needed?" nudge (never a verdict).

## Run it

Fast + free on the Haiku backend (own-split is one tiny call per uncited claim):

```
verify_my_text.py \
  --text examples/own_split_demo/my_text.md \
  --references examples/own_split_demo/my_text.md.refs.txt \
  --sources examples/own_split_demo/sources \
  --output-dir data/own_split_demo \
  --backend claude-code --open
```

Or on the Gemini default (drop `--backend claude-code`, add `--yes`).
Own-split is on by default; `--no-own-split` turns it off (chip should disappear).
