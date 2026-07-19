# /walkthrough — human-grade walkthrough of a finished run's viewer

Walk through a finished run the way the OWNER does: read claim cards as a
skeptical human reader, not as an engineer verifying that features render.

**Why this prompt exists (2026-07-07):** the owner walked `data/paper1_haiku`
and found 12 substantive problems the agent self-walkthrough missed entirely.
The self-walkthrough checked that buttons, chips, filters, and legends exist
and that the pipeline ran — it never actually READ the evidence sentences and
asked "would this convince me?". Every rule below traces to a concrete miss
from that day (docs/WALKTHROUGH_OWNER_TODO.md items 8–14 +
data/paper1_haiku/review_owner_full_2026-07-07.json).

## Input

`$ARGUMENTS` = the run's output dir (default: ask, or the most recent run).
Work from `analysis.json` — it contains everything the viewer shows. Open the
actual source files in `sources/` when a verdict needs checking.

## Mindset

You are the author reading their own verification report. For EVERY card you
inspect, the question is never "does the card render correctly" but:
**"If I trusted this card, would I be misled?"** Assume the pipeline has bugs
and the judge makes mistakes; your job is to catch them by reading, exactly
like a careful human. Do not skim evidence — read every quoted sentence in
full and compare it word-by-word against every component of the claim.

## Coverage

Inspect ALL of: unsupported claims, supported claims with confidence ≤ medium,
claims with any chip (partial support / over-cited / citation needed /
disagreement), multi-citation claims. Plus a random sample of ~10 plain
supported-high cards (the owner found problems in "supported/high" cards too —
t8, t10, t18 were all high confidence).

## Per-card checklist (all of these, every card)

1. **Full-component entailment.** Decompose the claim into its atomic
   components (every number, named entity, causal link, date, attribution).
   For each component, point to the evidence sentence that entails it. A card
   is over-supported if ANY substantive component has no evidence — even on a
   single-citation claim where partial-check doesn't fire.
   *(Miss t6: "under 5% for the EU" appeared in no evidence; verdict said
   supported because US+China matched. Miss t8: "an early lead can grow ever
   larger" supported by neither source.)*

2. **Read the evidence sentence as prose.** Is it garbled, boilerplate, or
   meaningless out of context? Flag: bylines/dates/photo captions glued in
   ("5, 2025 By Konstantin F. Pilz…", "Published On 19 Jun 2026…"), site-
   navigation dumps, cryptic fragments ("Effect of Proxy Model Alignment."),
   context-free snippets that only make sense inside the source, and
   `sentence: null`. Each of these is a finding even when the verdict is right.
   *(Misses t6, t13, t18, t23-alternative, t24, t25.)*

3. **Multi-citation claims: account for every marker.** For each cited source,
   ask what THIS source contributes. Flag when the verdict rests on one source
   and the others show nothing relevant — and say whether that means
   over-citation, a wrong source, or an evidence-retrieval miss. Flag when a
   co-cited source's evidence argues the OPPOSITE of the claim.
   *(Misses t8, t14, t22.)*

4. **Unsupported verdicts: try to overturn them.** Open the actual source
   text (`sources/<key>.*`, or pdftotext it) and search for the claim's key
   terms yourself. The pipeline's retrieval misses things; a human grep does
   not. If you find supporting text the judge never saw, that is a
   false-unsupported — one of the most damaging bug classes.
   *(Miss t23: the source explicitly stated Mythos's cyber capability; the
   judge said it didn't. The owner found it by reading the article.)*

5. **Secondhand evidence.** Does the supporting sentence itself cite another
   work ("(Baumol …)", "[12]", "X et al. argue…")? If yes, the author may be
   citing a middleman — flag it and name the original source to cite instead.
   *(Miss t9.)*

6. **Source genre and authority.** Is the cited artifact appropriate for how
   the text uses it? A slide deck cited as "the UNDP describes…", a news
   aggregator where a primary announcement exists, a methods paper cited for
   an empirical industry fact — all findings, even when technically supported.
   *(Misses t10, t18, t24.)*

7. **Relevance of the source at all.** Sometimes a cited article is simply
   about something else (t22's DARPA piece was about open-source tools, not
   frontier-firm services). Read enough of the source to say what it is
   actually about.

## Output

Two separated lists, like the owner's own review splits them:

1. **Tool findings** (pipeline/viewer bugs and UX) → append to
   `docs/WALKTHROUGH_OWNER_TODO.md` under a dated heading. Include the claim
   ids as evidence.
2. **Text findings** (what the author should fix/re-source) → a review-style
   list per claim id: what component is unsupported / what source to seek /
   what wording to align — the same shape as the marks in `review.json`, so it
   can feed the rewrite flow.

For every finding, name the claim id, quote the exact evidence text at issue,
and state concretely what a human would conclude. Rank by how badly the card
would mislead a trusting author.

Do NOT report: features that merely exist and work, cosmetic issues already in
`WALKTHROUGH_FIXES_TODO.md` (account B's) or `WALKTHROUGH_OWNER_TODO.md`, or
restatements of known limitations (grey-card OMITTED badge). Check those files
first so every reported finding is new.
