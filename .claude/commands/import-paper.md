---
description: Convert any published scientific paper (PDF / DOI / arXiv / URL) into this tool's input format — database-first, PDF-parsing last
---

Convert a published scientific paper into the tool's input format so its claims can
be verified against its own cited sources. This is the flexible, judgment-driven
path — it handles what the deterministic importer (`import_paper.py`, if present)
can't: docx, superscript citations, footnote styles, papers missing from databases.

Input: $ARGUMENTS — a PDF path, a DOI (`10.…`), an arXiv id, a URL, or a paper
title; optionally followed by an output dir. If no output dir is given, use
`data/<first-author-slug><year>_import/`.

**Strategy rule (owner directive): DATABASE-FIRST, PDF-PARSING LAST.** The paper's
bibliography almost certainly exists in structured form in Semantic Scholar /
OpenAlex / Crossref — fetch it from there. Only fall back to reading the PDF's
reference section when the databases fail, and even then only to resolve ordering
or ambiguities, never as the primary source of titles/DOIs.

Do this:

1. **Identify the paper.** If given an id/URL, use it directly. If given a PDF,
   extract the first pages and look for a printed DOI (`10.xxxx/…`) or
   `arXiv:NNNN.NNNNN` stamp; failing that, take the title from page 1:
   `venv/bin/python3 -c "from modules.papertrail.source_decomposer import read_source_pages; print('\n'.join(read_source_pages('<pdf>')[:2]))"`
   Resolve the title with the never-guess gate:
   `venv/bin/python3 -c "from modules.papertrail.semantic_scholar_api import find_paper_by_title; print(find_paper_by_title('<title>'))"`
   If no confident match (status != 'matched'), STOP and ask the author to supply
   a DOI. Never proceed on a guessed identity.

2. **Fetch the reference list from the databases** (structured, with DOIs/titles/
   years/OA urls — this replaces bibliography parsing):
   `venv/bin/python3 -c "import json; from modules.papertrail.paper_search import neighbors; print(json.dumps(neighbors('DOI:<doi>', 'references'), indent=1))"`
   (also accepts `ARXIV:<id>`; S2 first, OpenAlex fallback, disk-cached).

3. **Extract and trim the body text.** Get the full text (PDF via
   `read_source_pages`, docx/HTML by any means available). Keep Introduction
   through Discussion/Conclusion **including Methods**. Strip: the References
   section (always), Abstract, acknowledgments/funding/declarations, figure and
   table caption lines, appendices. Keep paragraphs intact — the verifier splits
   claims by paragraph + marker.

4. **Generate citation keys** from the database list: `<firstauthorsurname><year>`
   (ASCII, lowercase, charset `[A-Za-z0-9_-]` ONLY — anything else breaks the
   marker parser). Collisions get `_2`, `_3` suffixes.

5. **Place the markers.** Replace every in-text citation with `[[key]]` markers
   placed at the END of the sentence the citation belongs to (see
   `INPUT_FORMAT.md`). Mapping rules:
   - Author-year styles `(Smith et al., 2020)`: match surname+year against the
     database list directly.
   - Numeric styles `[12]`: the database list is UNORDERED — recover the paper's
     own numbering from its reference section (match each numbered entry against
     the known database entries by author+year+title; this is a closed-set
     assignment, not open parsing) and only then map `[12]` → key.
   - A citation you cannot resolve with confidence gets NO marker — list it in
     the summary instead. A missing marker just becomes an uncited claim the
     tool nudges about; a WRONG marker manufactures a false verdict.

6. **Write the four artifacts** into the output dir:
   - `my_text.md` — the trimmed body with `[[key]]` markers.
   - `my_text.md.refs.txt` — `key = <key>.pdf` lines (header comment allowed).
   - `sources_manifest.json` — `{"input_markdown": …, "citation_syntax": …,
     "sources": [{key, title, author, year, url, doi, suggested_filename,
     status}], "unresolved_keys": [], "uncited_bibliography_keys": []}` with
     status `has_link` (url or doi present) else `needs_search` — same schema as
     `import_claude_research.py` output.
   - an empty `sources/` dir.

7. **Sanity-check before finishing:**
   - every `[[key]]` in `my_text.md` exists in the refs file (grep);
   - the text parses:
     `venv/bin/python3 -c "from modules.papertrail import text_decomposer as td; refs, body = td.parse_references(open('<out>/my_text.md').read(), '<out>/my_text.md.refs.txt', '<out>/my_text.md'); cs = td.extract_claims(body); print(len(cs), 'claims,', sum(1 for c in cs if c['markers']), 'cited')"`
   - if fewer than ~30% of the paper's database references ended up cited in the
     text, say so loudly — the citation style probably defeated the mapping
     (superscripts etc.) and the author should eyeball the result.

8. **Summarize and hand off:** counts (claims, cited, unresolved citations with
   their sentence text), which database supplied the bibliography, what was
   stripped. Next steps for the author:
   `python3 download_sources.py --manifest <out>/sources_manifest.json` then
   `venv/bin/python3 verify_my_text.py --text <out>/my_text.md --sources <out>/sources --output-dir data/<name>_verification --backend claude-code`.

Guardrails: never invent a DOI, title, or key — every key must trace to a database
record (or, last resort, a reference-section entry you can quote); an unresolvable
citation is reported, never guessed; keys strictly `[A-Za-z0-9_-]`; the database
bibliography always beats a PDF-scraped one on conflicts; never edit the paper's
prose beyond citation-marker replacement and section stripping — the claims must
stay the author's exact words.
