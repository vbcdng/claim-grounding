# Grounding judgment rubric (Fable-distilled, 2026-07-17)

Distilled from Fable ruling on ~40 hard claims across three hand-audited papers
(paper1, bentonite, eggs) plus 8 mutation-bench corruptions, all in the
2026-07-17 sessions. Purpose: a decision procedure a **weaker grader**
(Opus/Sonnet/gemini) can follow to reproduce Fable-grade verdicts, and the
reference the deferred Opus-agreement pass is scored against. Every rule below
carries the concrete claim that calibrated it, so a grader can anchor on real
examples rather than abstractions.

## Core stance
A cited claim is a **conjunction of components**. Grade the components, then
aggregate — never grade the sentence as one gestalt. Most errors in the current
tool come from collapsing a multi-part claim into a single supported/unsupported
call. The verdict field is coarse; the truth is usually "these parts yes, those
parts no."

## Decision procedure (per cited claim)
1. **Decompose into atomic components.** A number, a named entity, a direction,
   a mechanism, and an interpretive framing are each separate components.
   *Anchor: paper1 t30 has three — microtargeting-adds-little, 20+-ops-disrupted,
   Romania-outcome — with different truth values.*
2. **For each component, demand a verbatim span** from the cited source(s) that
   entails it. No span → that component is unproven. Quote it; don't paraphrase.
3. **Aggregate:**
   - All components proven → **supported**.
   - Some proven, some not → **partial ONLY IF a proven component is substantive
     to the claim's actual assertion**; if only topical background/framing/a
     supporting stat is proven while the claim's distinctive assertion fails,
     the verdict is **unsupported**, not partial. This is the **centrality
     guard** (learned by cross-checking Fable's labels against the human audit,
     2026-07-17: Fable over-credited t23/t30/t5/t14 to partial, but the human
     held them unsupported because the proven part was mere framing). *Partial
     is right on t24/t32/t2 — there the proven part IS the core.*
   - No substantive component proven → **unsupported**.
   - The unproven parts are the author's synthesis/argument, not source claims →
     **own-interpretation** (citation-scope: concept), not a factual failure.
     *Anchors: t9, t61, t68 (paper1).*

## Rule N — Numbers are components, and focal-vs-in-prose matters
Check every reported figure against the source digit-by-digit. The judge reliably
catches a number when it is the claim's *focus* but misses it when it rides
inside qualitative prose it can otherwise confirm. *Anchors (mutation bench):
t22 caught (70% vs source 17% — focal), t29 MISSED (42%→22% — rode inside a
confirmable "diabetes as effect modifier" sentence).* **Grader instruction:**
extract each numeric token and verify it independently even when the surrounding
sentence is supported.

## Rule D — Direction and mechanism are components
A reversed direction or negated mechanism flips truth even when the surrounding
claim stays plausible. Direction flips are caught reliably; a negated *mechanism
verb* is a blind spot. *Anchors: t18/t20 caught (HR direction flips); mutation
t8 MISSED (liver "downregulating"→"upregulating" — surrounding claim stayed
true, source was about responder variation not regulation direction).*
**Grader instruction:** for any causal/mechanistic verb, confirm the source
states that direction, not merely the topic.

## Rule Q — Quoted attributions must be in the cited source
If a claim puts a **verbatim quotation** in a named source's mouth, that exact
quote must appear in the cited source — supporting the surrounding facts is not
enough. *Anchor: eggs t39 — cap-removal facts proven by soliman2018, but the
direct DGAC quote ("no appreciable relationship…") is not in soliman2018; it
needs the primary report.* This is the highest-confidence failure class: a
fabricated-looking attribution.

## Rule E — The shown evidence sentence must not contradict the claim
Retrieval maximizes lexical overlap, which can select an opposite-polarity
sentence. Before trusting a verdict, check that the displayed evidence *supports*
rather than *undercuts* the claim. *Anchors: paper1 t42 — shown sentence "how
soon China will attain peer status remains unclear" contradicts, while the true
proof "China is a peer competitor in AI" supports; t61 — evidence is an NBER
boilerplate disclaimer (junk).*

## Rule S — Missing source is not a verdict
If the source file was never ingested (`source_file_missing`), you cannot rule
supported OR unsupported — output **not_rulable / needs-source**. Do not let an
ingestion gap masquerade as an author error. *Anchors: paper1 t1 (Bostrom), t48
(Sen) — both almost certainly accurate to their sources, but unverifiable.*

## Rule C — Named-specific entities need their own proof
General-pattern proof does not cover a specific named deal, cohort, percentage,
or place embedded in the claim. *Anchors: paper1 t14 (OECD supports "foreign
capital rose in telecom/energy" but not the Chevron/Tengiz stake or exact
Hungarian percentages); eggs t35 (wallin2016 supports egg/T2D generally but
never mentions the named EPIC-InterAct cohort).*

## Aggregation cheatsheet
| pattern | verdict |
|---------|---------|
| all components have verbatim proof | supported |
| a **core/distinctive** component proven + others unproven | **partial** |
| only **background/framing/supporting-stat** proven, distinctive assertion fails | **unsupported** (centrality guard) |
| number/direction/mechanism wrong, rest fine | partial or unsupported (by centrality) |
| verbatim quote not in cited source | partial (fact ok) / unsupported (quote is the point) |
| unproven parts are author's argument | own-interpretation (scope: concept) |
| no substantive component proven | unsupported |
| source file missing | not_rulable |

## For the deferred API pass
Score Opus against this rubric on the 51 banked labels; every Opus-vs-rubric
disagreement is either a rubric gap (refine here) or an Opus-grader gap (fix the
grader prompt). Fold Rules N/D/Q/E/S/C into the grader prompt verbatim.
