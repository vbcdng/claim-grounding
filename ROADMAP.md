# Roadmap — living priority list

**This is a living document — the single place that says what we work on next
and in what order.** Rules (author, 2026-07-20):

- Every priority change is written here **in the same commit** as the decision
  that caused it, with a line in the changelog below. Nothing is decided "in
  chat only".
- Every past version is preserved automatically — the file is in git, so
  `git log -p ROADMAP.md` replays its full history. Never edit history;
  just commit the new state.
- This file holds the ORDER and one line per item. The plain-language detail
  for every item (what/why/size) lives in
  **`docs/PRIORITIES_DISCUSSION_2026-07-20.md`** — item IDs (A1, E3, ...)
  refer to that document. Keep the two in sync when priorities move.
- The previous roadmap (the 2026-07-02 build phase — everything in it was
  built) is archived at `docs/archive/ROADMAP_PHASE1_2026-07-02.md`, together
  with its still-useful notes (judge-drift history, prior-art tiers, gate
  economics).
- **This file is PUBLIC** (author decision 2026-07-20): it ships in the public
  repository together with `docs/PRIORITIES_DISCUSSION_2026-07-20.md`. After
  any change here, push the updated copies to
  `github.com/vbcdng/claim-grounding` in the same session — and write nothing
  in either file that can't be public.

## Changelog

| Date | Change |
|------|--------|
| 2026-07-20 | First version of the living list. Author picked **precision** as the goal for the next stretch → precision plan on top; then the cheap wins; then everything else, explicitly unordered. Old build-phase roadmap archived. |

---

## 1. Current priorities — the precision plan (in this order)

Goal chosen by the author 2026-07-20: make the tool's verdicts right more
often, in both directions. Steps that change judgments (marked "gate") must
each pass the full re-check on the hand-audited papers before shipping, one
at a time.

| # | ID | Item | Gate |
|---|----|------|------|
| 1 | A1 | Partly-proven rejected claims get a partial verdict (biggest measured error class, 58/236; design + test cases ready; includes the structured "missing parts" judge field) | yes |
| 2 | A4 | Numbers + cause-vs-effect direction check (kills the dangerous false "supported" cases our break-it test found) | yes |
| 3 | A6 | Verdict stability — same text in, same verdicts out (majority vote on borderline finals) | yes |
| 4 | E3+E2a | Better first-pass sentence finding: keyword search next to the meaning-based one + compare the local matching model against alternatives (offline, ~free to score) | yes when shipped |
| 5 | E1 | Judge-model sweep with the written qualification procedure (after 1–4, so a better model doesn't hide what the fixes did) | yes |
| 6 | B2 | Better benchmark — runs in the BACKGROUND from day one (extend the break-it test first; then a test set that can score partial verdicts) | n/a |
| 7 | E6 | Citation span — how many sentences one citation covers | yes |
| 8 | A5 | Completeness of the shown proof sentences | yes |

## 2. Very cheap things (hours each) — fill-in work between the steps above

Not a priority order; grab whichever fits the moment.

| ID | Item |
|----|------|
| A2 | Show the RIGHT proof sentence on the card (design done, display-only, verdicts can't move — worth doing first of everything) |
| A3 | "Source file missing" becomes its own grey category, not "unsupported" |
| B3 | Malicious-source test (booby-trapped PDFs → measure; turns a stated unknown into a number) |
| C4 | Plug-in point for a user's own paywalled-paper download script (inbox + ingest already exist) |
| D7 | Try Elicit's new API as a text producer (one afternoon; mind the $49/month) |
| D5a | Survey what retraction/quality databases are freely available (the lookup feature itself is a later, medium item) |
| B4a | Automatic test runs on every push to the public repo (~30 min) |
| B4b | Security scanning toggles on the public repo (~15 min) |
| B4c | Standard web-validator pass on the generated report HTML (~2 h) |
| — | Manual source fixes parked since July: OCR kornai1994, verify comin2010, identify lin2025 |

## 3. Everything else — NOT ordered by priority

These are real, wanted, and waiting; the order below means nothing. Detail
under their IDs in `docs/PRIORITIES_DISCUSSION_2026-07-20.md`; the ones
without an ID live in `IDEAS.md`.

- **B1** — automatic spot-check ("trust report") when the tool runs on a new
  kind of text
- **B4d–f** — rest of the testing batch: parser stress tests, broken-PDF
  feeding, test-strength measurement
- **C1** — one `claimg` command + standard pip install (loudest tester wish)
- **C2** — accept ordinary web pages as sources
- **C3** — show the sentences around each proof sentence
- **D1** — importer robustness on real papers (already in motion — waiting on
  the author's round-2 opinion file)
- **D2** — understand the author's own uncited claims (summaries vs original
  ideas vs overclaims)
- **D3** — follow a citation chain to the original source
- **D4** — suggest other relevant papers, including contradicting ones
- **D5b** — the retraction/quality lookup feature (after the D5a survey)
- **D8** — run the full repair loop end to end once and write it up (also the
  best demo story)
- **E2b** — finish the local-model backend (qualify a recommended local model
  + the three known small tasks)
- **E4** — source decomposition v2 (redesign; CLI flag removed 2026-07-16)
- **E5** — internal clean-up: one structured typed check instead of many small
  rules (needs a quiet period)
- Speed/cost items from IDEAS.md: batch the own-claims classification into one
  call; keep one Claude Code session alive instead of one per call;
  provider-side caching of source context across judge calls
- Internal refactors from IDEAS.md: shared viewer chrome module; main()
  pass-pipeline abstraction; unify the two file-hash schemes (only with a
  cache-schema bump)
- Ingestion ideas from IDEAS.md: decomposition-quality benchmarks
  (SciClaimHunt/BioClaimDetect); GROBID as the last rung for papers in no
  database (only if unindexed papers actually show up); cross-source claim
  dedup; nanopublication-shaped output (PROV-O export already ships);
  Claimify-style disambiguation gate

## 4. Consciously parked (decision needed before anyone touches them)

- **Argument map / cruxes** (incl. the P1–P5 audit fixes and the two unmerged
  branches) — author ruling in the submission: paused in favor of core quality.
- **D6** — figures vs text: explicitly not started, park until Theme A is done.
- **Docker image** — promised only if testers ask; nobody has asked.
- **README humanizing pass** — removed from the list by the author 2026-07-20.
