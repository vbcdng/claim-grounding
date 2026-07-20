# Generalization check — bentonite + chimpanzee (2026-07-04)

Every accuracy number and tuning decision in this tool (judge prompt, tail rescue,
lexical gating, own-split classifier, confidence proxy, the regression harness) was
derived from **one** paper (paper1). This is the overfitting check: run both example
corpora through the *whole* pipeline on the current default config
(`gemini/gemini-2.5-flash-lite`, this week's matcher) with **zero code/prompt/config
changes**, hand-audit the verdicts, and see whether paper1's tuning generalizes.

**Verdict: no evidence of overfitting.** Both corpora reproduce their independent
baselines, no false positives, all exercised features behave per their docs. Two
findings recorded below (a model-guard hole; an own-split coverage gap) — neither is
an overfitting signal; both are follow-ups, not fixed in this pass.

---

## Headline result

| corpus | claims | this run (flash-lite) | independent baseline | match |
|--------|--------|-----------------------|----------------------|-------|
| chimpanzee | 12 (all cited) | 12 supported / 0 unsupported | human audit 2026-07-02: 12/12 supported | **exact** |
| bentonite | 6 (all cited) | 4 supported / 2 unsupported | flash run 2026-07-02: 4/2 | **exact, zero flips** |

The chimpanzee corpus is the decisive probe: a prior *human* audit
(`docs/HUMAN_VS_TOOL_DIVERGENCE_LAYER3.md`) confirmed 12/12 supported. If paper1's
tuning had over-tightened the judge, human-confirmed supports would now flip to
unsupported. **None did.** Bentonite reproduces its old-model verdicts exactly
despite the flash→flash-lite switch — a strong reproducibility signal.

## Chimpanzee — per-claim audit (all 12 supported)

All 12 resolved via the direct-judge path (`method=llm`), cosine 0.90–1.00 (the text
is near-verbatim from the single cited source). Evidence sentences match in every
case. t9 (cos 0.899) is the one paraphrase the Layer-3 doc flagged ("too difficult
for them" vs. the source's "for some chimpanzees") — still correctly supported.
Known cosmetic carry-over (not verdict-affecting): t1's evidence still glues on the
section heading "Concerns with the study rationale" and PDF spacing noise ("et al .",
"[ 6]") — same Layer-3 point 3 as before, unchanged.

## Bentonite — per-claim audit (4 supported / 2 unsupported)

| id | verdict | audit | note |
|----|---------|-------|------|
| t0 | supported | ✓ correct | weathered biotite superior Cs sorption; source confirms. "two orders of magnitude" is qualitative-clear, exact magnitude not in snippet |
| t1 | supported | ✓ correct | montmorillonite > illite/kaolinite "under all tested conditions" — direct |
| t2 | unsupported | **watch** | compound claim: main clause (Kunipia-F > SiO2) IS supported; judge rejects on the incidental "which contain montmorillonite" clause not being in a *cited* source. Over-literal but defensible |
| t3 | supported | ✓ correct | Mo-vanadate-loaded bentonite enhances Cs removal — direct |
| t4 | supported | ✓ correct | glauconite > bentonite + acid-activated kaolinite; both clauses have matching cited sources |
| t5 | unsupported | ✓ **correct red** | cites only 32d6151f27 (glauconite/bentonite/zeolite/diatomite): supports "zeolite highest" but says nothing about "weathered biotite" (that's in t0's source, not cited here). Real citation gap |

No false positives (no wrong greens — the dangerous direction). t5 is a correctly
reasoned red. t2 is the only gray call → tracked as `watch`, not a hard expectation.

Notably, t2's **re-citation `alternatives`** (pure-cosine, no LLM) surface exactly the
missing evidence: *"The sample of Bentonite was chosen with a Na-form of
montmorillonite"* and *"Bentonite is composed mainly of montmorillonite."* The fact
is in the corpus, just not in a *cited* source — the feature points straight at the
fix. This is the multi-source path (9 sources) that paper1 exercised only one way.

## Overfitting signals — each checked

- **Judge strictness** (the important one): human-confirmed supports rejected on a
  new paper? **No** — chimp 12/12 held; bentonite 4/4 supports held. Decisive.
- **Cross-model disagreement** (`--second-opinion`, flash vs. flash-lite): **0/12 and
  0/6** disagreements. Flash even agrees with the borderline t2, so t2's rejection is
  a shared over-literalness, not a flash-lite idiosyncrasy. No overfitting signal.
- **Own-split tag distribution on a non-paper1 style**: **could not be exercised** —
  both corpora are fully cited (0 uncited/"own" claims on each). See finding #2. The
  classifier remains validated only on paper1 (37 own) + unit tests.
- **Cost estimator on new corpora**: predictions vs. actuals below — within range on
  both (chimp point $0.01, actual pennies; bentonite full point $0.01). No surprise.

## Estimator predicted-vs-actual

| run | predicted (point / range) | actual |
|-----|---------------------------|--------|
| chimp fresh | $0.01 / $0.01–0.03, ~47 calls | ~pennies, 37s wall |
| bentonite --full | $0.01 / $0.01–0.02, ~32 calls (decomp cached) | ~pennies, 200s wall (embeddings uncached) |
| chimp incremental (1 edit) | ~4 calls, 11 reused / 1 judged | exact: 11 reused, 1 judged, 8s |
| second opinions | "a cent or two" each | 18 flash calls total |

Total live spend for the entire check: **< $0.10**, well under the 100 Kč budget.
(Wall-clock note: bentonite's decomposition cache is model-agnostic and was reused —
$0 — but its embeddings cache didn't exist, so ~1600 source-claim vectors were
encoded on CPU; that's the 200s, not API cost.)

## Feature exercise matrix

| feature | corpus | result |
|---------|--------|--------|
| source decomposition (fresh, uncached PDF) | chimp | ✓ 100 claims from 1 PDF |
| decomposition cache reuse (model-agnostic) | bentonite | ✓ 9 sources, $0 |
| 3-stage matching (candidate→judge→fulltext) | both | ✓ llm / llm_fulltext / combined_fulltext all seen |
| confidence proxy chips | both | ✓ chimp 12 high; bentonite 3 high / 3 medium |
| omitted ranking + embed cap | bentonite | ✓ 1569 ranked, top 15 shown + 200 cap + beyond-cap note |
| re-citation `alternatives` (unsupported cards) | bentonite | ✓ 3 per red, surfaces the real missing evidence |
| incremental re-run (unchanged reuse) | chimp | ✓ 11/12 reused $0, Changed(1) filter + ✎ diff note |
| review loop: `verdict_feedback.json` → skip + chip | bentonite | ✓ disputed t2 skipped by 2nd opinion (5 checked not 6), "author disputed" in viewer |
| `--fix-claim` | bentonite t5 | ✓ swapped uncited "weathered biotite"→"glauconite", softened "best"→"strong", honestly flagged re-check inconclusive; did not weaken the quantitative "highest" |
| `--second-opinion` incremental | both | ✓ verdicts reused, only opinion calls billed |
| own-split classifier | — | **not exercised** (0 uncited claims on either corpus) — finding #2 |
| viewer triage buttons / REVIEW_DATA / Copy brief | both | ✓ present (browser interaction is the owner's check) |

## Findings (follow-ups, NOT fixed here — no code changed during a generalization test)

1. **Model-guard hole for pre-tracking runs.** `verify_my_text.py:226` only refuses
   verdict reuse when `metadata.model` is truthy. The old bentonite run predated
   model-in-metadata tracking (`metadata.model = None`), so a bare incremental re-run
   would have **silently reused old flash verdicts under a flash-lite run** — exactly
   the historical runs most likely to have a different model. Worked around here with
   `--full`. Fix candidate: when `prev_model` is missing, either warn (as the
   source-hash path already does: "predates … tracking") or force a full re-judge.
   Low severity (only affects runs made before the tracking was added).
2. **Own-split coverage gap.** Both shipped example corpora are fully cited (0 "own"
   claims), so the own-split / "citation needed?" classifier — the one feature whose
   prompt was hand-tuned on paper1's scenario-heavy style — got no independent
   exercise here. It stays covered by paper1 (37 own → 17 structural / 19 opinion / 1
   fact) and `tests/test_own_split.py`. To generalize-test it, a corpus with uncited
   sentences is needed (the owner's next real article will provide one).

## Follow-up: synthesized-article test (closes the own-split gap)

To exercise own-split and paraphrase grounding on a genuinely new text, a short
article was written *synthesizing* the surface-hydration source
(`…451becad04.txt`): 7 sentences, 4 citing `[[surf]]` (paraphrased, not copied) and 3
left uncited. Run on the default config (~$0.01), output in the job scratch dir (not
committed).

- **Grounding 4/4 correct**, including two real paraphrases: t3 (cos 0.94, "proceeds
  mainly through ion-exchange reactions" ← "confines itself to ion-exchange
  reactions") and t4 (cos 0.865, "lower outer-shell charge density lets it overcome
  the diffuse layer and fix firmly" ← the source's wording). No false positives.
- **Own-split**: t5 "Cesium-137 is among the most hazardous fission products in reactor
  effluent" → **citation-needed (correct — a planted uncited fact)**; the last sentence
  → **structural (correct)**; t1 "This means the decisive factor is surface chemistry,
  not bulk capacity" → citation-needed (defensible — it asserts a checkable claim; I
  had intended it as author-synthesis). The paper1 "hypotheticals→opinion" tuning did
  NOT cause under-flagging here — the real uncited fact was caught. Viewer rendered the
  two "citation needed?" chips, the filter, and the "2 citation suggestions" header.
- **New finding (i)**: t0's *displayed* evidence was the `.txt` file's `Source URL: … /
  Downloaded: … / ---` header, not a content sentence (verdict still correct — the
  matched chunk contained the real abstract). `download_sources.py` prepends that
  preamble to fetched `.txt` sources and the fulltext path can pick it as evidence.
  Cosmetic (never changes a verdict) but erodes trust in the shown evidence — filed in
  ROADMAP as (i): strip the preamble before decomposition/evidence selection.

## What now guards against regressions

Ground truth is now **three papers**, not one:
- `benchmarks/paper1_ground_truth.json` (35 claims)
- `benchmarks/bentonite_ground_truth.json` (4 hard supported, 1 hard unsupported t5, 1 watch t2)
- `benchmarks/chimpanzee_ground_truth.json` (12 hard supported)

`benchmarks/check_all.sh` scores all three (free — no API; reads existing
analysis.json files). The ship-gate rule is now: **no prompt/matcher/config change
ships unless `check_all.sh` passes on fresh runs of all three papers.**
