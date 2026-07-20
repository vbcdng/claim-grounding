# What to build next — a list to discuss (2026-07-20)

This document collects, in one place and in plain words, everything we could
work on next: the promises made in the competition submission, the half-finished
internal queues, and the older roadmap and idea-list entries that are still
alive. The point is to pick an order together.

How to read it: items are grouped by theme, not by priority. Each item says
what it is, why it matters, and roughly how much work it is. "Small" means
hours, "medium" means a day or two, "large" means several days or more,
usually with careful testing. At the end there is a summary table and my
suggested top five — mark your own order there and we'll discuss.

One rule that applies to many items below: anything that can change the tool's
judgments has to pass the full re-check on all our hand-checked test papers
before it ships. That makes "medium" judgment items slower than they look.

(Note for readers of the public repository: some documents named below are
internal working notes and are not included in the public snapshot.)

---

## Theme A — Making the judgments better

### A1. When a claim is rejected but part of it is actually proven
**What:** Today, when the tool rejects a claim, the report just says
"unsupported" — even when half of the claim IS in the source. The author then
can't tell whether to delete the sentence or just fix the wrong half. The fix:
the report should say "this part is proven, this part is not."
**Why it matters:** In our audit of 236 hard claims, this was the tool's most
common mistake (58 cases). The submission text itself says this one change
should improve the tool more than anything else planned. It was promised
publicly.
**Where it stands:** A design already exists and was checked against claims a
human graded (docs/FINDING1_PARTIAL_FROM_ARBITER_DESIGN.md), including a guard
so that a claim doesn't get credit just because an unimportant side detail is
proven. A related known case: claims that bundle several statements into one
sentence sometimes get rejected because of one small part (the "t41" case from
the eggs text).
**Size:** Large (it changes judgments, so full re-testing is required).
**Implementation note:** today the "which part is missing" information is
fished out of the judge's free-text answer with pattern matching — fragile if
the model or its phrasing changes. Part of this work should be asking the
judge to return the missing parts as a structured list (an already-written
deferred fix in IDEAS.md).

### A2. Show the RIGHT proof sentence on the card
**What:** Some cards show a sentence that is merely about the same topic
instead of the sentence that actually proves the claim — even though the
proving sentence was already found and is sitting in the run's data. The fix
just picks the better sentence for display.
**Why it matters:** A reader who sees a weak sentence stops trusting a correct
verdict. Seen on seven cards across all three test papers.
**Where it stands:** Design done, premise verified against the data
(docs/FINDING3_EVIDENCE_DISPLAY_FIX_DESIGN.md). Costs nothing to run — it's
display only, no new model calls, verdicts can't move.
**Size:** Small-medium. Probably the best effort-to-payoff ratio in this list.

### A3. "Source file missing" should not be counted as "unsupported"
**What:** When a source file simply isn't there, the claim currently lands in
the same red "unsupported" pile as claims that were really checked and failed.
It should be its own grey category with its own filter, and not inflate the
unsupported count.
**Why it matters:** 4 claims on one test paper make the tool look stricter
than it is. Honest reporting is the product.
**Size:** Small-medium.

### A4. Check numbers and cause-vs-effect direction separately
**What:** Two failure types our mutation test found: if someone changes a
number inside a claim, or flips "A causes B" into "B causes A", the tool can
still say "supported". A cheap, dedicated check — compare the numbers in the
claim against the numbers in the proof, and check the direction of the causal
wording.
**Why it matters:** These are exactly the errors a hurried human also misses —
the most embarrassing kind of false "supported". You yourself found a
published paper citing a source that argues the opposite causal direction.
**Size:** Medium-large (judgment-adjacent, needs full re-testing).

### A5. Fewer missing proof sentences on supported claims
**What:** The tool's most common small error: the verdict "supported" is
right, but the card doesn't show all the sentences that prove it. That's fine
for a machine reading the data, but a human reviewer has to open the source.
**Why it matters:** Promised in the submission ("the most typical error").
Also blocks the automatic repair loop from working well.
**Size:** Medium, open-ended — this is gradual improvement, not one fix.

### A6. Same text, same answer — verdict stability
**What:** Running the exact same text twice can flip a handful of borderline
verdicts (a no-change re-run once flipped ~6 of 44), because the cheap judge
answers borderline cases a bit randomly. Inside one judging step the tool
already votes several times and takes the majority; the fix is to do the same
for the final verdict of borderline claims. The test-paper gate currently
works around this by only scoring stable claims — a fix would let it score
everything.
**Why it matters:** "Precision" includes giving the same answer to the same
question. A tool that flips verdicts between runs is hard to trust and hard
to measure.
**Size:** Medium (changes judgments → full re-testing), plus a small cost
increase for the extra votes on borderline claims only.

