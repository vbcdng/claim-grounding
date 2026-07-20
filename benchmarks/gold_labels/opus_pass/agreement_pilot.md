# Fable-vs-Opus grader agreement — PILOT (20 labels, 2026-07-17)

Input: first 20 rows of `benchmarks/gold_labels/paper1_hard_2026-07-17.jsonl`
(the 20 rulable rows; the batch's last 7 rows include the 4 `not_rulable`
source-file-missing rows, excluded by taking the first 20).
Grader: **claude-code/opus** (local claude CLI, $0) with
`config/prompts/pt_owner_grader_v2.txt`, prompt assembled exactly like the
arbiter's (`arbiter._shown_block` + `arbiter._source_blocks`, full source or
best ~20k-word section). Raw outputs:
`opus_pass/paper1_hard_2026-07-17_graded.jsonl`. All 20 parsed, 0 errors,
0 hallucinated quotes (every proof sentence passed the verbatim gate:
`quotes_dropped=0` on all rows).

## Comparison axis

The grader grades the DISPLAY (rule B: provable-nearby is never "supported");
the Fable gold labels grade SUBSTANCE (is the claim provable from the cited
source at all). The comparable signal is **provability**:

- grader action `supported` or `wrong_or_insufficient_evidence` → every
  component provable from the source → **provable**
- grader action `add_citation_or_rewrite` → some component not provable →
  **not fully provable**
- Fable `supported` / `supported_minor_caveat` → provable;
  `partial` / `partial_weak` / `unsupported` / `own_*` → not fully provable.

## Summary

| | count |
|---|---|
| Agree (provability axis) | **12 / 20** |
| Disagree | **8 / 20** — t6, t25, t35, t37, t43, t44, t47, t49 |
| Parse errors | 0 |
| Hallucinated (gate-dropped) quotes | 0 |

All 8 disagreements are ONE direction and ONE shape: Fable ruled
`supported` (with the pipeline's amber/partial chip correctly marking an
interpretive rider), Opus ruled `add_citation_or_rewrite` because rule A
("interpretive components count") makes that same rider a failed component.
Opus never called a Fable-unsupported/partial row supported (0 loose-positive
errors — the direction the gate_v2 exam flags as Opus's known bias did NOT
appear). On the 9 Fable-partial/unsupported/own rows, agreement is 9/9.

## Per-claim table

| id | pipeline verdict | Fable gold | Opus verdict/action | axis | note |
|----|------------------|------------|---------------------|------|------|
| t6 | supported+amber | supported | unsupported / add_citation_or_rewrite | DISAGREE | see below |
| t9 | unsupported | own_interpretation | unsupported / add_citation_or_rewrite | agree | both: author's extrapolation from Baumol |
| t14 | unsupported | partial | unsupported / add_citation_or_rewrite | agree | both: named-specific facts (Tengiz, %) unproven |
| t17 | supported+amber | supported | unsupported / wrong_or_insufficient_evidence | agree | Opus: provable, display incomplete (rule B) |
| t22 | unsupported | partial | unsupported / add_citation_or_rewrite | agree | both: transition opener unproven |
| t23 | unsupported | partial | unsupported / add_citation_or_rewrite | agree | both: some components proven, core not |
| t24 | unsupported | partial | unsupported / add_citation_or_rewrite | agree | both: core event proven, rest not |
| t25 | supported+partial | supported | unsupported / add_citation_or_rewrite | DISAGREE | see below |
| t28 | supported+partial | supported | supported / supported | agree | only clean supported of the pilot |
| t30 | unsupported | partial | unsupported / add_citation_or_rewrite | agree | both: 2 components verbatim, rest not |
| t31 | unsupported | unsupported | unsupported / add_citation_or_rewrite | agree | both: wrong source entirely |
| t32 | unsupported | partial | unsupported / add_citation_or_rewrite | agree | both: several components proven, thesis not |
| t35 | supported+amber | supported_minor_caveat | unsupported / add_citation_or_rewrite | DISAGREE | see below |
| t37 | supported+partial | supported | unsupported / add_citation_or_rewrite | DISAGREE | see below |
| t42 | supported+partial (arbiter:wrong_or_insufficient) | supported | unsupported / wrong_or_insufficient_evidence | agree | Opus reproduces the arbiter's retrieval-display finding |
| t43 | supported+amber | supported | unsupported / add_citation_or_rewrite | DISAGREE | see below |
| t44 | supported+partial | supported | unsupported / add_citation_or_rewrite | DISAGREE | see below |
| t47 | supported+amber | supported | unsupported / add_citation_or_rewrite | DISAGREE | see below |
| t49 | supported+amber | supported | unsupported / add_citation_or_rewrite | DISAGREE | see below |
| t56 | supported+partial | partial_weak | unsupported / add_citation_or_rewrite | agree | both: claim's core (hegemon thesis) unproven |

## The 8 disagreements — one line each

Every one is the same rubric fork: **is the author's interpretive rider a
component of the claim (Opus, rule A) or correctly-ambered framing on a
supported factual claim (Fable)?**

- **t6** — Fable: compute stats proven verbatim, "scarce physical resource"
  thesis correctly amber. Opus: that thesis + "pressed without waiting for new
  chips" are unprovable components → author fix. Rule-A strictness vs Fable's
  substance call.
- **t25** — Fable: "without consultation / no process to contest" fairly
  entailed by the described abruptness. Opus: entailment isn't statement —
  those words are nowhere in the source. Genuine judgment call on entailment.
- **t35** — Fable: "first described by Good" is a minor primacy caveat. Opus:
  a superlative/primacy component the source never establishes → author fix.
  Same fact, different severity threshold.
- **t37** — Fable: "invest less in citizens" entailed by the
  reduced-accountability finding. Opus: never stated anywhere in the source.
  Entailment-vs-statement again.
- **t43** — Fable: "dual-circulation" is framing, correctly amber. Opus: the
  cited CSIS source never mentions dual-circulation → unprovable component.
- **t44** — Fable: "early lead may compound" rider interpretive, correctly
  partial. Opus: the governing thesis (autarky absorbs the shock of being
  outcompeted) is the author's, unprovable from the cited pair.
- **t47** — Fable: causal extension partly author's synthesis, amber fair.
  Opus: the currency-devaluation link is a component the source doesn't state
  (mixed case → author fix dominates, rule at line "MIXED CASE").
- **t49** — Fable: "without war or blockade" is the author's
  characterization, amber fair. Opus: "economic dislocation alone, no external
  coercion" is a causal component the source doesn't state.

**Reading:** these are not grader errors in either direction — they are the
known display-vs-substance / rule-A-severity fork the rubric's centrality
guard (docs/JUDGING_RUBRIC_FABLE_2026-07-17.md, Finding 1) exists to
arbitrate. Fable applies centrality (rider is framing, not the claim's core);
the v2 grader prompt has no centrality language, so Opus counts every rider
as a full component. The disagreement set is exactly the rubric-tuning input
the gold-labeling plan wanted: adding the centrality guard to the grader
prompt should collapse most of these 8.

Secondary observation: on every Fable-supported row except t28, Opus's
top-level `verdict` field was "unsupported" even when its action said the
claim was provable (t17, t42) — under this prompt Opus is display-strict,
not loose. The gate_v2 exam's warned bias (looseness on positives) did not
appear in this sample.
