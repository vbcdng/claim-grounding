# Stream C — partial-check accuracy fix (ROADMAP priority 7-i)

State doc, 2026-07-05 (Fable sprint, account B / window 2). Branch: `streamC`
(worktree `../cg-streamC`). Design per `docs/IMPROVEMENTS_FROM_PRIOR_ART.md`
Tier 1 (i-a hybrid retrieval → i-b NEI escalation → i-c ALCE), extended live
with a round-3 "verify the verifier" probe the plan didn't anticipate but the
validation demanded. **Goal: make `--partial-check` trustworthy enough to flip
default-on.**

> **RESOLVED 2026-07-05 — the flip LANDED.** The fresh 3-paper Gemini gate ran
> and passed (paper1 24/24, bentonite 5/5, chimpanzee 12/12 hard expectations,
> zero gated verdicts disturbed; commit 1fe96af) and partial-check has been
> **default-on** since (`--no-partial-check` opts out; `--partial-check` is a
> back-compat no-op). The "What's left before default-on" section below is the
> historical pre-gate checklist, kept for the record.

## What shipped (this branch)

`matcher.py` — the partial-check block is now a 3-round ladder in
`_partial_flags()` (all rounds NUDGE-only; the verdict never flips):

1. **Round 1 — hybrid retrieval (i-a, SemanticCite):** the combined judge sees
   each source's window **plus its lead sentences** (`_lead_text`, first 6
   non-degenerate ≈ title + abstract) — the exact evidence pure cosine missed
   in 6/7 re-audit false alarms.
2. **Round 2 — NEI-triggered escalation (i-b, DeepSciVerify/sciwrite-lint):**
   only on a round-1 negative, re-judge against `_escalated_context()`: title
   zone + top-6 verbatim sentences + top-4 cached decomposed claims, ranked by
   RRF-fused lexical+cosine (`_hybrid_top`). Sentences matter because a
   decomposition can be broken (korinek2023's cache = 8 math fragments from 72
   pages). Context kept deliberately COMPACT — a lead+8+8 blob measurably tips
   flash-lite into blanket "not stated" refusals (re-confirmed live; the
   run-4/5 learning). The deciding vote casts **all 3 votes; only a unanimous
   negative can flag** (precision-first).
3. **Round 3 — verify the verifier (new):** the negative reason names the
   allegedly-missing component (`_missing_components()` extracts it); each
   named component is re-checked **alone** via the chunked full-text
   extraction pipeline (`_extract_evidence`) per source — retrieval heuristics
   can't be trusted to fetch component evidence (davidson2025's near-verbatim
   sentence shares ONE lexical token with its component and loses the cosine
   rank too), but extraction reads the top chunks and the judge is stable on
   short single claims. All components found somewhere → the flag refuted
   itself → dropped. A genuinely absent component survives.

**i-c (ALCE):** `partial_support` now means *the union of cited evidence fails
to entail the claim* (recall). The precision side ships too: when recall
passes, each individually-unsupporting source gets one union-minus-it probe;
if the rest still entails, the claim gets `over_citation` (new field:
`{"sources": [{paper_id, source_title}]}`) — a grey "over-cited?" chip, note,
and filter in the viewer, exported in review.json + the repair brief.

`viewer.py` — over-cite chip/note/filter/card-class/CSS; partial note says
when a flag was escalation-checked. `verify_my_text.py` — `--partial-check`
help rewritten (still opt-in). Tests: `tests/test_partial_support.py` grew
from 9 to 22 (rounds, probe, over-cite, viewer markup); suite 250 green.

## Validation (live, ~10–15 cents total across 6 passes)

`benchmarks/partial_check_validation.py` — replays `_partial_flags` on the
flagged claims of a finished run, zero re-decomposition (`--dry-run` = no
API). Final state on the 7 re-audited paper1 flags:

| claim | before | after | why |
|---|---|---|---|
| t8 | false alarm | **CLEARED** (round 3: extraction finds davidson's "initial economic lead becomes bigger and bigger" + korinek's labor-share text) | |
| t36 | false alarm | **CLEARED** (round 1/2) + over-cited: drago2025 | |
| t65 | false alarm | **CLEARED** (round 1, hendrycks abstract) | |
| t74 | false alarm | **CLEARED** + over-cited: erdil2023 | |
| t69 | real gap (wrong sevilla2024.txt) | **CLEARED — corpus-correct**: the file was since replaced with the real Epoch paper, and the probe verified the Acemoglu component is genuinely in acemoglu2024 | |
| t44 | false alarm (old basis) | **still flagged, new basis**: "an early lead may compound until one power pulls clear" is in NEITHER cited source; the original hand audit called that phrase authorial and marked t44 "needs owner review" — the nudge now points exactly there. Treated as correct. | |
| t28 | false alarm | **CLEARED (7/5 afternoon)** — the blocker was never the matcher: PyPDF2 extracts anthropic2024.pdf as letter-spaced garble ("M e a s u r i n g …"). `read_source_pages` now detects that shape and falls back to poppler's **pdftotext** (`_looks_letter_spaced` + `_pdftotext_pages`, CACHE_SCHEMA 4→5 so the no-LLM upgrade path re-splits sentences); the clean index holds "each successive model generation is more persuasive than the previous" nearly verbatim and round 1 clears. NO re-fetch was needed; validated live from the real run-dir (the validation script self-upgrades pre-schema-5 caches). | |

