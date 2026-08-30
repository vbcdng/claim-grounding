# Where /walkthrough comes from — the human findings a machine check missed

*(Release note: this is a distilled version of the dev repo's internal
walkthrough log — internal run paths, commit history, and status tracking
removed. The item numbers are kept because `/walkthrough` cites them.)*

On 2026-07-07 the tool's author hand-walked a finished run's viewer — reading
every evidence sentence as a skeptical human — and found **12 substantive
problems that the agent's own self-check had missed entirely**. The
self-check had verified that buttons, chips, filters, and legends render and
that the pipeline ran; it never actually *read* the evidence and asked
"would this convince me?". The `/walkthrough` command encodes the author's
way of reading so an agent can repeat it on any run.

All findings below were subsequently fixed and validated against the
hand-audited benchmark papers; they are kept here because each one is a
*class* of failure worth re-checking on new runs.

## The author's findings (items 8–14)

- **8. Boilerplate leaking into evidence.** Evidence sentences contained web
  bylines, photo captions, publish dates, even a page of site navigation
  glued into one "sentence". → source text hygiene + evidence-length caps.
- **9. Partial support on single-citation claims.** A claim with one specific
  figure not present in any shown evidence was plainly "supported" because
  its *other* components matched. → component-level partial-support flags +
  a hunt for where the missing component might be supported.
- **10. Multi-source verdicts unexplained.** N sources cited, one supports —
  the card showed cryptic "not supporting" rows from the others with no
  explanation that supported = ANY cited source supports. → the OR-semantics
  note on cards ("supported via X; the others did not independently support
  it — best passage shown").
- **11. Evidence too thin to judge.** A 7-word fragment as sole proof, or a
  quote meaningless without its surrounding sentence. → multiple supporting
  sentences + context windows.
- **12. Secondhand evidence.** The supporting sentence in the cited source
  was itself citing another paper. → the "secondhand evidence?" chip: cite
  the original, not the survey.
- **13. A false unsupported.** The source stated the claim almost verbatim;
  retrieval never surfaced the passage. → the component-rescue path
  (re-judge on windows found for each named-missing component).
- **14. Co-cited sources disagreeing silently.** One cited source supported
  the claim while a co-cited source's best passage argued the opposite —
  shown as a plain "supported". → the "sources may disagree?" chip.

## Second-pass findings by the agent using this command (items 15–19)

- **15. Quoted spans never searched.** Claims quoting a source verbatim came
  out unsupported because retrieval missed the exact sentence. → quoted
  spans are now string-searched in the cited sources before judging.
- **16. Missing source files invisible on multi-citation cards.** → an
  explicit "source file missing" row per absent source.
- **17–19.** Parser and evidence-hygiene gaps (missing-component verbs,
  over-cite nudges on explicitly-attributed sources, reference-list
  fragments surfacing as "closest passage").

## The lesson the command encodes

For every card, the question is never "does the card render correctly" but
**"if I trusted this card, would I be misled?"** — decompose the claim into
its atomic components (every number, entity, causal link, date,
attribution) and compare each one word-by-word against the quoted evidence,
including on high-confidence supported cards.
