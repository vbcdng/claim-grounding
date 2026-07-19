# Fable-vs-Opus agreement — FULL PASS (2026-07-17)

Grader: claude-code/opus, prompt pt_owner_grader_v2.txt (v2 AS-IS, owner decision: measure the rule-A fork corpus-wide before v3).
Dedupe: (run, claim_id), corpus files over batch files, last row per file wins. Excluded by owner decision: 8 mutation rows (eggs_rerun_20260715+mutation) and minwage_argmap_demo (2 rows, no source_claims cache — not regenerated).

## Per-batch results

| batch | graded | agree | disagree | n/a axis | errors | disagree ids |
|---|---|---|---|---|---|---|
| bentonite_hard_2026-07-17 | 3 | 3 | 0 | 0 | 0 | — |
| coverage_gate_run | 7 | 7 | 0 | 0 | 0 | — |
| eggs_priority_2026-07-17 | 6 | 6 | 0 | 0 | 0 | — |
| eggs_supported_audit_2026-07-17 | 6 | 0 | 0 | 6 | 0 | — |
| first_check_run | 16 | 14 | 2 | 0 | 0 | t8, t9 |
| gate_run_bohemia | 5 | 2 | 0 | 3 | 0 | — |
| gate_run_pots | 5 | 3 | 2 | 0 | 0 | t3, t7 |
| newsys_synth_11 | 3 | 3 | 0 | 0 | 0 | — |
| newsys_synth_12 | 3 | 3 | 0 | 0 | 0 | — |
| newsys_wice_dev1 | 20 | 19 | 1 | 0 | 0 | t23 |
| newsys_wice_dev2 | 14 | 14 | 0 | 0 | 0 | — |
| newsys_wice_dev3 | 21 | 18 | 3 | 0 | 0 | t11, t22, t26 |
| newsys_wice_dev4 | 20 | 18 | 1 | 1 | 0 | t24 |
| newsys_wice_dev5 | 8 | 8 | 0 | 0 | 0 | — |
| newsys_wice_train1 | 19 | 17 | 1 | 1 | 0 | t26 |
| newsys_wice_train2 | 16 | 15 | 1 | 0 | 0 | t4 |
| nightB_wice_final | 15 | 13 | 2 | 0 | 0 | t8, t9 |
| paper1_hard_2026-07-17 | 27 | 13 | 10 | 4 | 0 | t6, t25, t35, t37, t43, t44, t47, t49, t68, t65 |
| polisci_verification | 14 | 0 | 0 | 14 | 0 | — |
| printing_press_fresh_2026-07-14 | 4 | 1 | 3 | 0 | 0 | t6, t1, t5 |
| printing_press_reformation_project_verification | 5 | 3 | 2 | 0 | 0 | t5, t6 |

## Corpus-wide summary (deduped)

- graded rows: **237** (+0 error rows pending retry)
- agree: **180**
- disagree: **28**
- axis n/a (unmapped Fable vocab, counted not dropped): **29**

- agreement rate on rulable rows: **86.5%** (180/208)

n/a rows by Fable verdict value: `not_rulable`×23, `true_claim_weak_evidence`×2, `partial_by_shown_evidence`×2, `verdict_ok_evidence_junk`×1, `verdict_ok_evidence_offset`×1

Disagreement direction:
- Fable provable vs Opus not_provable: 19
- Fable not_provable vs Opus provable: 9

## All disagreements (deduped)

| run | id | pipeline | Fable | Opus action | Opus missing_subclaim |
|---|---|---|---|---|---|
| first_check_run | t8 | partial | partial | supported |  |
| first_check_run | t9 | unsupported | supported | add_citation_or_rewrite | that the documentary film itself is Grammy-nominated — the source attributes the Grammy nomination to Steve Ao |
| gate_run_pots | t3 | unsupported | partial | wrong_or_insufficient_evidence |  |
| gate_run_pots | t7 | unsupported | supported | add_citation_or_rewrite | that the English museum survey reports concealment at doorways — MOLA (the survey) reports 'hearths or beneath |
| newsys_wice_dev1 | t23 | unsupported | supported | add_citation_or_rewrite | that the 1954 ruling declared all public schools must desegregate |
| newsys_wice_dev3 | t11 | unsupported | supported | add_citation_or_rewrite | that the retirement was specifically from first-class and List A cricket |
| newsys_wice_dev3 | t22 | unsupported | supported | add_citation_or_rewrite | the release subtitle "Metallica's Master of Puppets Revisited" |
| newsys_wice_dev3 | t26 | supported_partial | partial | wrong_or_insufficient_evidence |  |
| newsys_wice_dev4 | t24 | unsupported | supported | add_citation_or_rewrite | On March 1, 2017 (the specific calendar date of the selection) |
| newsys_wice_train1 | t26 | unsupported | partial | wrong_or_insufficient_evidence |  |
| newsys_wice_train2 | t4 | supported | partial | supported |  |
| nightB_wice_final | t8 | partial | partial | supported |  |
| nightB_wice_final | t9 | unsupported | supported | add_citation_or_rewrite | the documentary film is "Grammy-nominated" (the source attributes Grammy nominations to Steve Aoki the artist, |
| paper1_verification | t25 | supported+partial | supported | add_citation_or_rewrite | the withdrawal happened 'overnight', 'without consultation', and 'with no process by which an allied governmen |
| paper1_verification | t35 | supported+amber | supported_minor_caveat | add_citation_or_rewrite | that the intelligence explosion was FIRST described by Good (the claim of primacy) |
| paper1_verification | t37 | supported+partial | supported | add_citation_or_rewrite | that rentier / external-rent states tend to invest less in their citizens (populations) |
| paper1_verification | t43 | supported+amber | supported | add_citation_or_rewrite | the "dual-circulation" drive for self-sufficiency in food, energy, and high-tech inputs (and the broader frami |
| paper1_verification | t44 | supported+partial | supported | add_citation_or_rewrite | the central thesis that a self-sufficient state can 'absorb the shock of being outcompeted far better than an  |
| paper1_verification | t47 | supported+amber | supported | add_citation_or_rewrite | and the value of its currency — that a state also loses the value of its own currency (currency devaluation) |
| paper1_verification | t49 | supported+amber | supported | add_citation_or_rewrite | through economic dislocation alone, without ... blockade — i.e. the causal claim that no external coercion con |
| paper1_verification | t6 | supported+amber | supported | add_citation_or_rewrite | so a concentrated position can be pressed without waiting for new chips to be built |
| paper1_verification | t65 | supported+amber | supported | add_citation_or_rewrite | the race is better understood as a trust dilemma than a prisoner's dilemma (attributed via 'on their account') |
| paper1_verification | t68 | unsupported | own_interpretation | wrong_or_insufficient_evidence |  |
| printing_press_fresh_2026-07-14 | t1 | supported | supported | add_citation_or_rewrite | that earlier heresies had been actively contained/'snuffed out' (suppressed) and that print let heterodox doct |
| printing_press_fresh_2026-07-14 | t5 | supported | partial | wrong_or_insufficient_evidence |  |
| printing_press_fresh_2026-07-14 | t6 | unsupported | supported | add_citation_or_rewrite | regardless of print — that the new churches survived independent of the printing press |
| printing_press_reformation_project_verification | t5 | supported | partial | wrong_or_insufficient_evidence |  |
| printing_press_reformation_project_verification | t6 | unsupported | supported | add_citation_or_rewrite | regardless of print — that the new churches survived independently of the printing press |
