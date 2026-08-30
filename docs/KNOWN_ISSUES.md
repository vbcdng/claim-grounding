# Known issues

This page lists the problems in this repository that we know about and
have not fixed yet. Each entry says what happens, why, and what to do
about it where a workaround exists. Problems that get fixed move to the
"Fixed" section at the end, with the date of the fix.

Last updated: 2026-08-30. Most entries come from a systematic self-check
of the whole repository on 2026-07-20. That check also re-computed every
published benchmark number from the data included in the repository, and
every number reproduced. Nothing on this list changes a published result.

## Words used in this document

- **Source** — a file, usually a PDF or a text file, that a citation
  points at. The tool checks claims against these files.
- **Claim** — one cited statement in your text, ending at a citation
  marker.
- **Citation marker** — the `[[key]]` tag in your text that names which
  source a claim cites.
- **Verdict** — the tool's judgment on one claim. The main verdicts
  are supported, unsupported, and "own" (a sentence with no citation,
  which is shown but not checked).
- **Viewer** — the HTML report the tool writes. It shows your text next
  to one card per claim.

## Problems you might hit when using the tool

**1. A citation marker with a typo is dropped without any warning.**
The tool only recognizes markers written exactly as `[[key]]`, with no
spaces inside. A marker like `[[my key]]` (a space inside) or `[[key]`
(a missing bracket) is not recognized, and the tool does not tell you.
The sentence is then treated as your own uncited words and gets no
check at all. The interactive setup helper does not catch these either,
because its marker check accepts spaces that the real reader rejects.

Workaround: after a run, open the viewer and confirm that every
sentence you cited shows a verdict. A cited sentence shown as an "own"
claim means its marker was not recognized.

**2. A source file the tool cannot read looks like an ordinary
rejection.** A scanned PDF contains pictures of pages instead of
machine-readable text, so the tool finds no sentences in it. Claims
citing such a file are marked unsupported with the technical reason "no
source sentences", styled like any other rejection. There is no clear
"this source could not be read" state. When a claim cites several
sources and one of them is unreadable, that source adds nothing to the
check. The claim's card does not say so.

Workaround: run
`venv/bin/python download_sources.py --report-only` for your project.
The report it writes flags every source file with little or no readable
text.

**3. The first run downloads one model file, and blocking that download
gives a raw error.** The tool compares sentences with a comparison
model that runs on your own computer. That model is about 440
megabytes and is downloaded once, the first time you run the tool. If
that download cannot happen, the run stops with a raw technical error
instead of a plain explanation.

This happens when the machine is
offline. It also happens when the command was started with the two
`..._OFFLINE=1` settings that some older instructions showed. Those
settings tell the tool to never use the network.

Workaround: let the very first run reach the internet once. Every later
run works fully offline.

**4. On Windows, the `--open` flag builds a wrong address.** The flag
should open the finished report in your browser, but the address it
builds is not in the form Windows browsers accept. The report itself is
fine. Workaround: the file path is always printed at the end of the
run. Open that file by hand.

**5. A missing `claude` program ends in a long technical error trace.**
The `--backend claude-code` option runs the checks through the Claude
Code command-line program. If that program is not installed, the run
stops with a full Python error trace. The last line of the trace does
explain the problem in plain words. The interactive setup helper checks
for the program properly and will not offer the option when it is
missing.

**6. Failures inside the optional argument-structure pass can look like
a clean result.** The `--argument-map` flag adds an optional extra pass
that maps how your claims support each other. The pass has three parts.
If one part fails, its panel section shows the same text as a genuine
empty finding. An example is "no cruxes identified" (a crux is a claim
that much of the argument depends on). It does not say that the part
failed.

If all three parts fail, the whole panel is missing without comment.
Warnings appear only in the terminal output. Verdicts are never
affected by this pass.

## Notes for people re-checking the published benchmark numbers

**7. Two supporting documents are named without their folder.** A
note in `benchmarks/wice_anchor/README.md` cites
`NIGHT_LOG_2026-07-12_accB.md` and `FIRST_CHECK_RUN.md` by bare
filename. Both files are in the repository, at
`docs/archive/NIGHT_LOG_2026-07-12_accB.md` and
`docs/FIRST_CHECK_RUN.md`. The note just does not say where they are.

**8. The "58 rows" figure in `FOR_REVIEWERS.md` counts full and partial
support together.** The 58 counts rows where the final label found real
or partial support for a claim the tool had called unsupported. A
script that counts only full "supported" labels gets 10. It is the same
data under a stricter counting rule. The sentence in the document has
not been reworded yet.

**9. The benchmark scorer prints an alarming banner that reports old
news.** On 6 of the test batches that were kept aside and never used
during tuning, the scorer prints "FALSE-SUPPORT FAILURE". The banner
is correct, not a malfunction. Those rows are exactly the false-support
cases
the submission itself discloses: 3 in the base set, 6 after the
disputed rows were re-judged. It is not a new failure you discovered.

## Limits of the verdicts themselves

The list above covers defects in how the software behaves. A separate
question is in which situations the judgment itself can be wrong. That
is tracked as open improvement work in `ROADMAP.md`, with the current
state of each item.

## Git history note (only if you cloned before 2026-07-20)

On 2026-07-20 we rewrote the repository's history once. The rewrite
removed the extracted text of two sources that are only legally
available behind a publisher's paywall. Rewriting history means old
copies of the repository no longer match the current one. If you
cloned before that date, or `git pull` stops with an error about
diverged histories, download a fresh copy — or run
`git fetch && git reset --hard origin/master`. The history will not be
rewritten again. Since that day the repository only changes by ordinary
commits.

## Fixed

**Fixed 2026-08-19 — a failed model request inside the extra checks
went unreported, so the run looked complete.** The main judging step
already reported its failures (fixed 2026-07-20, below). This work
extended the same honesty to the extra checks. It fixed two entries
from the July version of this page:

- The optional second-opinion pass is a second model that re-reads
  every verdict. When its own request failed, the failure used to be
  scored as "the second model agrees", with nothing shown on the card.
  Now a failed request writes no opinion at all, the claim is listed in
  the run's closing warning, and a plain re-run retries it. (Numbered 5
  in the July version of this page.)
- A crash inside the evidence-coverage check used to mark the claim as
  already checked, so later runs never retried it. Now a crashed check
  leaves the claim unmarked, and a plain re-run redoes it. (Numbered 8
  in the July version of this page.)

The same work also made failures visible in the report. The viewer now
shows a warning banner at the top and a "check not run — API failed"
mark on every affected card. The API is the internet connection through
which the tool asks the model its questions. The benchmark scorers
refuse to score a run that contains failed requests.

**Fixed 2026-07-20, the day of the original self-check:**

- A failed model request in the main judging step used to store
  "unsupported" verdicts that later runs then kept forever. Since this
  fix such claims are flagged, listed in a run-end warning, and retried
  by a plain re-run.
- The README of that day was updated to document the then-default extra
  checking model and its key. (The default models changed again on
  2026-08-30 — the current README is the up-to-date reference.)
- A dead reference at the top of `docs/PRINTING_SIX_JUDGE_TABLE.md` was
  removed.
