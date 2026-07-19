# Repair playbook — how a fixing agent applies a viewer review

The contract between the viewer's review export and any agent (Claude Code via
`/apply-review`, or a chat LLM given the copied repair brief) that repairs the
author's text. Written for the agent; the author only needs the Workflow section.

## The workflow (author's view)

1. Run `verify_my_text.py`, open `viewer.html`, and mark cards with the triage
   buttons: **wrong source** / **rewrite text** / **find proof / rewrite** /
   **verdict wrong** / **needs citation** / **other**
   (free comment), plus notes. Marks persist in the browser (per run).
2. Export: **Download review file** — saved as `review_<run>_<date>.json`, into
   your once-chosen folder (**Save location…**, Chromium) or the browser's
   Downloads; move it into the run folder if it isn't there — for the
   `/apply-review` command. Or **Copy repair brief** to paste into any LLM.
3. The agent repairs the text (rules below), then re-runs the verifier.
   The re-run is **incremental**: unchanged claims keep their verdicts free of
   charge; the new viewer gets a **Changed (N)** filter and ✎ markers, so
   checking the fixes takes a minute.

## Where things live

Given a run folder (e.g. `data/paper1_verification/`):

- `analysis.json` — the run's full result; `metadata.text_file` is the absolute
  path to the author's article, `metadata.sources_dir` the sources folder,
  `metadata.model` the judge model.
- `review*.json` — the export (named `review_<run>_<date>.json` since
  2026-07-07; a bare `review.json` still counts — consumers take the newest
  `review*.json` by mtime): `{run, exported, marks:[...]}`. Each mark entry is
  self-contained: claim id/text/markers, verdict + judge reason + confidence,
  the quoted evidence per source, the author's `marks` (`wrong_source`,
  `rewrite`, `more_support`, `verdict_wrong`, `needs_citation`, `other`) and `note`, and for unsupported claims
  `alternatives` — the closest passages from the run's *other* sources.
- `analysis_prev.json` — the previous run, kept for diffing.
- `verdict_feedback.json` — created by the agent for `verdict_wrong` items (see
  below).

## Ground rules

- **Keep every `[[key]]` citation marker intact.** Keys map to source files via
  the references list (`<text>.refs.txt` or the `[References]` block). Changing
  which source a claim cites = changing its `[[key]]`, never deleting it.
- **Severity gate** (owner requirement): *minor* fixes — hedging, precision,
  dropping an unsupported qualifier, tightening a number to what the evidence
  says — apply directly. *Conceptual* changes — the argument itself shifts —
  show the author old → new and ask before applying.
- **Never invent citations, sources, or evidence.**
- Edit the article file at `metadata.text_file` (or a copy the author names).

## Guardrails (2026-07-04) — the repair loop writes to the judge's own test

Repairing text so it passes re-verification is Goodhart-prone: the agent could
optimize toward what the judge accepts instead of what is true. These rules are
hard requirements for any fixing agent:

1. **Edit ledger.** Every applied text change is appended to
   `<run folder>/changes.md` before re-running: claim id, the exact old text →
   new text, the mark type, one line of reasoning, and the **verbatim evidence
   quote** that justifies the new wording. No entry, no edit. The ledger is the
   author's audit trail — re-verification only checks the judge's opinion.
2. **Citation swaps require the quoted passage.** Swapping a `[[key]]` (the
   `wrong_source` path) is only allowed after quoting, in the ledger and to the
   author, the exact passage from the NEW source that establishes the claim.
   "High cosine relevance" or an abstract that sounds right is not enough — no
   quote, no swap.
3. **Never weaken a quantitative claim to make it pass.** Numbers, percentages,
   magnitudes, rankings: if the evidence does not support the figure, tell the
   author — do not round it off, hedge it ("roughly", "up to"), or drop it so
   the judge says yes. A wrong number softened into vagueness is still wrong,
   and now it's also unverifiable.
4. **One repair → verify cycle, then a human read-through.** After one
   `/apply-review` + incremental re-run, stop. Remaining unsupported claims go
   back to the author with the ledger; do not iterate again on the reds. Each
   further automated cycle optimizes the text toward the judge, not the truth.

## Per mark type

