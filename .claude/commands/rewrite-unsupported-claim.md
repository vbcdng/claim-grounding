---
description: Propose grounded rewrites for unsupported claims — the author approves every edit
---

Propose rewrites for unsupported claims so they match what their cited sources
actually establish. This is **propose-only**: you generate and verify a
suggestion for each claim and show it to the author, but you do NOT edit the
article unless they approve each change (owner rule — the user stays in control).

Run folder: $ARGUMENTS — the run's `--output-dir` (holds `analysis.json`,
`source_claims/`, `embeddings/`, `viewer.html`). You may append a specific claim
id (e.g. `data/paper1_verification t37`) to rewrite just that one; otherwise
target every claim whose `verdict` is `unsupported` in `analysis.json` (skip
those whose `reason` starts with `source_file_missing` — those need the source
fetched, not a rewrite; point the author at `/download-failed-papers`).

Do this:

1. **Generate a verified suggestion per target claim** using the built-in
   fixer (reuses the run's caches, no full re-run, same judge model as the run):
   `python3 verify_my_text.py --output-dir <run> --fix-claim <id>`
   It writes a `fix_suggestion` onto the claim in `analysis.json`, re-judges the
   rewrite by majority vote, refreshes `viewer.html`, and prints the suggestion
   with a ✓ verified / ⚠ inconclusive marker. For a $0 pass on a run whose model
   was Gemini, you may add `--model claude-code/haiku` (dev only — note that the
   verification then uses a different judge than the run).

2. **Present each suggestion to the author**: the claim id, **old → new** text,
   the ✓/⚠ verification result, and the verbatim source passage the rewrite is
   grounded in (from the fixer output / the claim's `evidences`). Group the
   confident ✓ rewrites separately from the ⚠ inconclusive ones.

3. **Apply only on approval.** For each rewrite the author OKs, edit
   `run.metadata.text_file` (the article), replacing the old sentence with the
   approved text and keeping every `[[key]]` marker intact. Before writing,
   append the edit to `<run>/changes.md` (old → new, why, the evidence quote).
   Leave unapproved and ⚠ suggestions in the article untouched.

4. **Re-verify** once, incrementally (only edited claims cost anything):
   `python3 verify_my_text.py --text <run.text_file> --sources <run.sources_dir> --output-dir <run>`
   Then summarize applied vs. pending, and point the author at the viewer's
   **Changed** filter. Stop after one rewrite→verify cycle.

Guardrails (hard rules):
- **Never weaken a quantitative claim** — numbers, percentages, magnitudes,
  directions of effect — to make it pass. If the source only supports a weaker
  number, show that to the author and let them decide; do not silently soften it.
- **Never invent citations or evidence**, and never add a `[[key]]` here — if the
  claim needs a different source, that's `/apply-review`'s `wrong_source` path or
  `find_replacement_sources.py`, not a rewrite.
- A rewrite that still verifies ⚠ inconclusive is a proposal to the author, never
  an auto-applied edit.