Net: **all 6 spurious flags gone**, no real gap suppressed. Validation exits 0
with zero known-open items.

**Over-cite precision (honest note):** three nudges on paper1 — t36 drago2025,
t74 erdil2023, t28 hackenburg2025. The probe now requires a **unanimous
all-votes verdict** (a single lenient call was enough before — that produced
the t28 nudge, and while it survived the higher bar, the bar closes the fluke
class). The t28 nudge is rubric-consistent but humanly debatable: the judge
treats "even if the gains … appear to be levelling off" as the writer's
concessive framing (rule 0), so hackenburg2025 — the source for exactly that
half — looks unneeded. It stays the mildest nudge (grey chip, dismissible);
worth an owner look before default-on.

## Findings worth keeping (they drove the design)

- **More context makes flash-lite WORSE as a judge.** A 10-sentence lead +
  8 sentences + 8 claims per source flipped a clean CLEAR (t36) into a 3-0
  false flag with absurd reasoning. Short clean passages are the judge's
  stable regime — this repo already knew that (runs 4/5); it generalizes to
  the combined judge.
- **Compound claims break component aggregation.** Three passes produced three
  DIFFERENT "missing" components for the same t8, each visibly present in the
  passage. Judge-level fix attempts (unanimity, more votes) don't help — the
  probe-the-named-component pattern does.
- **Ranking against a compound claim dilutes per-component relevance** — the
  reason round 3 re-retrieves per component via extraction instead of reusing
  the claim-ranked context.
- korinek2023's decomposition cache is broken (8 LaTeX fragments / 72 pp) —
  worth a re-decomposition look someday; round 2's verbatim-sentence arm was
  added exactly so this class of cache damage can't blind the check.

## Cost shape

Round 1: 1 vote-set per multi-cite supported claim (2–3 small calls; ~11
claims on paper1). Round 2 only on negatives (full 3 votes). Round 3 only on
would-be flags (extraction over top chunks per source per component — the
expensive step, a few dozen small calls, but rare by construction). Over-cite:
≤3 single calls per fully-covered claim. All opt-in behind `--partial-check`.

## What's left before default-on (Phase 2, post-7/7)

1. ~~Re-fetch anthropic2024 (corpus fix) → expect t28 to clear.~~ **DONE
   (7/5 afternoon), better than planned:** no re-fetch — the PDF was fine,
   PyPDF2's extraction wasn't. Letter-spaced-garble detection + pdftotext
   fallback at the single read boundary (`source_decomposer.read_source_pages`)
   fixes the whole macaskill2025 class; CACHE_SCHEMA 5 re-splits sentence
   indexes on next run with NO LLM calls. Validation now fully green: 6/6
   spurious flags cleared, t44's correct nudge stays, exit 0. Caveat: a
   previously-garbled source's cached decomposed CLAIMS stay garble-derived
   until a real re-decomposition (anthropic2024's happen to be clean — the
   decomposition LLM had decoded the spacing).
2. **One 3-paper gate run** (`benchmarks/check_all.sh` on fresh paper1 +
   bentonite + chimpanzee, ~$0.5, owner-approved spend) with
   `--partial-check` on — flags must not disturb any gated verdict (they
   can't, by construction, but the gate is the rule) — then flip the default
   and update CLAUDE.md/ROADMAP (both deliberately NOT edited on this branch
   to avoid cross-stream merge conflicts).
3. `--estimate` doesn't model partial-check calls yet (it didn't before
   either) — minor, note only.
4. ~~Optional: teach `_missing_components` the "X is contradicted by Y" reason
   shape so contradiction-flags get probed too (t28's second blocker).~~
   **DONE (7/5 afternoon):** three rule-5 contradiction shapes ("the claim that
   X is contradicted", "the passage contradicts X", "X is contradicted by Y")
   now extract the component, with evidence-tail trimming and a pronoun-subject
   guard. Probe semantics unchanged: a component the probe finds backed ⇒ the
   contradiction call refuted itself; a genuinely contradicted one stays
   flagged. Offline-tested (suite 251 green). t28 itself still needs blocker
   #1 (the garbled anthropic2024 text) — the probe can't find components in
   spaced-letter garble.