### `rewrite` — text needs rewriting
The claim overclaims relative to its cited evidence. Rewrite minimally so the
quoted evidence fully supports the sentence(s); keep the author's voice and the
`[[key]]` markers. Minor/conceptual gate applies.

### `more_support` — find more proof, else rewrite (2026-07-18)
The author thinks the claim may be right but the shown evidence doesn't prove
it (typical on amber / NOT-PROVEN-AS-WRITTEN cards). **Evidence first, edit
last**, in order:
1. Search the FULL TEXT of the cited source(s) — and the run's other sources —
   for sentences that actually prove the unproven part(s). Quote every
   candidate **verbatim** with its source; never paraphrase a proof.
2. If real proof exists: report it to the author; the text needs **no change**
   (the tool missed evidence — a re-run's arbiter can pick it up).
3. Only if no proof exists anywhere: fall back to the `rewrite` path — minimal
   edit (hedge, narrow, or drop the unproven part). Minor/conceptual gate
   applies.

### `wrong_source` — needs a different source
The claim may be fine; the citation is wrong. In order:
1. Try the `alternatives` in the mark entry — passages from sources already in
   the run. If one genuinely supports the claim, swap the `[[key]]` to that
   source's key (see `analysis.json` → `sources[]` for key ↔ title mapping).
2. If none fits, find a real external source (web search), or point the author
   at the viewer's **Copy Claude Science request** button — it bundles every
   wrong-source claim into ONE research request. When the author brings back
   the report export, merge its bibliography into the project:

   ```
   python3 import_claude_research.py --input <export.md or .bib> \
       --merge-into <project dir with sources_manifest.json>
   python3 download_sources.py --manifest <project dir>/sources_manifest.json
   ```

   (Duplicates are skipped by DOI/title; new keys are appended to the refs
   file.) Alternatively: direct finds via `download_sources.py`, or manual
   downloads dropped in `inbox/` + `ingest_downloads.py`. Then cite the new
   `[[key]]` in the text.
3. If no source can be found, tell the author — do not soften the claim
   silently (that's a `rewrite` decision, theirs to make).

### `verdict_wrong` — the tool is wrong, not the text
Feedback about the verifier, **never a text edit**. Append to
`<run folder>/verdict_feedback.json` (create as a JSON list if missing):

```json
{"claim_id": "t37", "text": "...", "tool_verdict": "supported",
 "author_says": "wrong", "note": "...", "timestamp": "YYYY-MM-DD"}
```

This file is ground truth for judge tuning (see `docs/PAPER1_TUNING_STATE.md`)
and is consumed by every subsequent run: disputed claims get an **author
disputed** chip in the viewer and are excluded from `--second-opinion`
re-checks (the author's ruling outranks any model's). An entry applies while
the claim's text is unchanged; rewriting the sentence retires it.

### `needs_citation` — the passage should cite a source
Usually an **own** claim the own-split chipped "citation needed?" and the
author confirmed (but the mark is available on any card). Find a real source
— the `wrong_source` search path applies (Claude Science request /
`download_sources.py` / manual `inbox/` + `ingest_downloads.py`), register it
in the project (manifest + refs), then add the `[[key]]` citation to the
passage. **Never invent a citation**; if no source can be found, tell the
author — leaving the passage uncited is their call.

### `other` — anything else (free comment)

The author's note IS the instruction. Read it and decide which of the above
paths it really is (a rewrite, a source problem, tool feedback) and follow
that path's rules. If the intent is unclear or it fits none of them, **surface
it to the author** with your best interpretation and wait — never guess an
edit from an ambiguous note.

## Finishing up

Re-run the verifier from the repo root:

```
python3 verify_my_text.py --text <metadata.text_file> \
    --sources <metadata.sources_dir> --output-dir <run folder>
```

Unchanged claims are not re-judged (near-zero cost; source-file changes are
hash-detected and re-judged automatically). Add `--second-opinion` if the
author wants the re-run cross-checked by a second model (~a cent).
Report to the author: what was applied (with the `changes.md` ledger), what
awaits their old → new approval, what went into `verdict_feedback.json`, and
point them at the new viewer's **Changed** filter. Then STOP — guardrail 4:
no second automated repair cycle; the remaining reds are the author's call.