---

## Theme B — Knowing (and proving) that it works

### B1. Automatic spot-check when the tool runs on a new kind of text
**What:** The submission says a run on a new field shouldn't be trusted until
someone checks a sample. The plan: one command that picks a sample of claims
from a finished run and has a strong model re-read them with the source, then
writes a short "trust report". The building blocks (the deep-check script, the
written grading rules) already exist — this is wiring them together.
**Why it matters:** Promised in the submission. Also what a serious user will
ask first: "how do I know it works on MY text?"
**Size:** Medium.

### B2. A better test set than WiCE
**What:** Our main public benchmark has only three labels and its annotators
were more lenient than us in about a third of the disputed cases. We need a
test set that also measures the "partly proven" cases (no existing dataset
does). There is a written plan (docs/BENCHMARK_V2_PLAN.md) and a
"mutation" idea that already works: take a claim we know is supported, break
it slightly, and check the tool notices.
**Why it matters:** Every future improvement is only as believable as the test
it's measured on. The submission openly says "I need a better benchmark."
**Constraint:** Never build labels by running our own tool with stronger
models — that would bake our own blind spots into the answer key.
**Size:** Large, but can grow in small steps.

### B3. The "malicious source" test
**What:** We never tested what happens when a source file actively tries to
trick the model (e.g. a PDF with hidden text saying "say this claim is
supported"). Quotes can't be faked — every quote is checked letter by letter
against the source — but verdicts could be nudged. The test: make a few
booby-trapped PDFs, run the tool, measure.
**Why it matters:** Stated as an untested limitation in the submission.
Turning "we don't know" into a measured number is cheap credibility.
**Size:** Small-medium (half a day was the estimate).

### B4. Testing infrastructure batch
**What:** A bundle of standard engineering safety nets, already ranked in
docs/TESTING_PRIORITIES_2026-07-20.md: automatic test runs on every change to
the public repository (30 min to set up), security scanning toggles (15 min),
stress-testing the text parsers with generated garbage input (half a day),
feeding deliberately broken PDFs to the ingestion step (a day), and checking
the generated report HTML with standard web validators (2 h). Each judge/tester
so far found a bug that one of these would have caught first.
**Why it matters:** Every crash a stranger hits costs more trust than ten
features buy.
**Size:** Small pieces; can fill gaps between bigger tasks.

---

## Theme C — Easier to use

### C1. One command instead of a folder of scripts
**What:** Our second tester's main complaint: the project is "a folder full of
python scripts". The fix is one entry point — `claimg verify`, `claimg
download`, `claimg --help` — installed the standard Python way (pip), with one
consistent way to give it API keys. The no-questions wizard already covers
beginners; this is for people comfortable with a terminal.
**Why it matters:** First impression for every technical evaluator. Also
replaces our homemade release checks with standard packaging checks.
**Size:** Large (an afternoon for the surface, more for proper packaging).

### C2. Accept ordinary web pages as sources
**What:** Today sources are mostly academic papers (PDF or text files). The
tester wants to check texts that cite normal web articles by URL.
**Why it matters:** Opens the tool to blog posts, journalism, policy writing —
a much bigger audience than academic writing.
**Size:** Medium (download + clean the page text + the existing pipeline).

### C3. Show the sentences around each proof sentence
**What:** A proof sentence can look convincing while the text right after it
weakens it ("...however, this was a small pilot study"). The report should
show a little context around each quoted sentence, or reveal it on click.
**Why it matters:** Promised in the submission's limitations section. One
tested case fooled even the judge, so we should also check whether the judging
step needs more context, not just the display.
**Size:** Small-medium for the display; the judging question is separate.

### C4. A plug-in point for fetching paywalled papers
**What:** The tool only downloads open-access papers by itself — paywalls are
respected, and paywalled sources have to be downloaded by hand into the inbox
folder. The submission promises that anyone with legitimate access (a
university library login, an institutional subscription) "can easily connect"
their own download script. Today that connection point doesn't formally exist.
The work: after the automatic download step, for every paper that is still
missing, the tool calls a user-supplied script — handing it the paper's DOI,
title, and link — and whatever the script saves lands in the existing inbox
and is filed automatically by the existing ingest step. Plus a documented
example script, so someone can see exactly what to write.
**Why it matters:** Missing paywalled sources are one of the biggest practical
limitations (a claim whose source can't be read can't be checked). For a
university user this turns an hour of clicking into nothing. And we don't
have to build or maintain any downloader ourselves — the user brings their own
access; the tool just needs a clean socket to plug it into.
**Size:** Small-medium. The hard parts (inbox, matching files to the right
paper, updating the reports) already exist and stay unchanged.

---

## Theme D — New abilities (the vision list from the submission)

### D1. Import any published paper reliably
**What:** The importer takes a PDF or DOI and turns a published paper into
checkable input. It works fully on some papers but splits claims wrong on
others. The submission says: "It needs more work, but probably not much."
**Where it stands:** Already in motion — a measured baseline exists and found
2 wrong citation mappings; waiting on your written opinion file for round 2.
**Size:** Medium, iterative.

### D2. Understand the author's own uncited claims better
**What:** Today uncited claims are just shown as "own claims", unchecked. The
promise: tell apart (a) summaries of the author's cited material — these could
be checked against the rest of the text, (b) genuinely original ideas, and (c)
overclaims that quietly go beyond what the citations support. An older idea in
the file — checking a paper's conclusions against its own body — is the same
family.
**Size:** Large.

### D3. Follow a citation to the original source
**What:** When a text cites paper B, but paper B is itself only citing paper A
for that fact, find and check paper A. Today the tool marks "this looks like a
secondhand claim" but doesn't follow the chain.
**Size:** Large.

### D4. Suggest other relevant papers — including ones that disagree
**What:** For any claim, offer additional papers that support or contradict
it, without touching the author's citations. Half-built: the "find a better
source" script exists for claims marked as wrongly cited, and an unmerged
branch can search for papers that could settle a disputed point. A related
parked idea: when the tool finds counter-evidence in a source, surface it as a
real contradiction instead of just a flag.
**Size:** Medium to large depending on how far we go.

### D5. Check the quality of the sources themselves
**What:** Is the cited paper retracted? Is the journal questionable? First
step is plain lookups in free databases (we haven't yet surveyed what is
freely available — that survey is the first concrete task, an afternoon).
Model-based paper-quality scoring would come much later, if ever.
**Size:** Small for the survey; medium for the lookup feature.

### D6. Check figures and images against the text
**What:** Does the picture actually show what the text says it shows?
Explicitly "not started" in the submission. **Suggestion:** park it.

### D7. Try Elicit as a text producer
**What:** Elicit (an AI research-report tool) opened its API on July 15. One
experiment: have it produce a cited report, run our tool on it, write a short
note. Mind the cost — their API needs a $49/month subscription.
**Size:** Small (one afternoon + the subscription decision).

### D8. Run the full repair loop once, end to end
**What:** The submission's vision: report → an LLM fixes the text (with a
prompt that stops it from just watering everything down) → re-check → repeat
until clean. All the pieces exist; nobody has run the whole circle on a real
text and written down what happened.
**Why it matters:** It's the story the whole tool is building toward, and a
great demo if it works. Re-runs are cheap because unchanged claims aren't
re-checked.
**Size:** Medium (mostly running and documenting, some prompt work).

---

## Theme E — Cheaper, faster, tidier (mostly invisible to users)

### E1. LLM model analysis — find the best model for each step
**What:** The tool doesn't use one model; it uses a different one per job —
the main judge (currently Gemini 2.5 Flash-Lite), the second re-check of
flagged claims (DeepSeek), source handling (DeepSeek), and the optional
second opinion. The submission openly says we've only tested the cheapest
models and that "there is still a lot of low-hanging fruit." The work: refresh
the price-and-model survey (docs/MODEL_OPTIONS.md is from June — prices and
model lineups drift fast), pick 2–3 promising candidates per job, and run each
through the written qualification procedure (docs/MODEL_SWAP_PROTOCOL.md — a
step-by-step battery where cheap tests fail first and nothing ships without
passing the hand-checked papers). Keep whichever passes at the best price.
**Why it matters:** Promised in the submission. The procedure has already paid
off once: the current main judge came out of it, about 6× cheaper than the
previous one and with zero false "supported" on the audit paper. Newer cheap
models keep appearing; each sweep is a chance for the same kind of win —
better verdicts, lower cost, or both.
**Also worth testing:** does a somewhat stronger (still cheap) judge reduce
the tool's known weak spots — compound claims, missing proof sentences —
enough to justify its price? That question connects directly to items A1 and
A5.
**Size:** Medium; mechanical once the survey is fresh, so most of it can be
delegated to cheap helper sessions. Each candidate costs pennies to a few
dollars to qualify.

### E2. Small local models — the free, on-your-computer parts
**What:** Two separate pieces, both promised in the submission ("I also did
not test all possible local small models... or their combinations"):
1. **The sentence-matching model.** Before any LLM is called, a small free
   model running on your own computer (SPECTER) picks the candidate sentences
   from the source. It was chosen once and never compared against newer
   alternatives. If a better matcher finds the right sentence more often,
   every later step gets better for free — several past false alarms trace to
   the matcher missing a proof sentence that was phrased very differently
   from the claim. Testing is cheap: re-run the stored test sets with a
   different matcher and count how often the right sentence lands in the
   candidates. Connects to E3 (adding a keyword-based search alongside it).
2. **Running the whole tool with a local LLM.** For people who can't send
   texts to an API (privacy, cost, no key), the tool can already run against
   a local model through Ollama — a 4-step guide exists (LOCAL_MODELS.md).
   But no local model has been put through the qualification tests, so today
   the guide honestly says "treat it as a draft." Finishing the job means
   qualifying one or two recommended local models, plus three small known
   tasks written down earlier: a setting that makes small models answer in
   the exact format the tool expects, smaller text chunks so small models
   don't overflow, and friendly model names in the wizard.
**Why it matters:** A fully free, fully private mode is a real differentiator,
and the matcher comparison is one of the cheapest possible accuracy wins.
**Size:** Medium for each piece. The matcher comparison costs nothing to run;
the local-LLM part needs patience (local runs are slow) more than money.

### E3. Better first-pass sentence search
**What:** The step that finds candidate sentences sometimes misses proof that
is worded very differently from the claim; several past false alarms trace to
it. There's a written plan to add a classic keyword-based search alongside the
meaning-based one and re-measure.
**Size:** Medium (affects judgments → full re-test).

### E4. Redesign of source decomposition ("what does this source claim?")
**What:** The feature that broke each source into its own list of claims was
removed from the command line on your ruling — it cost most of the money and
the verdicts never used it. Design seeds for a v2 are written down. It would
enable the "what did you omit from this source?" panel again.
**Size:** Large. **Suggestion:** only after the assessment items above.

### E5. Internal clean-up: one structured check instead of many small rules
**What:** The judging quality currently comes from many small rules, each
added after a real failure. A refactor idea would replace several of them with
one structured "check every part of the claim, typed by kind" step. Less
fragile long-term, but risky to rush.
**Size:** Large; needs a quiet period, not deadline pressure.

### E6. How many sentences does one citation cover?
**What:** A citation at the end of a paragraph may be meant to back several
sentences, not just the one it touches. There's a written idea for detecting
this "citation span". Wrong span = wrong claims from the start.
**Size:** Medium-large.

---

## Summary table

Effort: S = hours, M = 1–2 days, L = several days+. "Gate" = changes
judgments, must pass the full re-check on all hand-audited papers.

| # | Item | Effort | Gate | My suggested tier | Your call |
|---|------|--------|------|-------------------|-----------|
| A2 | Show the right proof sentence | S-M | no (display) | **1** | |
| A1 | Partly-proven rejected claims | L | yes | **1** | |
| A3 | "Source missing" own category | S-M | mostly no | **1** | |
| B3 | Malicious-source test | S-M | no | **1** | |
| D8 | Full repair loop once, end to end | M | no | **1** | |
| C3 | Context around proof sentences | S-M | display part no | 2 | |
| B1 | Auto spot-check on new domains | M | no | 2 | |
| A4 | Numbers + cause-direction check | M-L | yes | 2 | |
| A6 | Verdict stability (same text, same answer) | M | yes | 2 | |
| C1 | One `claimg` command + pip | L | no | 2 | |
| E1 | LLM model analysis (best model per step) | M | yes | 2 | |
| B4 | Testing infrastructure batch | S each | no | 2 (fill-in work) | |
| D1 | Importer robustness (in motion) | M | partly | 2 (continues anyway) | |
| C4 | Plug-in point for paywalled-paper fetchers | S-M | no | 2 | |
| B2 | Better benchmark | L | n/a | 3 | |
| A5 | Fewer missing proof sentences | M+ | yes | 3 | |
| C2 | Web pages as sources | M | partly | 3 | |
| D4 | Suggest papers incl. contradicting | M-L | no | 3 | |
| D5 | Source-quality lookups (survey first) | S then M | no | 3 | |
| E2 | Small local models (matcher compare + offline mode) | M each | yes | 3 | |
| E3 | Better sentence search | M | yes | 3 | |
| D7 | Elicit experiment | S | no | 3 | |
| D2 | Uncited-claim understanding | L | yes | 4 | |
| D3 | Follow citations to the original | L | yes | 4 | |
| E6 | Citation span detection | M-L | yes | 4 | |
| E4 | Source decomposition v2 | L | yes | 4 | |
| E5 | Typed-check refactor | L | yes | 4 | |
| D6 | Figures vs text | L | yes | park | |

## My suggested top five, in order

1. **A2 — right proof sentence.** Design done, no cost, verdicts can't move,
   and it fixes the thing a reader sees first.
2. **A1 — partly-proven rejected claims.** The submission's own "biggest
   planned improvement"; the design and its test cases are ready.
3. **A3 — source-missing category.** Small, honest, makes the numbers fairer.
4. **B3 — malicious-source test.** Cheap, and turns a stated unknown into a
   measured number.
5. **D8 — one full repair loop.** All pieces exist; the write-up doubles as
   the tool's best demo story.

## If the goal is precision — the ordered plan

"Precision" here means: how often the tool's verdicts are simply right, in
both directions — not calling a bad claim "supported" (the dangerous error)
and not rejecting a good claim (the annoying one that makes people ignore the
tool). If that is the goal for the next stretch, this is the order I would
work in, with the reason for each position:

1. **A1 — partly-proven rejected claims.** The single biggest measured error
   class: 58 of the 236 audited hard claims — roughly a quarter of all the
   mistakes we know about — were "unsupported" verdicts on claims that were
   partly proven. No other item comes close in measured impact, and the
   design plus its accept/reject test cases are already written. Start here.

2. **A4 — numbers and cause-vs-effect direction.** Smaller in count but it
   targets the dangerous direction: our own break-it test showed the tool can
   still say "supported" after a number in the claim was changed or the
   causal direction was flipped. These are exactly the false "supported"
   cases the whole submission brags about avoiding, so closing them protects
   the tool's strongest selling point.

3. **A6 — verdict stability.** Same text in, same verdicts out. Majority
   voting on the final verdict of borderline claims, the way single judging
   steps already vote internally. Without it, measuring the gains from steps
   1–2 is muddied by random flips — which is also why it sits this early.

4. **E3 + the matcher comparison from E2.** Better first-pass sentence
   finding. A verdict can only be as good as the sentences the judge is
   shown; several known false alarms happened because the proof existed but
   was worded too differently for the matcher to find it. Two cheap,
   measurable experiments: add a keyword-based search next to the
   meaning-based one, and compare the current matching model against newer
   alternatives on the stored test sets. Both can be scored offline, without
   paying for model calls, before anything touches the real pipeline.

5. **E1 — the judge-model sweep.** After the targeted fixes, test whether a
   newer or slightly stronger (still cheap) judge lifts everything at once —
   especially the compound-claim weakness that rule-writing struggles with.
   Doing it after 1–4 matters: otherwise a better model would hide how much
   the fixes themselves helped.

6. **B2 — the better benchmark, started in the background on day one.**
   Not a fix itself, but every claim of "precision went up" is only as
   believable as the measuring stick. The cheap first step is extending the
   break-it test (more mutation types, more claims); the bigger step is a
   test set that scores the "partly proven" verdicts that item A1 introduces
   — without it, A1's improvement literally cannot be measured.

7. **E6 — citation span.** If the tool attaches a citation to the wrong
   number of sentences, the claims themselves are wrong before any judging
   starts, and no downstream fix can recover that. Root-cause work; slower
   and riskier, which is why it comes after the measured error classes.

8. **A5 — completeness of the shown proof sentences.** The most common small
   error. It doesn't flip verdicts, but for a human reader a supported claim
   with half its proof missing *feels* imprecise — and the repair loop needs
   it.

Alongside, not instead: **A2** (show the right proof sentence) and **A3**
("source missing" as its own category) don't change any verdict, but they fix
the two things a reader most often *experiences* as wrong verdicts. Both are
small; A2 is worth doing first of everything simply because it's nearly free.

Sequencing note: steps 1, 2, 3, 7 and the sweep in 5 all change judgments, so
each must pass the full re-check on the hand-audited papers separately — they
are best done one after another, not merged. Steps 4 and 6 are offline
measurement work and can run in parallel or be delegated.

Open questions for you:
- Does competition follow-up (reviewer questions, continuation funding
  material) outrank all of this for the next weeks, or run alongside?
- C1 (one command) is the loudest tester wish but the least "research" item —
  how much do you weigh outside usability vs verdict quality right now?
- B2 (better benchmark) is slow and unglamorous but everything else is
  measured against it — start it in the background now, or after tier 1?
- Any of the tier-4 items you'd promote because they matter to how you
  personally want to use the tool?
