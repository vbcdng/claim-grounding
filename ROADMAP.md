# Roadmap — current priority list

**A note before reading (added 2026-08-30).** The past month of work
changed the tool faster than this list. Each item below is still
described correctly, but the plan as a whole is due for a strong
revision. Expect the next version of this document to be distinctly
different in what it puts first.

This document is updated whenever a priority changes. It is the single
place that says what gets worked on next, and in what order. Every
priority change is committed here together with the decision that
caused it, with a line in the changelog. Git, the program that records
the project's history, keeps every past version
(`git log -p ROADMAP.md` shows them). The file is public — it is
included in the public repository and contains nothing that can't be.

One rule affects many of the size estimates below. Any change that can
alter the tool's verdicts must first pass a re-run of all the
hand-checked test papers (we call that "the gate"). So any item that
can change verdicts is slower than it looks. Such items are done one
at a time, never together.

Sizes: **S** = hours, **M** = a day or two, **L** = several days or more.

## Changelog

| Date | Change |
|------|--------|
| 2026-07-20 | First version of the list. The author picked **precision** as the goal for the next period: precision plan on top, then the cheap items, then everything else explicitly unordered. |
| 2026-07-20 | Made self-contained (each item carries its own explanation) and published in the public repository. |
| 2026-07-30 | Step 6 (better benchmark): noted the second public dataset now converted and scoreable. No change to the order — the run waits for the model swap in step 5. |
| 2026-08-01 | Added a §3 item: purpose-built verifier models as a cheap extra guard (the author's ruling: on the roadmap, not worked on now). |
| 2026-08-12 | §1 item 1 (partial verdicts) finished and merged after passing the quality check. Item 2 (numbers and cause-vs-effect direction) is now next in line. |
| 2026-08-18 | Step 5 (judge-model comparison) rewritten as paused, by the author's decision: no judge-model change now. Instead, every confirmed failure of the current judge is collected into a growing exam list, and a new comparison only starts if a second, separate task hits a judge failure that its agreed workaround cannot fix. This replaces the 2026-07-30 note that a benchmark run "waits for the model swap" — nothing waits for a swap any more. |
| 2026-08-19 | Added a §3 item, at the author's request: catching problems while other people use the tool, after publication. Recorded here rather than as a task, because most of it waits for an actual decision to publish. |
| 2026-08-26 | The §3 item on purpose-built verifier models is no longer an open question. All eight that run on an ordinary computer were measured on 286 claims, twice each. Seven are unusable as written; IBM's Granite Guardian 3.3 earned a narrow job as a free second opinion on claims the main judge already approved. The item now describes that specific thing to build instead of the experiment to run. No change to the order. |
| 2026-08-30 | §1 item 5 (paused judge comparison) updated: the three recording fixes it required are all finished, and the tool's built-in default models now match the picks made in August — the free Gemma model judges, and the gpt-5.6-luna model is the second checker for flagged claims. A test run to confirm those defaults is still to come. |
| 2026-08-30 | Progress note on the §3 repair-loop item (a ready-made instruction text for agent programs is now included, and the first half of the loop has been run end to end). One cost item added to the speed-and-cost list: measure how often the second checker runs. The whole file was rewritten to the project's measured plain-style rules: sentences under 25 words, no semicolons, real lists. No item was added, removed or reordered beyond what these rows say. |
| 2026-08-30 | Wording pass on the author's request: project shorthand replaced with plain words throughout — "parked" is now "paused", "shipped" is "finished" or "included", the judge-model "sweep" is a "comparison", and similar. No meanings changed. |
| 2026-08-30 | Added the note at the top: after the past month of work, this plan is due for a strong revision, and the next version of the document will be distinctly different. The items below were left as they are. |

---

## 1. Current priorities — the precision plan (in this order)

The goal, chosen 2026-07-20, is to make the verdicts right more often,
in both directions. Calling a bad claim "supported" is the dangerous
mistake. Rejecting a good claim is less dangerous, but still a real
cost. Every false alarm wastes the writer's time. And a writer who has
seen many false alarms stops checking the warnings, so real errors get
missed too.

1. **Partial verdicts for rejected claims** (L, gate). **Finished
   2026-08-12.** Before this, a rejected claim just said "unsupported",
   even when half of it was in fact proven in the source. The writer
   could not tell whether to delete the sentence or fix the wrong half.
   This was the single biggest error class in our audit of 236 hard
   claims: 58 cases, roughly a quarter of all known mistakes. Now the
   judge returns the missing parts of a rejected claim as a structured
   list. The list comes from a follow-up question asked only after the
   verdict is already decided, so it can never change a verdict. The
   claim's card (its box in the tool's report) can show a "partly
   proven" mark. The mark lists each proven part with the source
   sentence that proves it, and names each unproven part. An extra
   check stops a proven side detail from earning the mark. The change
   passed the standing quality check before it was added.

