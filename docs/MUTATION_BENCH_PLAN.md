# Mutation bench — plan & first run (2026-07-17)

## Idea
Generated "hard example" datasets are weak exactly where you need them: the
intended label is often subtly wrong, so you re-audit by hand anyway. The
mutation bench sidesteps that. Take claims the pipeline **already verified as
supported**, apply a *minimal single-fact corruption* the cited source
contradicts, and re-run. Ground truth is free — we know the edit made the claim
wrong — so the only question is: **does the pipeline catch it?**

This measures the error direction we otherwise measure least: **false support**
(a corrupted number/direction slipping through inside an otherwise-supported
claim). It is the mirror of the coverage gate, which measures false alarms.

## Why it's cheap
Incremental rerun (`rerun.py`): copy a finished run's output dir, mutate N claims
in the text, rerun into the copy on the **same model**. Only the mutated claims
re-judge (`metadata.source_hashes` + text-hash match reuses the rest). N=8 on
gemini-flash-lite ≈ $0.01–0.05 with all detection passes on.

**Pitfall found on first run:** switching the backend (`--backend claude-code`)
forces a FULL re-run — "previous run used gemini … this one uses claude-code …
verdicts not comparable". Keep the mutated run on the **same model** as the
cached baseline or you lose both incrementality and a model-matched before/after.

## Mutation taxonomy (kinds applied this run)
- **negate** — flip a mechanism verb (downregulating → upregulating). t8.
- **num** — change a reported effect size (42%/RR1.42 → 22%/RR1.22). t22, t29, t30, t39.
- **num_10x** — order-of-magnitude inflation (3M → 30M person-years). t17.
- **flip_dir** — reverse the direction of a finding + its statistic
  (lower risk/HR 0.89 → higher risk/HR 1.89). t18, t20.

All 8 are on **supported** eggs claims; each `old` string was uniqueness-checked
before replacement. Manifest: `scratchpad/mutbench/mutation_manifest.json`.

## Scoring
For each mutated claim, compare new verdict/flags against the baseline
(`data/eggs_rerun_20260715`):
- **CAUGHT** — flipped to unsupported, OR gained a `partial_support` /
  covering-set uncovered amber / arbiter "conflicting evidence" flag.
- **MISSED** — still clean `supported`, no new flag (a false support).
The **catch rate** = CAUGHT / 8. A number-mutation that survives is the most
interesting failure: it shows the judge validated surrounding prose without
checking the specific figure against the source.

## First run — results (2026-07-17, gemini-2.5-flash-lite, full detection stack)
8 mutations on eggs_rerun_20260715, incremental rerun (46 claims reused, 8
re-judged). Cost $0.074 total ($0.056 gemini + $0.018 deepseek arbiter), 226s.

**Catch rate: 6/8 (75%).** How each was caught:

| claim | kind | mutation | result | caught by |
|-------|------|----------|--------|-----------|
| t18 | flip_dir | HR 0.93 → 1.93 (no risk → substantial risk) | unsupported | arbiter: "directly contradicted, source gives HR 0.93" |
| t20 | flip_dir | lower risk/HR 0.89 → higher/HR 1.89 | unsupported | judge + arbiter |
| t22 | num | 17%/HR 1.17 → 70%/HR 1.70 | unsupported | judge caught the figure directly |
| t17 | num_10x | 3M → 30M person-years | supported + amber | covering-set uncovered + arbiter |
| t30 | num | 54%/RR 1.54 → 154%/RR 2.54 | supported + amber | covering-set uncovered + arbiter |
| t39 | num | cap 300 → 500 mg/day | supported + amber | covering-set uncovered ×2 |
| **t8** | **negate** | **downregulating → upregulating** | **supported, no flag** | **MISSED** |
| **t29** | **num** | **42%/RR 1.42 → 22%/RR 1.22** | **supported, no flag** | **MISSED** |

**The two misses are the finding, not the noise:**
- **t8 (mechanism negation, MISSED)** — reversing the physiology verb
  (liver *downregulates* → *upregulates* its cholesterol synthesis) slipped
  through with no flag. Most concerning class for a grounding tool: the
  surrounding claim ("net effect on serum cholesterol is much smaller") stays
  true, so the judge validated the intact prose and never tested the flipped verb.
  **Correction (adversarial verification, 2026-07-17):** the cited source
  mcnamara1987 DOES state the correct direction verbatim ("suppression of
  endogenous cholesterol synthesis" / "decreasing ... endogenous cholesterol
  synthesis"), so the mutation was CATCHABLE — the miss is a retrieval + judge-
  scope failure (the mechanism sentence was never surfaced), not a source that
  lacks the direction. This strengthens the case for Finding-D Part 2 (mechanism-
  direction check): the ground truth to check against is present in the source.
- **t29 (halved effect size, MISSED)** — judge reasoning: "the passage states
  egg consumption may be associated with increased incidence of type 2
  diabetes … which directly supports the claim." It confirmed the *qualitative*
  direction and never pinned the *number* (42% vs mutated 22%).

**Inconsistency is the headline:** t22 (a number mutation) was caught cold —
judge quoted "does not state 70% … source reports HR 1.17" — while t29 (also a
number mutation) was missed. Number-checking fires when the figure is the
claim's focal point and misses when it rides inside qualitative prose the judge
can otherwise confirm. That is a targeted, fixable weakness (e.g. a
deterministic numeric-token cross-check on supported cited claims, cousin of the
named-specific-entity check in ARCHITECTURE §6.5).

**Direction flips + order-of-magnitude errors are caught reliably** (3/3 + the
10x); **in-direction magnitude edits are the soft spot** (1/3 missed).
Full per-claim data: `scratchpad/mutbench/score_result.json`.

## How to extend
- Scale N across all 23 supported claims and all 3 gate papers (paper1,
  bentonite, chimpanzee) → a standing false-support metric per model.
- Add a **control arm**: re-apply the ORIGINAL (correct) values as "mutations"
  that should NOT flip — measures the bench's own false-positive rate.
- Wire into a `benchmarks/mutation_bench.py` harness once the manual run
  validates the approach; it can share `regression_check.py`'s scorer shape.
