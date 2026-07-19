# Pre-registration: held-out WiCE evaluation (frozen 2026-07-19, BEFORE the run)

This document fixes the entire evaluation plan BEFORE any held-out row is
run. The git commit timestamp of this file is the proof of ordering. The
results will be published as they come out; any deviation from this plan
will be documented as a deviation in the results report.

## 1. Rows (mechanical rule, no discretion)

- **Core set:** ALL 358 rows of the WiCE claim-level `test` split
  (`data/wice/test.jsonl`; 111 supported / 215 partially_supported /
  32 not_supported).
- **Refuted stress set:** ALL `not_supported` rows of the `train` split
  whose `wice_id` does not appear in any ground-truth file under
  `benchmarks/wice_runs/` or `data/first_check/wice_ground_truth.json`
  (the 160 already-used ids). Expected ≈150 rows; the exact list is
  produced by the conversion step and shipped with the results.
- **Pre-declared exclusions only:** (a) rows whose source page is
  non-English (the existing owner rule; same detection approach as the
  2026-07-18 exclusion — language probe on the source text); (b) rows the
  converter cannot emit (missing/empty source in the dataset). Every
  excluded row is listed by wice_id + reason in the results report.
  No other exclusion is permitted.

## 2. Code and configuration

- Conversion: `benchmarks/wice_bench.py convert` (a mechanical extension to
  emit a whole split / a label-filtered subset is permitted — conversion
  only, it does not touch judging or scoring).
- Pipeline: `verify_my_text.py` with the shipped defaults — model
  `gemini/gemini-2.5-flash-lite`, arbiter ON (default
  `deepseek/deepseek-v4-flash`), partial-check/citation-scope/own-split
  defaults as shipped. `--full` fresh runs, no verdict reuse. The tool
  commit used for the run is recorded in the results report and must
  contain no changes beyond master at the time of this pre-registration
  other than the permitted converter extension.
- Scoring: `benchmarks/wice_bench.py score` UNCHANGED from the version at
  this commit. Both layers reported: strict verdict-level and
  arbiter-adjudicated, plus the false-support line on refuted rows
  (base and adjudicated).

## 3. Numbers that will be published (all of them, as they come out)

1. Test split: strict agreement x/358-ish, adjudicated agreement, and
   false-supports on its 32 refuted rows.
2. Refuted stress set: false-supports on ~150 fresh refuted rows (the
   headline safety number), plus its agreement rates.
3. The excluded-rows list with reasons.
4. Cost and runtime.

No re-runs to improve numbers. If an infrastructure failure interrupts a
run, the run may be restarted, and this is reported; judged verdicts are
never selectively re-judged.

## 4. Freeze extension

These rows join the held-out set the moment this file is committed: no
prompt/matcher/config change may ever be justified by any of them
(same rule as benchmarks/wice_anchor/ and the owner's verdicts —
memory: owner-verdicts-benchmark-only). If a future version of the tool is
evaluated on them again, that is a new versioned report, and deriving
fixes from the results burns the set — it would have to be replaced from
the remaining unused train rows.

## 5. Context note to publish WITH the results (label-noise disclosure)

Draft (owner's voice, to be approved before publication):

> One thing to know when reading numbers against WiCE. On the 159 WiCE
> claims we analyzed deeply during development, the labels themselves are
> not always right: two frontier models both disputed 12 of the 117
> reviewed labels (about 8%), and when I hand-checked 9 of those disputed
> rows myself, the WiCE label was wrong in 6 and right in 3. I also
> disagreed with the frontier models themselves: even when both models
> agreed with each other, I overruled them 4 times out of 26 rows I
> checked (15%) — so neither the dataset nor the strongest models are a
> perfect standard, including mine. About a fifth of WiCE source pages
> are mostly table scaffolding rather than prose, and 5 rows had
> non-English sources (excluded). Direction of the label noise: in the
> disputes we examined, WiCE credits claims more generously than our
> standard, almost never the reverse. For calibration: a frontier model
> reading the sources generously agrees with WiCE labels about 90% of
> the time (binary) — so ~90%, not 100%, is the realistic ceiling for
> any checker scored against WiCE.

Supporting numbers for the note (computed 2026-07-19, in-repo evidence:
benchmarks/wice_anchor/, docs/K3_TRIANGULATION_2026-07-18.md, chat-session
analyses banked in memory): models jointly disputed 12/117 reviewed unique
claims; owner arbitration of 9 disputed: 6 WiCE-wrong / 3 WiCE-right;
owner-vs-consensus overall 22/26 (85%); Fable-vs-WiCE binary 90%
(121/134 on non-owner rows), Opus 83%.

## 6. Execution (separate session, post-deadline)

Estimated cost ~$0.7–1.2 (sources ship inside the WiCE dataset, no
downloads). Steps: extend converter → convert both sets → run pipeline
per batch → `wice_bench.py score` per batch → aggregate → results report
`docs/WICE_HELDOUT_RESULTS_<date>.md` + the context note. Nothing else.