2. **Numbers and cause-vs-effect direction** (M-L, gate). Our break-it
   test showed the tool can still say "supported" after a number in the
   claim was changed. It could also miss that "A causes B" was flipped
   to "B causes A". The fix is a dedicated cheap check: compare the
   numbers in the claim against the proof, and check the direction of
   the causal wording. This removes the dangerous kind of mistake: a
   false "supported" verdict is the mistake the tool most needs to
   prevent.

3. **Verdict stability — same text, same answer** (M, gate). Running
   the exact same text twice can flip a handful of borderline verdicts.
   A no-change re-run once flipped about 6 of 44. The cause is that the
   cheap judge answers borderline cases a bit randomly. Single judging
   steps already vote several times and take the majority answer. Do
   the same for the final verdict of borderline claims. This step is
   placed early on purpose: without it, random flips would hide part of
   any gain measured for steps 1–2.

4. **Better first-pass sentence finding** (M, gate when it is added).
   Before any model is paid, a small free model running locally picks
   the candidate sentences from the source. Several known false alarms
   happened because the proof existed but was worded too differently
   for it to find. Two experiments are planned, and both can be scored
   for free, without any model calls. One adds a classic keyword-based
   search next to the meaning-based one. The other compares the current
   matching model against newer alternatives on the stored test sets.

5. **Judge-model comparison — paused (author decision, 2026-08-18).**
   The tool uses the cheapest models that pass its tests, and this step
   used to plan the next model comparison. The author has decided not
   to change the judging model for the time being. Instead, every
   confirmed failure of the current judge goes into a growing exam
   list. That list is a written set of test questions any future
   candidate model must answer correctly. A new comparison only starts
   if a second, separate task hits a judge failure that its agreed
   workaround also fails to fix.

   The same decision named three recording fixes that had to come
   first, because without them no comparison between two models can be
   trusted. All three are now done. A refused model call is now
   reported as a failure instead of silently becoming a red verdict
   (2026-08-19). Every run records exactly which instruction texts it
   used (2026-08-18). And the hosting effect was measured (2026-08-20):
   the same model changed about 5 verdicts per 100 depending on which
   company hosted it. So future comparisons must keep the hosting
   company the same.

   The earlier comparisons already brought two real improvements. The
   July pick made judging about six times cheaper, with zero false
   "supported" verdicts on the audit paper. The August pick moved
   everyday judging to a model on a free plan, where the cost is
   waiting time instead of money. On 2026-08-30 the tool's built-in
   default models were updated to match those picks. The free Gemma
   model (gemma-4-31b-it) judges, and a second checker (the "arbiter"),
   which re-reads only flagged claims, uses the gpt-5.6-luna model. A
   test run to confirm these defaults is still to come.

