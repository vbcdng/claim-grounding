---
name: checked-research
description: Deep-research a topic and produce a machine-verifiable cited text — runs Claude Code's built-in deep-research workflow, shapes its report to this tool's input format (my_text.md + my_text.md.refs.txt), collects the sources, runs the checker, and does at most one repair cycle. Invoke when the user wants a researched text whose every citation is verified against its real source.
---

# Checked research — deep-research whose citations get verified

You produce a cited text whose every citation has been machine-checked against its real source, using the claim-grounding tool in this repository. The user gives you a TOPIC (skill argument or ask). This skill governs the PROCESS; the text-shaping rules live in two repository files you must actually read when told to below: `RESEARCH_WRITE_PROMPT.md` (what a cited sentence may claim, where markers go) and `CONVERT_MY_TEXT_PROMPT.md` (the exact input format). The process guardrails mirror `AGENT_LOOP_PROMPT.md` — if this skill and that file disagree, tell the user instead of guessing. These three files sit at the repository top level in the public release and under `docs/` in the development tree; glob for them before reading.

## Hard rules (never break these)

- NEVER invent a citation, and never cite a source you have not read.
- A source file must hold the ACTUAL document — the fetched page text, PDF, or transcript, in full. NEVER fill it with your own summary, excerpts, or paraphrase: the checker would then verify the text against words you wrote yourself, which proves nothing. If you can only obtain fragments, say so in the file and the report.
- NEVER silently substitute a different paper for the one the research step cited. A replacement is a decision the user makes.
- NEVER bypass a paywall. An unreachable source is recorded as unreachable, honestly.
- ONE repair cycle maximum, then stop and report (docs/REPAIR_PLAYBOOK.md guardrail 4).
- Nothing paid without the user's explicit go in this conversation: the checker's default judge is free; the default arbiter (`--arbiter`, on by default) is a PAID OpenRouter model — ask, or pass `--no-arbiter`, or use `--backend claude-code` (all $0). Name the model and expected cost when you ask.
- Report failures as failures. A skipped step, a refused call, or a missing source is stated in the final report, not smoothed over.

## Step 0 — model check

The built-in deep-research workflow's helper agents inherit THIS session's model. Tell the user which model that is before starting. If they wanted a specific research model (e.g. Sonnet), the session itself must run on it — they should restart with that model; you cannot switch the workflow's model from inside.

## Step 1 — research

Run the built-in deep-research workflow (Workflow tool, `name: "deep-research"`), passing the user's topic as the question, extended with: "For every factual claim, name the specific source it comes from. End with a numbered source list giving, per source: authors, year, title, DOI or URL, and open-access status if known." The report it returns is the raw material; its sources list is the citation universe — everything downstream cites ONLY papers from it.

If the Workflow tool is unavailable in your session, or you research any other way (direct web searches, fetching named pages), you MUST say so explicitly, at the moment it happens and again in the final report, naming what you used instead and why. Substituting your own research routine silently counts as a failure of this skill, even when the text comes out fine — the first test run did exactly this and the deviation was only caught by reading the session log.

## Step 2 — shape the text for the checker

Read `RESEARCH_WRITE_PROMPT.md` (the "How the checker reads your text" intro and Variant B rules). The checker splits text into claims AT the citation markers and verifies each claim against the marker's source, so marker placement decides what gets checked. The deep-research report is machine-written, so unlike a user draft you MAY rewrite its sentences to conform. Rework the report into a text that obeys the writing rules, most importantly:

- One `[[key]]` marker on EACH sourced sentence (never one marker covering a run of sentences; never markers grouped at paragraph end). Keys: lowercase author+year (`smith2020`), same source = same key everywhere.
- One source's worth of facts per sentence. A range or list gathered from several papers is split so each number sits with its own citation.
- Preserve the source's direction, magnitude, and hedges. "May be associated" never becomes "causes"; a one-study setting never becomes a general fact.
- A citation that only backs a method, definition, or comparison is worded that way ("following the method of [[key]]"), with your own conclusions in separate, uncited sentences.
- Framing, transitions, and synthesis sentences carry NO marker — the checker labels them "own claim", which is correct.

Save exactly two files in the working folder: `my_text.md` (the text with markers) and `my_text.md.refs.txt` (one line per source: `key = filename.pdf` or `.txt`). This two-file format is the contract in `INPUT_FORMAT.md`.

## Step 3 — collect sources

Create `sources/` and fetch each cited source under the filename in the refs file. Prefer the repository helpers: write a `sources_manifest.json` and use `download_sources.py` (open-access cascade + status report), with `inbox/` + `ingest_downloads.py` for hand-downloaded files.

**Hand the user the missing-source list before checking.** Some sources will resist automated downloading (paywalls, bot-blocking pages) that a person with a browser can save in seconds. So after collection, list every source you could NOT obtain — key, title, URL, and why it failed — and give that list to the user with the fix path: save the file yourself and drop it into `<working>/inbox/` (then `ingest_downloads.py` files it) or directly into `sources/` under the refs filename. Wait for their answer before running the check. If the session is non-interactive and cannot ask, proceed, mark each missing source honestly in its file, and repeat the list prominently in the final report so the user can supply the files and re-run. Never fill the gap yourself with a summary, and never leave a marker pointing at a file that is not really that document.

## Step 4 — check

Run `venv/bin/python verify_my_text.py --text <working>/my_text.md --sources <working>/sources --references <working>/my_text.md.refs.txt --output-dir <working>/run1`, choosing the cost mode per the hard rule above. The check takes many minutes — longer than a default shell-command timeout — so run it with the longest timeout you can set, or as a harness-tracked background command, and log to disk (`./run_logged.sh`). Do NOT end your session while the check still runs: a headless session that exits kills its background children and the run dies half-done. Confirm `analysis.json` exists before moving on; a run folder without it means the check did not finish. Then read it / the viewer: for each claim note verdict, partial-support flags, and amber "no evidence shown" lines.

## Step 5 — one repair cycle, then report

For each unsupported or gap-flagged claim, re-read the verdict's evidence and fix the TEXT (soften, split, re-attribute, or delete the sentence — following the same writing rules), never the verdict. Re-run the checker into `run2`. Then stop, whatever the result, and give the user: what was written, per-claim verdicts before and after, what was repaired and how, every source that could not be fetched, every call that failed, and the run folders' paths so they can open `viewer.html` themselves.
