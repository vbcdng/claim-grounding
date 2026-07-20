# Subject-entity guard (2026-07-12, Fable final session)

## The failure it kills
WiCE train2, claim `waleedmajid` (t26): *"Majid has played in several World
Cup of Pool events representing Qatar, including reaching the quarter-finals
at the 2015 event."* The cited source is a 2 KB paywalled archive stub of an
AZBilliards news page whose entire text **never contains the string "Majid"**.
The fulltext-extraction fallback surfaced the team score-table sentence
("Qatar put on a masterful performance to reach the quarter-finals…") and the
judge accepted it 2-0 → `supported`. WiCE labels the row not_supported. This
was the first false-support on a refuted row in any labeled eval — the hard
stop-and-report metric (`wice_bench.py` banner) caught it the same evening the
batch ran.

It is the verdict-path twin of the Finland/Agta class: the round-7
deterministic named-specific entity check exists only in the display-layer
covering audit, so an entity-free "proof" could still buy the verdict itself
on the extraction path.

## The rule
When a claim **leads** with a proper-noun run (its subject), a
`llm_fulltext`/`combined_fulltext` positive is accepted only from a source
whose **full text** (not just the shown window) contains at least one subject
token, diacritic-folded (`Ljubojević` ↔ `Ljubojevic`). Rationale: a source
genuinely about X names X at least once — pronouns only substitute after a
name is introduced. The guard checks the whole source, never the window, so
mid-article windows that say "he" cannot false-fire.

Deliberately narrow:
- **Strictly leading**: token 0 of the first sentence (after an article).
  "The first is diabetes… Shin and colleagues found…" has NO leading subject
  — buried attribution shapes never arm the guard, because a paper's
  extracted body text can legitimately lack the author byline (real sighting:
  eggs t29, `shin2013.txt` contains "Shin" 0 times yet is the right source).
- **Frozen common-words set** (in code, never the system dictionary —
  verdicts must be machine-independent): disarms capitalized-because-initial
  ordinary words ("Reviews of…", "Without dialogue,…", "Nor would…", "She
  competed…", "Many Americans…" — all real corpus rows). Singular-aware
  ("Americans" folds to common "american").
- **Cosine-path positives are NOT guarded** — zero observed failures there;
  widening the guard would risk gate rows for no evidence. Watch item.

On fire the claim goes/stays unsupported with an explicit reason ("subject
'majid' is never mentioned in the cited source's text…") and carries
`subject_guard: {subject, missing_from: [pids]}`. Both rescue paths honor it:
`_component_rescue` is skipped when every cited source is guarded, and
`arbiter.rescue` skips guarded sources when locating proof windows — a
verbatim-but-subjectless quote passes the quote gate and could otherwise win
a ±2-sentence window judgment, re-buying the exact false positive the guard
removed.

## Blast radius (measured before writing the fix)
Offline scan of every `analysis.json` on disk: 57 claims supported via the
fulltext paths with a checkable leading subject. The guard fires on **2**:
- `waleedmajid` (the target — WiCE not_supported), and
- `panzerdragooniizwei` (source is a Japanese-language Sega list page; the
  game's name appears only in katakana, so Latin-token matching reads absent
  — and WiCE also labels the row not_supported, so the fire agrees with the
  label). Cross-script aliasing is the one known accepted miss-direction.

The remaining 9 naive-scan candidates are all disarmed by the strict rules
above (sentence-initial common words, buried attributions, paper1 gate t18
"Nor would…" included).

## Validation (all same-session)
- 19 offline tests (`tests/test_subject_guard.py`); full suite 836 OK.
- Fresh train2 scratch run: guard fired exactly once (Majid), waleedmajid
  red, nevilleprice arbiter-rescue still flips, **false-supports 0/0**,
  verdict agreement 65% (was 58% pre-guard), adjudicated 80%.
- Mandatory 6-block gate: paper1 24/24, bentonite 5/5, chimpanzee 12/12,
  coverage essay 8/8, bohemia 2/2, pots 2/2 — zero regressions.

## Addendum (same session): non-leading entity extension SHIPPED
The wildskin class (mid-sentence "the film stars Marilyn Castonguay", star
absent from the source) is covered by `_claim_entity_sets`: in addition to
the leading subject, every NON-LEADING MULTI-TOKEN proper-noun run is a
checkable entity (single-token non-leading runs stay out — too alias-prone;
adjective compounds like "Egyptian-born" and common/nationality words are
filtered). Re-scan with the filter: **2 fires / 39 fulltext-supported
claims, both correct, 0 false** (the earlier naive scan's "Egyptian-born
French" false fire is gone). Semantics: a SINGLE source must contain every
entity to prove the claim alone; the combined judge needs each entity only
in the UNION of contributing sources; component rescue is blocked only when
an entity is absent from every source. 25 tests; suite 846 OK; second
6-block gate clean (commit c20bbaa).

## Addendum 2 (2026-07-17): collapsed-run disarm — the first HONEST gate run

The "second 6-block gate clean" claim above was vacuous: the guard landed
21:40/22:09 on 2026-07-12, AFTER that day's 14:15 fresh runs, and the scorer
reads whatever analysis.json is on disk — it scored pre-guard outputs. The
first fresh runs against the guard (2026-07-17, verdict-path-bug gate) failed
paper1 t27 deterministically, twice:

- t27's tail_rescue tail leads with "Frontier AI is already a potent
  instrument of persuasion…". `_subject_tokens` drops 'AI' (< 3 chars) and
  the run collapses to the single generic token **'frontier'** — which
  salvi2025 (the GPT-4 persuasion RCT, proving the claim's substance) never
  prints. The guard dropped the true fulltext positive; t27 flipped to a
  false unsupported.
- Fix: a ≥2-token leading run that collapses to ONE checkable token is a
  fragment of a longer phrase, not a checkable subject → guard off (the
  docstring's conservative direction). Finland (single raw token) and
  Castonguay (fully-kept multi-token) are unaffected; all 25 prior guard
  tests pass unchanged, +2 new (collapse disarms / fully-kept still arms).
- Commit 71904be on branch fix/verdict-path-bugs; gate re-run fresh after it.

Process lesson: check_all.sh cannot tell a stale analysis.json from a fresh
one — "gate clean" after a matcher change is only meaningful if the runs
postdate the change. A staleness warning was added to check_all.sh.