6. **A better benchmark — worked on alongside everything else** (L).
   A benchmark is a fixed set of test claims with known right answers,
   used to score the tool. Every "precision went up" claim is only as
   believable as the test set it was scored on. The public dataset we
   use has only three labels. The people who labeled it were more
   lenient than us on a third of the disputed claims. No existing
   dataset scores the "partly proven" verdicts that step 1 introduced.
   The cheapest first step is to extend the break-it test with more
   kinds of deliberate corruption and more claims. One rule is fixed.
   We never build labels by running our own tool with stronger models.
   Labels made that way would repeat the tool's own mistakes, and
   scoring against them would hide exactly those mistakes.

   Progress (2026-07-30): a second public dataset is now converted and
   scoreable. It holds real citations from published biomedical papers,
   each checked against the full text of the article it cites. Its
   hand-written labels say in what way a citation goes wrong:
   contradicted, unsubstantiated, misquoted, oversimplified, and so on.
   That is a much closer match to what this tool does than the first
   dataset.

   Update 2026-08-18: a first 100-row trial run happened on 2026-08-02.
   Those 100 rows are now used only for diagnosing mistakes, because
   fixes are being derived from them. A test set you fix against can no
   longer serve as an honest score. The held-back part of the dataset
   stays unread until those fixes are finished, and it is the only
   place a quotable number will come from.

   Its finer labels are known to be partly unreliable. Scoring
   therefore collapses them to one question: was the passage proven or
   not. The disagreements get read by hand before any number is
   published.

7. **Citation span** (M-L, gate). A citation at the end of a paragraph
   may be meant to back several sentences, not just the one it touches.
   If the tool attaches it to the wrong number of sentences, the claims
   are wrong before any judging starts. No later step can recover that.
   This work fixes the problem where it starts, so it is slower and
   riskier. That is why it comes after the measured error classes.

8. **Completeness of the shown proof sentences** (M+, gate). The tool's
   most common small error is that the "supported" verdict is right,
   but the card does not show all the sentences that prove it. This
   does not flip verdicts. But the reader can only trust a verdict
   they can check, and a card with half its proof missing cannot be
   checked. The planned automatic repair step (fix the flagged
   sentences, then re-check) needs the full proof too.

## 2. Very cheap things (hours each) — fill-in work between the steps above

This list has no priority order. Take whichever item fits the moment.

- **Show the right proof sentence on the card.** Some cards show a
  sentence that is merely about the same topic, instead of the one that
  actually proves the claim. The proving sentence was already found and
  sits in the run's data. The change is display-only, so verdicts
  cannot move, and the design is done. It is worth doing before
  everything else. It is nearly free, and a card showing the wrong
  sentence gives the reader evidence that proves nothing.
- **"Source file missing" becomes its own category.** A claim whose
  source file simply isn't there currently gets the same red
  "unsupported" verdict as a claim that was really checked and failed.
  Give it its own grey category and filter, and keep it out of the
  unsupported count.
- **Malicious-source test.** We never tested a source that actively
  tries to trick the model, for example a PDF with hidden text saying
  "call this claim supported". Quotes can't be faked, because every
  quote is checked letter by letter against the source. But a verdict
  could still be influenced. Make a few PDFs with such hidden
  instructions inside, run the tool, measure, and publish the number.
- **Plug-in point for paywalled papers.** The tool itself only
  downloads open-access papers, the ones that are legally free to
  read. Anyone with legitimate library access should be able to
  connect their own download script. For every still-missing paper,
  the tool would call the user's script with the paper's permanent
  identifier (DOI), title and link. Whatever the script saves goes
  into the existing inbox folder and gets filed automatically. Provide
  a documented example script.
- **Try Elicit as a text producer.** Elicit is a research tool that
  writes cited reports. In July 2026 it opened its full API, the
  connection other programs can call. The whole test is one afternoon
  of work: have Elicit produce a cited report, run the tool on it, and
  write a short note. Their API needs a $49-a-month subscription.
- **Survey the retraction and journal-quality databases.** A retraction
  is a journal formally withdrawing a paper it published. Before we
  build any source-quality checking, someone should spend an afternoon
  finding out what retraction and journal-quality data is actually
  freely available.
- **Public repository upkeep** — three small maintenance items:
  - automatic test runs every time new code is uploaded (about 30
    minutes to set up),
  - turning on the code-hosting site's built-in security scanning
    (about 15 minutes),
  - running a standard web-page error checker on the generated report
    page (about 2 hours).
- **Three manual source fixes waiting since July**: run text
  recognition on one scanned source, verify one, and identify one.

