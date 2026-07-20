---
description: Apply a viewer review (review*.json) — fix marked claims in the author's text
---

Apply the claim-grounding review exported from the viewer.

Run folder: $ARGUMENTS (if empty, use the run folder containing the newest
`review*.json` under `data/*/` — the viewer exports distinguishable names like
`review_<run>_<date>.json`; a bare `review.json` also counts. If none is found
there, check `~/Downloads/` for a fresh `review*.json` and move it into the
matching run folder first — match by its `run.output_dir` field. On several
candidates, take the newest by mtime).

Follow `docs/REPAIR_PLAYBOOK.md` exactly — including its **Guardrails**
section. In short:
1. Read the newest `review*.json` in the run folder; the article is at `run.text_file`.
2. For each marked claim, act per mark type (rewrite / more_support /
   wrong_source / verdict_wrong / needs_citation / other). `more_support` =
   hunt the sources' full text for verbatim proof of the unproven part FIRST;
   only rewrite if no proof exists anywhere. `needs_citation` = find and
   register a real source, then add the `[[key]]` citation — never invent one.
   `other` = free-form: read the author note and decide;
   if the intent is unclear, ask the author instead of guessing an edit.
   Minor wording fixes: apply directly. Conceptual changes:
   show old → new and ask the author before applying. Keep every `[[key]]`
   marker intact; never invent citations or evidence.
3. Guardrails (hard rules): log every applied edit in `<run folder>/changes.md`
   (old → new, why, the verbatim evidence quote) BEFORE re-running; swap a
   `[[key]]` only after quoting the passage from the new source that
   establishes the claim; never weaken a quantitative claim (numbers,
   percentages, magnitudes) to make it pass — flag it to the author instead.
4. `verdict_wrong` items go to `<run folder>/verdict_feedback.json`, not into
   the text.
5. When done, re-run the verifier (incremental — unchanged claims are free):
   `python3 verify_my_text.py --text <run.text_file> --sources <run.sources_dir> --output-dir <run folder>`
6. Summarize: applied fixes (point at `changes.md`), pending approvals,
   feedback recorded; point the author at the new viewer's **Changed** filter.
   Then stop — ONE repair→verify cycle per review; remaining unsupported
   claims go back to the author, not into another automated pass.
