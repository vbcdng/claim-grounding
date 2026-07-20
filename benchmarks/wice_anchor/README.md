# WiCE anchor — frozen evaluation set (2026-07-19)

One row per unique WiCE claim ever run through the tool (159 after the
non-English exclusion), combining: the WiCE human label, every banked
Fable/Opus grader verdict, and the author's ruling where one exists.
Built by `benchmarks/build_wice_anchor.py`; scored by
`benchmarks/score_wice_anchor.py` (no LLM calls).

## Freeze rule (author, 2026-07-19)

This set is **held out from all tuning from 2026-07-19 on**. Never edit
labels from a tuning session; never justify a prompt/matcher change by an
anchor row. Author verdicts are benchmark-only (see memory rule
owner-verdicts-benchmark-only). Rows that fed verdict-path code changes
BEFORE the freeze are marked tier X and excluded from held-out scoring.

## Tiers

| tier | meaning | label used | scored |
|---|---|---|---|
| A (14) | author personally ruled the claim | author's | yes |
| B (87) | every banked model grade agrees with WiCE | WiCE's | yes |
| B0 (37) | no grader ever reviewed the row | WiCE's (unreviewed) | yes |
| B_flag (8) | one model dissents, the other agrees | WiCE's (2-v-1) | yes |
| C (1) | all graders dissent, no author ruling (jeffglor) | none | no |
| X (12 rows / 10 sources) | a VERDICT-PATH fix was derived from the row pre-freeze (contamination audit 2026-07-19: subject guard ×2, P3 date-context set ×5, entity tokenizer, byline rule ×3-row source, possessive normalization) | none | no |

Additionally 4 scored rows carry `display_fix_derived` (a display-only fix —
evidence collapse, badge wording — was derived from them; verdict path
untouched): ninashipperlee, bonairpresbyterianchurch, cokezoo,
averymurraychristmas. Full evidence pointers: the 2026-07-19 contamination
audit (SUBJECT_GUARD.md, NIGHT_LOG_2026-07-12_accB.md, FIRST_CHECK_RUN.md).
Author-ruled row jessicachastain is tier X (contamination outranks tier A for
scoring; the author label stays recorded in the row).

## Dual reporting (house rule)

Always report BOTH: **raw** (vs original WiCE labels — comparable to the
literature and to the submission's published numbers) and **corrected**
(vs anchor final labels). Replacing raw with corrected in any external
document would look like grade inflation. Baseline at freeze time:

    RAW       strict 82/159 (52%)   adjudicated 127/159 (80%)
    CORRECTED strict 80/145 (55%)   adjudicated 123/145 (85%)
    (CORRECTED = clean held-out rows only: contaminated + disputed +
     author-not-rulable removed)

## Provenance notes

- Author arbitration of the model-vs-WiCE disputes (Part 3, 2026-07-19):
  author sided with the models on 6 of 9, with WiCE on 3 of 9 — model
  consensus is usually but not always right; hence tier C exists rather
  than letting models outvote WiCE without a human.
- Slugs are NOT unique (two claims can cite the same article) — join by
  wice_id / (run, claim_id), never by slug.
- The dev2_pilot batch was verified three times (first_check_run and
  nightB_wice_final re-runs); the anchor dedupes by wice_id and records
  all runs.