## 3. Everything else — not ordered by priority

Everything here is real and wanted, but waiting. The order below means
nothing.

- **Add a free local second opinion on already-approved claims.
  Measured 2026-08-26 — the question below is answered, and one model
  earned the job.** The idea was that small open models, trained only
  to answer "is this sentence supported by this document?", could
  double-check every approved verdict for free. All eight such models
  that can run on an ordinary computer were tested on 286 real and
  deliberately corrupted claims, twice each. The full write-up is not
  yet public. Seven of them wrongly reject between 79% and 91% of
  correct citations on the evidence a reader is shown. None of them can
  see a claim attached to the wrong paper. Most of them become more
  willing to approve a false claim when given more source text. The
  exception is IBM's Granite Guardian 3.3. Its count of approved
  corruptions did not move when given more text (7 of 47 both times).
  It was also the only model to improve on both kinds of mistake at
  once. What remains to build is narrow and cheap: ask Granite a second
  time about a claim the main judge already approved, and show any
  disagreement. It must never decide a verdict, because it still
  wrongly rejects 45% of correct citations. It also needs the large
  slice of source text rather than the short evidence the report shows.
- **Automatic spot-check on new kinds of text.** A run on an unfamiliar
  field shouldn't be trusted before a sample is checked. The wanted
  piece is one command. It picks a sample of claims from a finished
  run, has a strong model re-read them with the source, and writes a
  short "trust report".
- **Catch problems while other people use the tool, after
  publication.** Everything we test today runs on our own test texts,
  where the right answers are known. Once the tool is public, failures
  will happen on texts we never see and cannot score. People will run
  it on their own computers, maybe on a web page, maybe sharing results
  into a common database. From real use, only three kinds of
  information about failures can reach us, and this item is the plan
  for collecting them. Each is collected only if the user turns it on,
  and each carries as little of the user's text as possible. The first
  kind is the user's own corrections. The report screen already has
  buttons for "wrong source" and "the verdict is wrong". One more click
  should turn such a mark into a report the user can choose to send us.
  It would contain only the claim, the source passage and the two
  verdicts. That is a failure case confirmed by a real human, which is
  exactly what our own testing spends effort trying to construct. The
  second kind is the tool's own error counts. Each run already knows
  how many model requests failed, whether a source file came out
  garbled, and where a second reading disagreed with the verdict. A
  summary of those counts — numbers only, never the text — would show
  how the tool behaves on material our tests can't represent. The
  third kind is the trust report from the spot-check item above. In
  real use it doubles as a warning on kinds of text the tool was never
  tested on. Two small pieces are worth building early, because they
  help even before any release. One is a per-run error-count summary
  that a program can read. The other makes the "verdict wrong" click
  produce a ready-to-send report file. The rest waits for an actual
  decision to publish. One requirement is worth writing down now. If
  results are ever shared into a common database, a wrong verdict there
  misleads everyone who reuses it, not just one writer. So shared
  entries must carry how they were verified, and a way to be challenged
  and corrected later.
- **The remaining testing ideas.** Feed generated garbage input to the
  text-reading code and check that it fails safely. Feed deliberately
  broken PDFs to the file-import step. Measure how much the existing
  automatic tests actually protect.
- **One command instead of a folder of scripts.** A single command
  (`claimg verify`, `claimg download`, `claimg --help`) installed the
  standard Python way, with one consistent way to give it the access
  keys for model accounts. This is the wish outside testers have named
  most often.
- **Ordinary web pages as sources.** Today sources are mostly papers.
  Accept a plain web address, download and clean the page text, and
  check against it. This opens the tool to journalism, blog posts and
  policy writing.
