# Adversarially-verified findings — 2026-07-17

The corpus audit's most consequential claims (the false-positives — the
exceptions to the "safe error direction" story) were re-checked by independent
Fable skeptics instructed to REFUTE them (defend the pipeline). None of the
false-positives could be refuted; two trace to concrete verdict-path bugs. This
doc records only what survived adversarial verification.

## Two verified verdict-path bugs (false-support MECHANISMS)

### Bug 1 — near-verbatim cosine short-circuit bypasses contradiction signals
**synth_11 t5, synth_12 t5 — CONFIRMED false-positive.** The cited source
(`prizerec.txt`) says the early newspaper report of a prize winner was
**retracted** and the committee minutes name a DIFFERENT winner. The claim asserts
the retracted winner. Verdict: `supported`, via the ≥0.97-cosine "near-verbatim
match" method (0.982 / 0.979). The contradiction WAS detected downstream —
`partial_support` logged a 3-0 "directly contradicts" vote and the arbiter's
`conflict` field quoted the retraction verbatim — but the near-verbatim cosine
path had already locked in `supported`, and those signals never downgraded the
verdict field. **Fix:** the near-verbatim short-circuit must still consult the
conflict/partial-support signals before finalizing `supported`; a gate-verified
contradiction should block or flip it. A ≥0.97 lexical match to a sentence that is
itself a retraction is not proof.

### Bug 2 — tail_rescue judges a window contaminated with the claim's own text
**coverage_gate_run essay t9 — CONFIRMED false-positive on a must-flag row.**
tail_rescue flipped the verdict to `supported`, but the "proof" the judge quoted
("AI capability growth ... is discontinuous and compute-driven, leaving no
comparable historical baseline") is **verbatim the claim's own tail**, not source
text — verified absent from the real PDF (`gruetzemacher2021.pdf` lines 747-749:
the actual next sentence is "Long-term forecasts require substantial time to
verify..."). The retrieved window had the claim tail appended to it, and the judge
self-proved against it. Every other signal — covering (4/6 uncovered),
partial_support (3-0), arbiter (`proofs: []`), a prior Fable pass, AND the human
author ground truth (`coverage_ground_truth_essay.json` t9 must_flag, "should be
unsupported") — says unsupported. Only tail_rescue disagreed, and only because its
window was contaminated. **Fix:** tail_rescue must strip/deduplicate the claim
text from the window before judging; a window that contains the claim sentence
verbatim cannot be used as its own evidence. This is the more dangerous of the two
because it manufactures proof from the claim itself.

## Confirmed content false-positives (not mechanism bugs, but real misses)
- **WiCE dev1 t11 (Viber) — CONFIRMED.** Claim says popular in "Nepal"; source
  says "Iraq, Libya and **Sri Lanka**" in the exact enumeration the claim mirrors.
  "Nepal" appears 0 times — and the pipeline's OWN evidence window contained "Sri
  Lanka" yet it voted 2-0 supported. A direct contradiction the judge read past.
- **WiCE dev1 t16 (Wild Skin) — CONFIRMED.** Claim "stars Marilyn Castonguay";
  the name appears 0 times (only the director, Ariane Louis-Seize, is named). The
  python/erotic-experience half is supported, but the load-bearing casting fact is
  absent — a blanket `supported` on a partially-present claim (the over-support
  class from the other direction).

## Refuted — my own earlier flags that did NOT survive verification
Adversarial verification also overturned two of my earlier over-support flags —
recorded here because correcting them matters as much as confirming the rest:
- **printing-press t5 — REFUTED.** The claim bounds itself to literacy "into this
  period" (the Reformation era); the source states ~18% pre-17th-century directly.
  The 71%-illiterate figure I cited as a conflict is a SEVENTEENTH-century number,
  after the claim's window — no contradiction. Pipeline `supported` is correct.
- **eggs t8 — REFUTED, and it corrects my over-support audit.** mcnamara1987 DOES
  state the downregulation mechanism verbatim ("subjects compensated ... by
  decreasing ... endogenous cholesterol synthesis"; "suppression of endogenous
  cholesterol synthesis"). The only gap is lexical ("liver" vs "endogenous
  synthesis" — a common-knowledge paraphrase). So t8 was NOT a citation-target gap
  as I claimed; it is another EVIDENCE-DISPLAY artifact (the covering snippet and
  the shown evidence both missed the mechanism sentence that is in the source).
  This strengthens Finding 3 and means all three eggs "over-support" cases
  (t8/t18/t29) were display artifacts, not real over-credit.

## Verification scorecard
False-positives/over-support put to the skeptics: 6. **CONFIRMED 4** (synth_11 t5,
synth_12 t5, WiCE t11, WiCE t16), **REFUTED 2** (printing t5, eggs t8). The "safe
error direction" headline survives and is if anything reinforced (two of my own
false-positive flags were themselves too harsh). The 4 that stand are REAL, and
two are systematic verdict-path bugs, not one-off judge slips. Lesson: single-pass
Fable labels over-flag some over-supports; the adversarial second pass is load-
bearing and should gate any finding before it drives a code change.

## Bonus: source-adequacy precheck (prototype, deterministic, 8/8)
The 23 not_rulable labels come from "present but a stub" sources. A deterministic
detector (`benchmarks/gold_labels/source_adequacy_proto.py`) flags them before
judging so their claims become `not_rulable`, not `unsupported`:
- markers: paywall preview ("preview of subscription content", "log in via an
  institution", "buy print or eBook"), Cambridge Core scaffold ("render date:",
  "has data issue:", "published online by Cambridge University Press"), Wayback
  boilerplate ("Internet Archive / captures / COLLECTED BY"), scraped nav (mailing
  list, country-dropdown blob), plus a reference-list-only / near-empty-body check.
- Tested 8/8: all four known stubs (schneider1982, simon1978, harpenden,
  hammurabi) flag; four full papers (rong2013, shin2013, zhong2019, barnard2019)
  pass clean. **Fix:** run this precheck at ingest/judge time; a stub source →
  `not_rulable` + a report entry, excluded from the unsupported count (same
  treatment citation-scope already gives scoped claims).
