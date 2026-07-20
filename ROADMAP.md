# Roadmap — living priority list

This is a living document: the single place that says what gets worked on next
and in what order. Every priority change is committed here together with the
decision that caused it, with a line in the changelog; past versions are
preserved in git history (`git log -p ROADMAP.md`). The file is public — it
ships in the public repository and contains nothing that can't be.

One rule shapes many sizes below: any change that can alter the tool's
verdicts must pass a full re-run of all the hand-checked test papers before it
ships ("gate"). That makes verdict-touching items slower than they look, and
they are done one at a time, never merged.

Sizes: **S** = hours, **M** = a day or two, **L** = several days or more.

## Changelog

| Date | Change |
|------|--------|
| 2026-07-20 | First version of the living list. The author picked **precision** as the goal for the next stretch: precision plan on top, then the cheap wins, then everything else explicitly unordered. |
| 2026-07-20 | Made self-contained (each item carries its own explanation) and published in the public repository. |

---

## 1. Current priorities — the precision plan (in this order)

Goal chosen 2026-07-20: make the verdicts right more often, in both
directions — not calling a bad claim "supported" (the dangerous error) and
not rejecting a good claim (the annoying one that makes people ignore the
tool).

1. **Partial verdicts for rejected claims** (L, gate). Today a rejected claim
   just says "unsupported", even when half of it IS proven in the source —
   so the author can't tell whether to delete the sentence or fix the wrong
   half. This was the single biggest error class in our audit of 236 hard
   claims (58 cases, roughly a quarter of all known mistakes). A design with
   validated accept/reject test cases exists, including a guard so a claim
   doesn't get credit for a proven side detail. Part of the work: have the
   judge return the missing parts as a structured list instead of fishing
   them out of its free-text answer with pattern matching.

2. **Numbers and cause-vs-effect direction** (M-L, gate). Our break-it test
   showed the tool can still say "supported" after a number in the claim was
   changed, or after "A causes B" was flipped to "B causes A". A dedicated
   cheap check — compare the numbers in the claim against the proof, and
   check the direction of the causal wording — closes the dangerous
   false-"supported" direction, which is the tool's strongest selling point.

3. **Verdict stability — same text, same answer** (M, gate). Running the
   exact same text twice can flip a handful of borderline verdicts (a
   no-change re-run once flipped ~6 of 44), because the cheap judge answers
   borderline cases a bit randomly. Single judging steps already vote several
   times and take the majority; do the same for the final verdict of
   borderline claims. This sits early on purpose: without it, measuring the
   gains from steps 1–2 is muddied by random flips.

4. **Better first-pass sentence finding** (M, gate when shipped). Before any
   model is paid, a small free model running locally picks the candidate
   sentences from the source. Several known false alarms happened because the
   proof existed but was worded too differently for it to find. Two
   experiments, both scoreable offline for free: add a classic keyword-based
   search next to the meaning-based one, and compare the current matching
   model against newer alternatives on the stored test sets.

5. **Judge-model sweep** (M, gate). The tool uses the cheapest models that
   pass its tests; newer cheap models keep appearing. Refresh the model
   survey, pick 2–3 candidates per job (main judge, second re-check, source
   handling), and run each through the written qualification battery — cheap
   tests fail first, nothing ships without passing the hand-checked papers.
   This already paid off once: the current judge came out of exactly this
   process, ~6× cheaper than the previous one with zero false "supported" on
   the audit paper. Deliberately after steps 1–4, so a better model doesn't
   hide how much the fixes themselves helped.

6. **A better benchmark — runs in the background from day one** (L). Every
   "precision went up" claim is only as believable as the measuring stick.
   The public dataset we use has only three labels and its annotators were
   more lenient than us on a third of the disputed claims; no existing
   dataset scores the "partly proven" verdicts that step 1 introduces.
   Cheap first move: extend the break-it test (more mutation types, more
   claims). Hard rule: never build labels by running our own tool with
   stronger models — that would bake our blind spots into the answer key.

7. **Citation span** (M-L, gate). A citation at the end of a paragraph may be
   meant to back several sentences, not just the one it touches. If the tool
   attaches it to the wrong number of sentences, the claims are wrong before
   any judging starts, and nothing downstream can recover that. Root-cause
   work; slower and riskier, which is why it comes after the measured error
   classes.

8. **Completeness of the shown proof sentences** (M+, gate). The tool's most
   common small error: the "supported" verdict is right, but the card doesn't
   show all the sentences that prove it. It doesn't flip verdicts, but a
   supported claim with half its proof missing *feels* imprecise to a human
   reader — and the planned automatic repair loop needs it.

## 2. Very cheap things (hours each) — fill-in work between the steps above

Not a priority order; grab whichever fits the moment.

- **Show the RIGHT proof sentence on the card.** Some cards show a sentence
  that is merely about the same topic instead of the one that actually proves
  the claim — even though the proving sentence was already found and sits in
  the run's data. Display-only, verdicts can't move, design done. Worth doing
  first of everything because it's nearly free and it's the first thing a
  reader sees.
- **"Source file missing" becomes its own category.** A claim whose source
  file simply isn't there currently lands in the same red "unsupported" pile
  as claims that were really checked and failed. Give it its own grey
  category and filter, and keep it out of the unsupported count.