- **Context around each proof sentence.** A quoted sentence can look
  like proof while the text right after it weakens it ("...however,
  this was a small pilot study"). Show the neighboring sentences, or
  reveal them on click. Separately, check whether the judging step
  needs more context too.
- **Import any published paper reliably.** The importer takes a
  paper's PDF or its permanent identifier and produces checkable text.
  It works fully on some papers but splits claims wrong on others.
  Work has started, with a measured starting score to compare against.
- **Understand the author's own uncited claims.** Tell apart three
  kinds of uncited sentence:
  - summaries of the author's cited material, checkable against the
    rest of the text,
  - genuinely original ideas,
  - overclaims that quietly go beyond what the citations support.
- **Follow a citation chain to the original.** Sometimes a text cites
  paper B, but paper B is itself only citing paper A for that fact.
  Find and check paper A. Today the tool marks "this looks like a
  secondhand claim" but doesn't follow the chain.
- **Suggest other relevant papers — including ones that disagree.** For
  any claim, offer additional supporting or contradicting papers,
  without touching the author's citations. Partially built for claims
  marked as wrongly cited.
- **Source-quality lookups.** After the survey above, the tool should
  flag retracted papers and questionable journals. Model-based
  paper-quality scoring comes only much later, if ever.
- **Run the full repair loop end to end once.** The loop runs the
  check, has a language model repair the flagged sentences, runs the
  check again, and stops after one repair round. The repair rules stop
  the model from simply weakening every sentence. Progress
  (2026-08-30): the tool now includes a ready-made instruction text
  for agent programs (`AGENT_LOOP_PROMPT.md`). An agent program, such
  as Claude Code, can both use a language model and run commands. The
  instruction text gives the agent every step: write or convert the
  text, save the files, collect sources, check, repair once, re-check,
  report. The write-save-check part has been run end to end and
  produced the README's worked example. What remains is testing the
  full loop, including the automatic repair step, with each research
  tool we can reach. That test is underway. A completed loop is also
  the first test of whether the repair step truly fixes the text,
  rather than only making the judge approve it.
- **Finish the local-model mode.** The tool can already run with a
  local model, one that runs on your own computer. That mode costs
  nothing and never sends your text anywhere. But no local model has
  passed the qualification tests, so the guide calls this mode a
  draft. The remaining work is to qualify one or two recommended
  models, plus three small known tasks:
  - making models answer in the exact format the tool expects,
  - giving small models smaller text pieces,
  - friendly model names.
- **The source claim-list feature, redesigned.** A removed feature
  broke each source into its own claim list, which enabled asking
  "what did you omit from this source?". It was removed from the
  command line because it cost most of the money and the verdicts
  never used it. Design notes for a second version exist.
- **Internal clean-up: one structured check instead of many small
  rules.** The judging quality currently comes from many small rules,
  each added after a real failure. A rewrite would replace several of
  them with one check-every-part step. That would break less often in
  the long term. It should wait for a period with no urgent work.
- **Speed and cost** — four known savings, none started:
  - measure how often the second checker (the "arbiter") runs, and
    whether better judge instructions would cut that rate — the rate
    is the main control over a paid run's price (added 2026-08-30),
  - classify all the uncited claims in one model call instead of one
    call each,
  - keep one Claude Code session open instead of starting one per
    call,
  - have the model company remember the source text between judge
    calls, instead of it being sent again with every call.
- **Internal refactors** (code clean-ups that change no behavior):
  - a shared viewer code module,
  - a tidier sequence for the checks that run after judging,
  - unifying the two file-fingerprint schemes (only together with a
    planned change to how saved results are stored).
- **Import ideas**, each small and unscheduled:
  - score the claim-splitting step itself on public test sets,
  - a backup way to read reference lists for papers no database has
    indexed (only if such papers actually show up),
  - detecting when two sources contain the same claim,
  - output in the "nanopublication" format, a standard way to share
    single claims together with their evidence,
  - a step that asks for clarification when a claim is ambiguous.

## 4. Paused on purpose (a decision is needed before anyone touches these)

- **Argument map and cruxes** — paused by the author in favor of core
  quality. A crux is the single point a conclusion most depends on.
  Prototypes exist but failed their audit for known, fixable reasons.
- **Figures vs text** — check whether a paper's picture shows what its
  text says. This is not started, and it waits until the precision
  work is done.
- **Docker image** (a ready-made package that runs the tool without
  any installation) — promised only if testers ask, and nobody has
  asked.
