# Known issues

This page lists the problems in this repository that we know about and
have not fixed yet. Each entry says what happens, why, and what to do
about it where a workaround exists. Problems that get fixed move to the
"Fixed" section at the end, with the date of the fix.

Last updated: 2026-08-31. Most entries come from a systematic self-check
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

Nothing is open on this list right now. All nine problems from the
2026-08-30 version of this page were fixed on 2026-08-31 — the details
are in the "Fixed" section at the end. New problems will be added here
as they are found.

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

**Fixed 2026-08-31 — four weaknesses found by a security review of the
whole project.** A review using automated scanners plus model readers
went through every place the tool touches files or reads input it does
not control. Four real weaknesses were found and fixed the same day.
First, a citation key coming from an imported bibliography could
contain path characters that made the source downloader write its file
outside the sources folder. Second, a DOI printed inside a downloaded
file could count as permission to overwrite an existing source file
during inbox ingestion. Third, one report page (a search-results viewer
not yet reachable from the command line) did not check link addresses
before embedding them. Fourth, the interactive setup helper printed a
pasted API key back to the screen inside its "command to skip the
wizard next time" line, so saving that command would save the key with
it. The fixes: file names and keys are cleaned in one shared module
(`modules/papertrail/safe_paths.py`) before any write, overwriting now
requires a key-named file, link addresses are checked, and the printed
command shows the key redacted. None of this affects verdicts.

**Fixed 2026-08-31 — the six tool problems from the previous version of
this page.** None of these fixes changes any verdict. They add warnings,
plain error messages, and clearer report displays. Their old numbering:

- *(1)* A typo in a citation marker used to be dropped without any
  warning. Examples are `[[my key]]` with a space and `[[key]` with a
  missing bracket. The sentence silently became an unchecked "own"
  claim. The tool now prints a warning for each marker-like tag it
  cannot read. The same warning appears in the yellow banner at the top
  of the viewer. The interactive setup helper now uses the same marker
  rules as the real reader and lists the typos it finds.
- *(2)* A source file with no readable text used to look like an
  ordinary rejection. (A scanned PDF stores pictures of pages instead
  of text.) It is now a separate grey "could not be checked" state. The
  claim's card says in plain words that the file has no readable text.
  The report header counts it as "unverifiable" instead of a judged
  failure. When a claim cites several sources, the card names which one
  was unreadable. The stored verdict value is unchanged, so nothing
  downstream moves. The same fix also covers a second case the page had
  not listed. It was found by a measurement during the same day's
  library update. A damaged font inside a PDF can replace every letter
  with a different one, so the text reads as gibberish while looking
  structurally normal. That case used to sail through to the judgment
  step with no notice at all. The tool now measures the share of
  invisible control characters in each source's final text. Real
  language in any alphabet has essentially none of them. A file over
  the line gets the same grey "could not be checked" treatment, and the
  card says the text came out garbled. Do not expect to see this state
  in a normal run. After the same day's reading fixes, zero of the
  project's 227 test source files trip it. It is a guard for the next
  broken PDF, not a message you should ever see on healthy sources.
- *(3)* Blocking the one-time first-run model download (about 440
  megabytes) used to end in a raw technical error. The run now stops at
  startup, within seconds, with a plain explanation — including a note
  when the `..._OFFLINE=1` settings are the cause.
- *(4)* On Windows, `--open` built a browser address in the wrong form.
  The address is now built by the standard library routine that is
  correct on every platform.
- *(5)* A missing `claude` program under `--backend claude-code` used
  to end in a full Python error trace. The run now exits with the
  one-line explanation only.
- *(6)* A failure inside one part of the optional `--argument-map` pass
  used to render like a genuine empty finding ("no cruxes identified").
  A failure of all three parts made the whole panel disappear. Each
  failed part now shows an amber "this check failed and did not finish"
  line. The panel renders even when every part failed. One limit
  remains. The `--fix-claim` command rebuilds the report from the saved
  files, and a failed part saved no file. A report rebuilt that way
  shows the plain empty wording again.

**Fixed 2026-08-31 — the three documentation notes for people
re-checking the published benchmark numbers.** These were numbered 7, 8
and 9 in the previous version of this page:

- Two supporting documents were named without their folder in
  `benchmarks/wice_anchor/README.md`. The note now gives their full
  paths from the repository root
  (`docs/archive/NIGHT_LOG_2026-07-12_accB.md` and
  `docs/FIRST_CHECK_RUN.md`).
- The "58 rows" sentence in `FOR_REVIEWERS.md` now says the count
  includes both full and partial support. Counting only full
  "supported" labels gives 10 rows from the same data.
- The benchmark scorer's "FALSE-SUPPORT FAILURE" banner now prints a
  note for re-checkers. The cases it reports are the false-support
  cases the submission itself already discloses (3 in the base set, 6
  after the disputed rows were re-judged). It is not a new failure.

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