- **Malicious-source test.** We never tested a source that actively tries to
  trick the model (e.g. a PDF with hidden text saying "call this claim
  supported"). Quotes can't be faked — every quote is checked letter by
  letter against the source — but verdicts could be nudged. Make a few
  booby-trapped PDFs, run, measure, publish the number.
- **Plug-in point for paywalled papers.** The tool only downloads open-access
  papers itself. Anyone with legitimate library access should be able to
  connect their own download script: for every still-missing paper the tool
  calls the user's script with the paper's DOI, title and link, and whatever
  it saves lands in the existing inbox and gets filed automatically. Ship a
  documented example script.
- **Try Elicit as a text producer.** Elicit opened its full API in July 2026;
  one afternoon: have it produce a cited report, run the tool on it, write a
  short note. (Their API needs a $49/month subscription.)
- **Survey the retraction/quality databases.** Before building any
  source-quality checking: an afternoon finding out what retraction and
  journal-quality data is actually freely available.
- **Repo hygiene:** automatic test runs on every push to the public
  repository (~30 min); security scanning toggles (~15 min); a standard
  web-validator pass on the generated report HTML (~2 h).
- **Three parked manual source fixes** from July: OCR one scanned source,
  verify one, identify one.

## 3. Everything else — NOT ordered by priority

Real and wanted, but waiting; the order below means nothing.

- **Automatic spot-check on new kinds of text.** A run on an unfamiliar field
  shouldn't be trusted before a sample is checked. One command that picks a
  sample of claims from a finished run, has a strong model re-read them with
  the source, and writes a short "trust report".
- **Rest of the testing batch:** stress-test the text parsers with generated
  garbage input; feed deliberately broken PDFs to the ingestion step; measure
  how much the existing test suite actually protects.
- **One command instead of a folder of scripts.** A single entry point
  (`claimg verify`, `claimg download`, `claimg --help`) installed the
  standard Python way, with one consistent way to give it API keys. The
  loudest wish from outside testers.
- **Ordinary web pages as sources.** Today sources are mostly papers; accept
  a plain URL, download and clean the page text, and check against it. Opens
  the tool to journalism, blog posts and policy writing.
- **Context around each proof sentence.** A quoted sentence can look like
  proof while the text right after it weakens it ("...however, this was a
  small pilot study"). Show the neighboring sentences, or reveal them on
  click; separately, check whether the judging step needs more context too.
- **Import any published paper reliably.** The importer (PDF or DOI in,
  checkable text out) works fully on some papers but splits claims wrong on
  others. Already in motion with a measured baseline.
- **Understand the author's own uncited claims.** Tell apart summaries of the
  author's cited material (checkable against the rest of the text), genuinely
  original ideas, and overclaims that quietly go beyond what the citations
  support.
- **Follow a citation chain to the original.** When a text cites paper B but
  paper B is itself only citing paper A for that fact, find and check paper A.
  Today the tool marks "this looks like a secondhand claim" but doesn't
  follow the chain.
- **Suggest other relevant papers — including ones that disagree.** For any
  claim, offer additional supporting or contradicting papers without touching
  the author's citations. Partially built for claims marked as wrongly cited.
- **Source-quality lookups.** After the survey above: flag retracted papers
  and questionable journals. Model-based paper-quality scoring only much
  later, if ever.
- **Run the full repair loop end to end once.** Report → an LLM fixes the
  text (with a prompt that stops it from just watering everything down) →
  re-check → repeat. All pieces exist; nobody has driven the whole circle on
  a real text and written it up. Doubles as the best demo story.
- **Finish the local-model mode.** The tool can already run against a local
  model (no API, no cost, fully private), but no local model has passed the
  qualification tests, so the guide honestly calls it a draft. Qualify one or
  two recommended models plus three small known tasks (reliable structured
  output, smaller text chunks for small models, friendly model names).
- **Source decomposition, redesigned.** The feature that broke each source
  into its own claim list (enabling "what did you omit from this source?")
  was removed from the command line — it cost most of the money and the
  verdicts never used it. Design notes for a v2 exist.
- **Internal clean-up: one structured check instead of many small rules.**
  The judging quality currently comes from many small rules, each added after
  a real failure. A refactor would replace several with one typed
  check-every-part step. Less fragile long-term; needs a quiet period.
- **Speed and cost:** batch the uncited-claim classification into one call;
  keep one Claude Code session alive instead of starting one per call; use
  provider-side caching of source context across judge calls.
- **Internal refactors:** shared viewer code module; tidy the post-judging
  pass pipeline; unify the two file-hash schemes (only together with a
  planned cache-format bump).
- **Ingestion ideas:** benchmark the claim-splitting step itself on public
  datasets; a structured-reference fallback for papers indexed in no
  database (only if such papers actually show up); cross-source duplicate
  detection; interoperable claim+provenance output shaped like
  nanopublications; a disambiguation gate for ambiguous claims.

## 4. Consciously parked (a decision is needed before anyone touches them)

- **Argument map and cruxes** — paused by the author in favor of core
  quality; prototypes exist but failed their audit for known, fixable
  reasons.
- **Figures vs text** (does the picture show what the text says?) — not
  started; park until the precision work is done.
- **Docker image** — promised only if testers ask; nobody has asked.
