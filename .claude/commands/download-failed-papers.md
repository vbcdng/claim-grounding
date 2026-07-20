---
description: Fetch the sources the automated downloader couldn't — Claude uses web search for the stragglers
---

Fetch the papers `download_sources.py` failed to get automatically. The tool
respects paywalls and only follows open-access cascades, so a real manifest
always leaves some entries "missing"; your job is to find open-access copies of
those with judgment + web search, and file them so the project can use them.

Project folder: $ARGUMENTS (the dir holding `sources_manifest.json`, `sources/`,
the `*.refs.txt`, and `download_report.md` — the importer's `--output-dir`. If
empty, use the most recently modified project under `data/*/` that has a
`sources_manifest.json`.)

Do this:

1. **Refresh the status.** Regenerate the report from disk so it reflects
   what's actually there:
   `python3 download_sources.py --manifest <project>/sources_manifest.json --report-only`
   Then read `<project>/download_report.md`. It has three actionable sections:
   **Content mismatch** (wrong document saved), **Missing — have a link**, and
   **Missing — need a literature search**.

2. **For each missing / mismatched entry**, in report order:
   - Find an **open-access** copy: prefer the DOI/landing link in the report;
     then web-search the exact title (+ first author, + year) for a PDF on the
     publisher OA page, arXiv, PMC, an institutional repository, or the author's
     site. **Never bypass a paywall** — if only a paywalled copy exists, leave it
     for the author and say so.
   - Confirm it is the RIGHT document before saving: the title/authors on the PDF
     must match the manifest entry (this is exactly what the "content mismatch"
     rows are warning about).
   - Save the file into `<project>/inbox/` (create it if needed). Keep the
     original filename or name it `<key>.pdf` — the ingester matches by
     key / DOI / title and won't guess on ambiguity.

3. **File them in.** Run the ingester (no network, no LLM — it moves matched
   files into `sources/`, fixes the refs extension, and updates the report):
   `python3 ingest_downloads.py --manifest <project>/sources_manifest.json`
   Anything it reports as *ambiguous / unmatched* stays in `inbox/` — rename it
   to `<key>.<ext>` and re-run, or note it for the author.

4. **Re-check** with `--report-only` again and summarize: how many were fetched,
   which are still missing and why (paywalled / no OA copy found / ambiguous),
   and which had a content mismatch you replaced. Do NOT re-run the verifier —
   fetching sources is preparation; the author decides when to re-verify.

Guardrails: open-access only, never a paywall workaround; never save a document
whose title/authors don't match the entry (a wrong source is worse than a
missing one — it manufactures false "supported" verdicts downstream); never edit
the manifest or refs by hand — let `ingest_downloads.py` do it.
