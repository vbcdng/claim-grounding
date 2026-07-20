# Architecture — how everything works

Detailed reference for the whole claim-grounding tool. Written 2026-07-06 from a full
code read; **keep it updated** — whenever a change alters behavior described here
(flags, schemas, pipeline stages, caching, file contracts), update the matching
section in the same commit. CLAUDE.md stays the short operational summary; this file
is the source of truth for *how it works*.

Contents:
1. [Big picture & data flow](#1-big-picture--data-flow)
2. [Repository layout](#2-repository-layout)
3. [Input contract & project directory](#3-input-contract--project-directory)
4. [Core verification pipeline (`verify_my_text.py`)](#4-core-verification-pipeline)
5. [Module reference — core pipeline](#5-module-reference--core-pipeline)
6. [Additive judging passes (own-split, second opinion, partial-check)](#6-additive-judging-passes)
7. [The HTML viewer](#7-the-html-viewer)
8. [Assessment layer (`--argument-map`) & other extras](#8-assessment-layer--other-extras)
9. [Source acquisition & import tooling](#9-source-acquisition--import-tooling)
10. [The review-and-repair loop](#10-the-review-and-repair-loop)
11. [Caching — what is keyed by what, where](#11-caching-summary)
12. [Output artifacts & schemas](#12-output-artifacts--schemas)
13. [Config, API keys, models, cost](#13-config-api-keys-models-cost)
14. [Benchmarks, quality gates, tests](#14-benchmarks-quality-gates-tests)
15. [Design invariants (do not break)](#15-design-invariants)
16. [Gotchas](#16-gotchas)
17. [docs/ index](#17-docs-index)

---

## 1. Big picture & data flow

The tool checks cited writing against its source documents. The author writes prose
with `[[key]]` citation markers, supplies the cited files, and gets back
`analysis.json` (verdicts + evidence) and a self-contained `viewer.html`.

```
import_claude_research.py  →  download_sources.py  →  ingest_downloads.py  →  verify_my_text.py
     (parse export)            (auto-fetch OA PDFs)     (file manual drops)      (grounding)
                                                                                     │
                          ┌──────────────────────────────────────────────────────────┘
                          ▼  viewer exports review.json
        find_replacement_sources.py   /apply-review   /rewrite-unsupported-claim   /download-failed-papers
             (find better papers)      (fix text)        (rewrite claims)           (web-search stragglers)
```

Inside `verify_my_text.py` a run is three stages (names inherited from the upstream
PaperTrail project; the code runs Stage 2 first because it is free):

- **Stage 2** — parse references + split the author's text into claims by `[[key]]`
  markers (`text_decomposer.py`, no LLM).
- **Stage 1** — index each cited source: a full sentence index always
  (`source_decomposer.py`, no LLM for this part, disk-cached by content hash);
  atomic-claim decomposition NEVER on new runs (**opt-in 2026-07-10 → CLI flag
  removed 2026-07-16, owner ruling; redesign parked in IDEAS.md "Source
  decomposition v2"** — verdicts/supporting sentences never used the
  claims, 0/90 measured in docs/SUPPORTING_SENTENCES_HOW_IT_WORKS.md; they feed
  only the advisory layer: unused-points panel, re-citation alternatives,
  component hunt, partial-check round-2 context, origin trace).
- **Stage 3** — ground each claim: SPECTER-cosine candidate retrieval → per-source
  LLM judgment → chunked full-text extraction fallback → multi-source combined
  judge (`matcher.py`). Extraction-fallback positives pass a deterministic
  **subject-entity guard** (2026-07-12, `_subject_tokens`/`_subject_in_source`;
  design + blast-radius scan in `docs/SUBJECT_GUARD.md`): when a claim LEADS
  with a proper-noun run (its subject), a fulltext/combined positive is only
  accepted from a source whose full text mentions at least one subject token
  (diacritic-folded). Strictly leading + a frozen common-words set keep the
  guard off ordinary sentence openers ("Reviews of…") and buried attributions
  ("… Shin and colleagues found …"); a multi-token leading run that COLLAPSES
  to a single checkable token (length/common filters — "Frontier AI" →
  'frontier') also disarms (2026-07-17: the collapsed fragment demanded the
  generic word 'frontier' verbatim in salvi2025 and killed a true tail_rescue
  positive, paper1 t27). Fired = claim carries
  `subject_guard: {subject, missing_from}`; component rescue and arbiter
  rescue skip guarded sources (a verbatim-but-subjectless quote must not
  re-buy the positive). Born from the WiCE train2 waleedmajid false-support
  (a team score-table judged 2-0 to prove a claim about a player the source
  never names); corpus scan: 2 fires in 57 fulltext positives, both
  WiCE-labeled not_supported; cosine-path positives are NOT guarded (zero
  observed failures there — watch item).

Embeddings are always local CPU (SPECTER); only decomposition/judging/extraction
calls hit an LLM (or the free local `claude` CLI backend).

**Verdicts:** `supported` / `unsupported` / `own` (author's uncited claim — indigo,
never red, nothing was checked) / `omitted` (source claims the author didn't use,
ranked by relevance). `partial_support` and `over_citation` are *flags* on a
supported verdict, never new verdicts.

## 2. Repository layout

| Path | What it is |
|---|---|
| `verify_my_text.py` | CLI entry point / orchestrator (§4) |
| `import_claude_research.py` | Claude Science export → project dir; `--merge-into` merges a bib (§9.1) |
| `download_sources.py` | Open-access source fetcher + `download_report.md` (§9.2) |
| `ingest_downloads.py` | Files manual downloads from `inbox/` into `sources/` (§9.3) |
| `find_replacement_sources.py` | Review-loop replacement-paper finder, propose-only (§10.3) |
| `modules/papertrail/` | All library code (§5, §6, §8, §9) |
| `config/prompts/pt_*.txt` | The 12 production prompts (§13.4) |
| `config/gemini_config.json` | Model default — only the `claim_validation` block is used |
| `benchmarks/` | Regression gate + live-API benches (§14) |
| `tests/` | ~37 offline `unittest` files (§14.5) |
| `data/` | Verification runs & example projects (gitignored) |
| `docs/` | Design/state docs (§17) |
| `.claude/commands/` | Prompt-only slash commands (§10.4) |
| `INPUT_FORMAT.md` | The input contract — the seam to any upstream producer |

## 3. Input contract & project directory

A **project directory** (produced by the importer or by hand) canonically holds:

- `my_text.md` — prose with `[[key]]` markers. Markers delimit claims: text before a
  marker group is one claim, cited by those markers; a paragraph with no marker
  becomes an `own` claim. Best authoring: one claim per paragraph, markers at the end.
- `my_text.md.refs.txt` — `key = filename` lines (`#` comments allowed). This is the
  single `[[key]] → filename` source of truth; three tools rewrite it idempotently
  (importer preserves existing lines, downloader fixes extensions, replacement
  finder appends/replaces).
- `sources_manifest.json` — per-source metadata (key/title/author/year/url/doi,
  `status: has_link|needs_search|not_in_bibliography`, `merges` provenance). Used by
  the downloader and, when present next to the text file, by `verify_my_text.py` to
  give sources real titles.
- `sources/` — the actual `<key>.pdf` / `<key>.txt` files.
- `inbox/` — drop manually-downloaded files here for `ingest_downloads.py`.
- `download_report.md` — persistent per-source status (regenerable, `--report-only`).

Reference resolution precedence in `text_decomposer.parse_references`:
explicit `--references` file → sibling `<text>.refs.txt` → a trailing
`[References]` block inside the text itself.

The **run/output dir** (`--output-dir`, default `data/my_text_verification`) holds
`analysis.json`, `analysis_prev.json`, `viewer.html`, `source_claims/` (decomposition
cache), `embeddings/` (SPECTER cache), `sources/` (copies for viewer deep-links),
plus optional `review.json`, `verdict_feedback.json`, `changes.md`,
`argument_map.json`/`.argdown`, `independence.json`, `crux.json`, `provenance.json`,
`replacements.json`, `replacement_report.md`.

## 4. Core verification pipeline

`verify_my_text.py::main()` step by step:

1. **Wizard** — no args on a TTY → `wizard.run_wizard()` walks the whole pipeline
   step by step (import → download → verify flags; see §5.8) and prints the
   equivalent command; result feeds normal argparse.
2. **Backend normalization** — `apply_backend(args)`: `--backend claude-code`,
   `--model claude-code[/alias]`, or a bare alias all canonicalize to
   `claude-code/<alias>` and install the Haiku-tuned judging rubric
   (`benchmarks/pt_combined_judgment_haiku_v1.txt`) via `matcher.PROMPT_OVERRIDES`.
3. **`--fix-claim <id>` short-circuit** → `claim_fixer` on a finished run's caches
   (no full re-run), then exit.
4. **Stage 2** — `parse_references` + `extract_claims` → ordered
   `[{id:"t0", text, markers}]`. Claim IDs `t0,t1,…` are assigned in document order
   and are the stable identifiers everywhere (viewer, fix-claim, reuse, review).
5. **Source hashes** — SHA-1 of each cited file's bytes → `metadata.source_hashes`
   (change detection for the *next* incremental run).
6. **Incremental reuse decision** (`rerun.py`, §5.6) — build `reuse_map` of claims
   whose previous verdicts carry over with zero LLM calls.
7. **Cost estimate** (`cost_estimator`, §13.3) — `--estimate` prints and exits;
   runs above $1 prompt for confirmation unless `--yes`; claude-code backend prints
   a $0 note and skips estimation.
7b. **API-key preflight** (2026-07-14) — one tiny `llm.call` right after the
   judge client is constructed; `None` (auth/bad-model — the provider error is
   already logged by `llm_client`) → clear message + `sys.exit(2)` BEFORE any
   embedding work. Without it a typo'd key surfaced as a per-call error wall
   and a garbage run minutes later (clean-venv test).
8. **Stage 1** — lazily per cited source: `source_decomposer.decompose_source`
   with `extract_claims=False` (hardcoded since the 2026-07-16 flag removal —
   sentence index only, `decomposed:false` in the cache; cache hit = zero LLM;
   a cache that already has claims keeps them and still feeds the advisory
   paths), copy the source into
   `<output-dir>/sources/`, read
   non-PDF full text into `source_texts` (embedded in the viewer).
9. **Stage 3** — `matcher.run(...)` (§5.4) with
   `partial_check=not args.no_partial_check` (default ON).
10. **Own-split** (default ON, `--no-own-split` skips) — tag `own` claims
    structural/opinion/fact (§6.1).
11. **Author feedback** — `verdict_feedback.json` → `owner_flag` on disputed claims.
12. **Second opinion** (`--second-opinion [model]`) — §6.2.
13. **Write** — archive previous `analysis.json` as `analysis_prev.json`, write the
    new one.
14. **`--provenance-export`** — PROV-O-shaped `provenance.json`, no LLM (§8.5).
15. **`--argument-map`** — three assessment passes in this order:
    `argument_map.build_map` → `evidence_independence.assess_independence` →
    `crux.find_cruxes` (crux v2 consumes the independence payload for fragility).
    Each pass is wrapped in try/except; a failure never touches verdicts.
16. **Viewer** — `viewer.generate(analysis, ..., source_texts, assessment)`;
    `--open` launches the browser.

### CLI flags → code paths

| Flag | Default | Effect |
|---|---|---|
| `--text`, `--sources` | required | input text + source folder |
| `--references` | sibling `.refs.txt` | marker→filename map |
| `--output-dir` | `data/my_text_verification` | run dir (re-runs into it are incremental) |
| `--model` | config default (`gemini/gemini-2.5-flash-lite`) | litellm string or `claude-code/<alias>` |
| `--backend {api,claude-code}` | `api` | claude-code = local CLI, $0 |
| `--api-key`, `--api-base` | see §13.2 | file path or raw key; custom endpoint |
| `--estimate` / `--yes` | off | cost preview, no API / skip >$1 confirm |
| ~~`--decompose` / `--decomp-model`~~ | — | REMOVED 2026-07-16 (owner) — passing them is now an argparse error; runs always build the sentence index only; redesign seeds in IDEAS.md "Source decomposition v2" |
| `--concurrency N` | 4 | thread-pool width for decomposition chunks + per-claim judging (clamped to 6 under `--backend claude-code`) |
| `--full` | off | disable incremental reuse |
| `--fix-claim ID` | — | rewrite one unsupported claim from caches |
| `--second-opinion [MODEL]` | off; const `gemini/gemini-2.5-flash` | cross-model flag pass |
| `--no-own-split` | off | skip own-claim classification |
| `--no-partial-check` | off | disable the partial-support check (single- and multi-citation; `--partial-check` is an accepted no-op) |
| `--argument-map` | off | assessment passes (workers capped at `min(concurrency,4)`) |
| `--provenance-export` | off | write `provenance.json` |
| `--open` | off | open the viewer |

## 5. Module reference — core pipeline

### 5.1 `text_decomposer.py` (Stage 2, no LLM)
- `strip_frontmatter(text) -> (title, body)` — a leading pandoc-style `---`
  block never becomes claims; its `title:` names the piece (the viewer derives
  the review filename from it). `parse_references` applies it first.
- `parse_references(text, refs_path, text_path) -> (refs_map, body)` — precedence in §3.
- `extract_claims(body) -> [{id, text, markers}]` — **claims are delimited by marker
  groups, not sentence boundaries** (robust to "et al." etc., exact marker→source
  attribution, $0). Paragraph split on blank lines; text before a
  `(?:\[\[key\]\]\s*)+` group is one claim; trailing text after the last marker in a
  paragraph becomes an uncited claim; a stray marker attaches to the previous claim.
- `_sentence_split` — NLTK `sent_tokenize` with regex fallback (used by tail rescue).

### 5.2 `source_decomposer.py` (Stage 1, LLM + disk cache)
- Cache: `source_claims/<paper_id>.json`, keyed by **SHA-256 of file bytes**
  (`file_hash`) + `CACHE_SCHEMA` (currently 7 — the sentence re-index that
  de-garbles cached evidence; 6 was the web-boilerplate line filter; 5 was the
  per-sentence index). Same content, older schema → in-place upgrade rebuilding
  the sentence index with **no LLM calls**.
- **Claim extraction never runs from the CLI anymore** (`extract_claims` param;
  was wired to `--decompose` — opt-in 2026-07-10, flag removed 2026-07-16, owner
  ruling, redesign in IDEAS.md). The module still builds + caches the sentence
  index (the verdict path's input) with zero LLM calls and marks the cache
  `decomposed: false`. A cache that already has claims is used as-is — the
  advisory paths (unused-points panel, re-citation alternatives, component hunt,
  round-2 escalation context, origin trace) keep consuming existing claims; only
  producing NEW ones is retired. `extract_claims=True` remains callable directly
  (decomp_bench, future v2).
- **Decomposition model** separability (`--decomp-model`) was removed with the
  flag; `decomp_bench.py --model` still exercises any model against the harness.
  `deepseek/deepseek-chat` is benched cleaner + ~30% less fragmented than
  flash-lite (`docs/DECOMP_MODEL_COMPARISON.md`); needs `DEEPSEEK_API_KEY`.
- **Post-decomposition junk filter** (`_is_junk_claim`, model-agnostic, applied
  in `_extract_claims_from_text` before dedup): drops citation/DOI entries,
  funding/COI/copyright boilerplate, table/figure captions, emails, and bare
  leading-statistic fragments ("P < 0.001 …"). High-precision (validated to drop
  ~1.3% of 5077 real eggs claims, all genuine junk); **length is NOT a signal**
  — "Eggs are affordable." is a valid 3-word claim.
- `paper_id = sha1(filename)` (computed in `verify_my_text.py::paper_id_for`) —
  the ID is a hash of the *filename*, not the content.
- Reading: PyPDF2 per page; THREE garble heuristics trigger a `pdftotext`
  (poppler) retry: `_looks_letter_spaced` (>35% single-letter tokens, the
  anthropic2024/macaskill2025 class), `_looks_space_collapsed` (mean alpha-token
  length in [8,20), the mcnamara1987 whole-doc class), and `_looks_locally_glued`
  (a 25+-char alpha run — a localized collapse the whole-doc detectors miss, the
  vincent2019 "tdescribedthedata…" class). The swap is **guarded**: pdftotext
  must be clean on the whole-doc detectors AND, for a localized glue, must
  *reduce* the glued-run count — never trade PyPDF2 for a differently-broken
  reflow. The cache re-decompose guard fires only on `_looks_letter_spaced` of
  the cached CLAIM text (NOT space-collapse: a long-word corpus could sit in the
  band and re-buy the LLM every run). `.txt` sources get the
  downloader's `Source URL:` preamble stripped at read time (cache keys stay raw
  bytes); when that preamble is present (= a downloader-saved WEB page),
  `webtext.drop_boilerplate_lines` also filters bylines, publish/date stamps,
  photo-credit captions, site chrome, all-caps section headers (digit-free only —
  numeric table rows are evidence, audit t6), and ≥4-line headline-dump runs
  (owner walkthrough item 8; hand-supplied .txt is never filtered). The schema-6
  upgrade retrofits clean sentence indexes to cached web sources for $0.
- Segmentation (`sentence_split`): NLTK punkt + a **blob guard** (>600-char
  "sentences" re-split at original newlines; last-resort hard wrap ~300 chars) + a
  **fragment merge** (runs of ≥2 short non-sentence fragments, e.g. table rows,
  merged into one block).
- Decomposition: `_chunk_paragraphs` (~1200-word chunks) → extraction prompt
  (`pt_extract_claims_prompt.txt`) per chunk via `parallel_map` → dedupe.
- Evidence linking: SPECTER cosine, up to 3 sentences per claim at ≥0.75
  (`EVIDENCE_LINK_THRESHOLD`), else single best. Page numbers via normalized
  containment with a 6-word prefix probe for page-straddling sentences.
- Output schema: `{paper_id, key, filename, title, file_hash, schema, num_pages,
  sentences:[{text,page}], claims:[{id, text, evidence, evidence_pages}], warning?}`.

### 5.3 `embeddings.py`
SPECTER (`sentence-transformers/allenai-specter`), local CPU. Per-source vectors
disk-cached in `<output-dir>/embeddings/<pid>.sents.npz` and `.claims.npz`
(content-hash keyed, float16; encoding ~30k texts was ~33 min before caching).
`cosine_matrix` is the shared primitive. Model load is local-first
(`local_files_only=True`, network fallback for a missing copy, 2026-07-12):
without it every load contacted the HuggingFace Hub for freshness checks, which
stall for tens of seconds when the hub throttles unauthenticated clients.
On a non-CPU device the loaded model is probed with one tiny encode and falls
back to CPU on failure (2026-07-14): a CUDA-build torch on a GPU it has no
kernels for reports cuda-available but crashes (`torch.AcceleratorError`) on
first encode — found when a clean `pip install -r requirements.txt` pulled
CUDA torch, which is also why requirements.txt now pins `torch==2.12.1+cpu`
on Linux via the PyTorch extra index.

### 5.4 `matcher.py` (Stage 3 — the core)
Key constants: `OFFTOPIC=0.55` (cosine floor for candidates), `AUTO_SUPPORT=0.97`
(near-verbatim accepted with no LLM), `TOPK=3` candidates/source, `JUDGE_VOTES=3`
(majority-of-3 for fallback/combined judging; early break after 2 agreeing),
`EXTRACT_CHUNK_WORDS=1200`, `EXTRACT_TOP_CHUNKS=6`, `EXTRACT_LEX_CHUNKS=2`,
`TAIL_RESCUE_MAX_SUFFIX=2`, `ALTERNATIVES_PER_CLAIM=3`.
`PROMPT_OVERRIDES: dict` lets a backend swap prompts (claude-code installs the
Haiku rubric there).

**Document date (P3, owner ruling 2026-07-11, night of 07-11→12)**: `run()`
stamps every source with `doc_date` (`_doc_date`: publication date from the
first 25 sentences — tiers: `PUBLISHED DATETIME:` meta, marker-adjacent dates
(`Posted:`/`Updated:`/`Written`/`Date` + ≤60 chars), article-URL path
`/YYYY/MM/DD/`, standalone ≤7-word datestamp lines; narrative event dates
("…26 December 1944…" johnrfox) and web-archive capture RANGES are rejected).
All judged passages then use `_src_label` — `From {title} (article dated
YYYY-MM-DD):` — and both judgment prompts allow resolving RELATIVE time
("this year", "last month") against that date, marking the reason
`DATE-INFERRED:`. `_evaluate` sets claim `date_inferred: true` from the
marker OR a deterministic fallback (claim names a year, no shown evidence
contains it, a used source's doc_date year matches — flash-lite ignores the
marker instruction); the viewer renders a grey "date inferred from article
date" chip (visible-caveat requirement). **P3-general (same night; the
owner's "general rule if it shows in other data types" condition was met by
2 sightings — day-level datestamps + author bylines):** `_doc_author`
(EXPLICIT `AUTHOR:` metadata marker only, never guessed from prose) joins the
header — `From <title> (byline: <author>; article dated <date>):` — the
injected rule also allows byline attribution ("reviewer Annie Zaleski" proven
by the byline + the reviewed content), marks reasons `BYLINE-INFERRED:`, sets
claim `byline_inferred` (marker OR surname-fallback), grey "attribution from
article byline" chip. Same conditional safety: sources with no metadata get
byte-identical pre-P3 prompts. Display titles stay bare; datestamp
lines stay excluded from evidence display (`_unusable_evidence`) — the date
reaches judges only via the passage header. Fixed WiCE b2 t20 (essex "this
year"→2019) + t8 (milwaukee November false amber); agreement 57%→65%, no
regressions.

Per-claim chain (`_evaluate`, pure/thread-safe):
1. **Candidate stage** (`_judge_source` per cited source): rank the source's
   sentences by cosine, judge top-3 above `OFFTOPIC` with the single-source prompt
   (temp 0.0); ≥0.97 cosine auto-accepts — UNLESS the ±1-sentence window carries a
   retraction/contradiction cue (`_CONTRA_CUE_RE`: retracted/erratum/in fact/…), in
   which case the hit is judged like any other (BUG-1, 2026-07-17: a synth
   retracted-newspaper source's near-verbatim first sentence locked in supported
   while the very next sentence walked it back); short-circuit on first support. Passage =
   `From {_src_label}: {±1-sentence window}` (title + doc date, for attribution
   claims and relative-time resolution).
   **Quoted-span probe** (item 15, 2026-07-07): when the claim contains a verbatim
   quote (`"…"`/`"…"`, ≥3 words), source sentences containing that string are
   judged FIRST, ahead of the cosine ranking — a deterministic $0 string search
   that fixes the retrieval miss behind two confirmed false-unsupporteds (t42
   "a peer competitor in AI", t31); the sentence is still judged (a negated quote
   is caught), never auto-accepted. Candidate sentences that are `_degenerate` OR
   `_is_reference_fragment` (bare journal/proceedings names like "Review of
   Economic Studies." — item 19), OR `_is_citation_header` (a DOI or
   "year;volume:page" locator glued into a body sentence, e.g. qin2018
   "…Heart 2018;104:1756–1763. doi:10.1136/…Original research…" — owner
   walkthrough t20) are excluded from ranking and from the closest-sentence
   fallback (`_unusable_evidence`). The citation-header test is **DOI + vol;page
   only, high-precision**: a superscript-author heuristic ("Name,<digit>") was
   tried and removed because it also matched the most valuable statistical
   evidence ("HR, 1.18", "range, 13.0", "ARD, 4.43%").
2. Any support → `verdict=supported, method="llm"`.
3. **Full-text extraction fallback** (`_extract_evidence` per source): chunk the
   sentence index ~1200 words; long sources keep top-6 chunks by cosine ∪ up to 2
   chunks rescued by IDF-weighted lexical overlap (catches verbatim figures
   embeddings miss). Extraction prompt per chunk; map extracted sentences back to
   the index (exact → containment → Jaccard); drop degenerate fragments and claim
   echoes (extraction that parrots the claim, ≥0.85 similarity, unmapped).
   **Membership gate on unmapped extractions** (BUG-2, essay t9, 2026-07-17):
   verbatim-in-source (punctuation- or spacing-insensitive — `_loose_text` /
   `_charstream`, garbled-PDF tolerant) is an unconditional keep; otherwise the
   extraction dies only if it carries a ≥6-token run of the CLAIM's own wording
   that the source doesn't contain (`_unsourced_claim_fragment` — the fused
   claim-tail poison, where the extractor concatenates the claim's tail onto a
   real source sentence and the judge self-proves). Honest non-verbatim
   condensations survive — the strict all-verbatim form flipped paper1 t27 to a
   false unsupported (the tail_rescue proof rode a condensed quote). Drops are
   logged (`membership gate: dropped …`). Pooled
   hits ranked by reciprocal-rank fusion of cosine + lexical ranks, cap 8, judged by
   majority-of-3 with the combined prompt.
4. Combine: one source supported → `method="llm_fulltext"`; else ≥2 sources returned
   sentences → `_combined_judge` over the union (`method="combined_fulltext"`);
   else unsupported. Empty extraction keeps the candidate stage's closest sentence
   so the reviewer always has something to read.

`process_claim` adds: reuse path (previous verdict verbatim, zero LLM) · missing
file → `unsupported` with `reason="source_file_missing: …"` (and on a MULTI-cite
claim where only SOME files are missing, the absent markers ride along in
`missing_markers` so the viewer shows a "source file missing" row instead of
silently dropping them — item 16, t14) · no markers → `own` ·
**component rescue** (a fulltext-unsupported verdict probes the judge's
named-missing components alone; all found + unanimous union re-judge →
`method="component_rescue"`, §6.4) · **tail rescue** (a still-unsupported
multi-sentence claim re-judges its last 1–2 sentences alone; success →
`method="tail_rescue"`, lead-in treated as author framing) ·
**partial-support / over-citation nudges** (§6.3).

`run()` embeds all claims+tail pseudo-claims once, slices per source, judges claims
concurrently via `parallel_map(workers=--concurrency)`, then computes: **omitted**
(source claims none of whose evidence sentences were used anywhere, ranked by max
cosine to the user's text), **alternatives** (for each unsupported cited claim, the
3 closest source claims from the *other* run sources — powers "wrong source"
repair), and **coverage** per source + totals.

### 5.5 `llm_client.py`
- `LLMClient.__new__` returns a `ClaudeCodeClient` when the model string starts
  `claude-code` — every call site transparently gets the free backend.
- litellm under the hood (`drop_params=True`). Retry: 3 attempts; auth/invalid →
  immediate `None`; rate/quota/429 → **65 s** sleep; other transient → `2**attempt`.
- Output-cap guards (2026-07-07): the requested `max_output_tokens` is clamped to
  the model's output ceiling (`litellm.get_max_tokens`, else `FALLBACK_OUTPUT_CAP`
  65536) so batched callers whose cap scales with input (argument_map edges,
  dedup pairs) can't draw a provider 400; a response with
  `finish_reason == "length"` retries with a doubled cap (within the 3 attempts),
  and at the ceiling the truncated text is returned WITH a warning — never
  silently (the audit's 0-edge bug class). claude-code backend unaffected
  (ignores caps).
- `parallel_map(fn, items, workers)` — ordered ThreadPoolExecutor map (threads are
  safe: stateless network-bound calls).
- `extract_json` — tolerant fence/first-span JSON extraction.

### 5.6 `rerun.py` (incremental re-runs; pure, no I/O)
- `match_claims`: exact key = (normalized text, sorted markers) → **reuse**; fuzzy
  (`SequenceMatcher ≥0.6`, +0.05 same-markers bonus) → **prev** (re-judged, viewer
  shows "was: …" diff); else new.
- Reuse gate in `main()`: same model (a missing `metadata.model` forces full re-run)
  + `reusable(prev)` (only judged supported/unsupported carry) + none of the claim's
  cited files changed (`changed_source_files` on SHA-1 `source_hashes`).
- **Outage verdicts never reuse (2026-07-20).** `LLMClient.call()` counts calls
  that ended in None (`failed_calls`); `_evaluate` snapshots the counter and an
  unsupported verdict minted while calls were dying gets `judge_error: true` —
  the negative may be an outage artifact, not the sources. `reusable()` refuses
  those (plus legacy `"no LLM response"`/`"LLM judgment unparseable"` reason
  prefixes from older runs), so a plain re-run retries exactly the affected
  claims. Run end prints a WARNING with their ids; both viewers chip the card
  grey "⚠ not fully judged — API failed" (`jechip`). Under `--concurrency` a
  neighbor claim's failure can over-flag — accepted: "couldn't fully judge" is
  honest during an outage and the retry is free.
- The previous run is archived as `analysis_prev.json`; `--full` disables reuse.

### 5.7 `claude_code_backend.py` (Stream E, $0 dev backend)
`ClaudeCodeClient` shells out to `claude -p --model <alias> --output-format text
--strict-mcp-config --max-turns 1` from a **neutral temp cwd** (so no project
CLAUDE.md leaks into judgments), timeout 240 s. temperature /
max_output_tokens are accepted but ignored. Judging with the Haiku rubric is
validated (0-FP); decomposition/extraction through this backend are **unvalidated —
dev/iteration only**; the ship-gate baseline stays the Gemini API.

**Retry policy (two budgets, P2.1).** The CLI shares ONE subscription across the
whole `--concurrency` fan-out, so a high fan-out trips a rate/concurrency ceiling
that comes back as `rc!=0` with **empty stdout+stderr** (or a transient rate-limit
message). This throttle signature gets its OWN budget — `_THROTTLE_MAX_RETRIES=6`
attempts on a long jittered backoff (`4·2ⁿ`, capped 60 s, +0–50% jitter) so the burst
doesn't re-collide — kept separate from the small generic budget (`_MAX_RETRIES=3`,
`2ⁿ` backoff) so a genuinely broken call still fails fast. Non-retryable errors
(auth / unknown-model / `usage:`) return `None` on the first try. Timeouts burn the
generic budget. Without this, a throttle burst exhausted the tiny generic budget and
returned `None` → claims silently mislabeled **unsupported** (walkthrough #1).
**Concurrency clamp (P2.1b):** `apply_backend` clamps `--concurrency` to
`RECOMMENDED_MAX_CONCURRENCY=6` for this backend (with a warning) so the ceiling
isn't provoked in the first place. **Scaling reality:** claude-code is for
chimp/bentonite-size runs; large (~60-source) papers should use the Gemini default
(faster, clean JSON, audited).

### 5.8 `wizard.py`
(User-facing walkthrough of every prompt/option/default: `docs/WIZARD_GUIDE.md` —
ships in the release zip; keep it in sync with prompt-text changes here.)
Interactive pipeline walkthrough triggered by no-args-on-a-TTY; returns an argv list
fed to the normal argparse path and prints the equivalent command. Since 2026-07-12
it walks the whole pipeline, not just the verify flags (born from an owner terminal
test: a raw `[@key]` Claude-Science export + an empty sources folder produced a
meaningless 2-own-claim run with no warning). Steps:

1. **Text** — pandoc `[@key]` citations with zero `[[key]]` markers are detected as
   a Claude Science export: the wizard offers to run `import_claude_research.py`
   (bib defaults to the sibling `<stem>.bib`) and continues on the imported
   project's `my_text.md`. A text with no markers at all gets a warn-and-confirm
   (default: abort) instead of a silent all-`own` run.
2. **Sources** — after the folder + refs questions, the refs map is checked against
   the folder (refs-named file or `<key>.pdf/.txt/.md`). Missing files plus a
   `sources_manifest.json` (looked for next to the text and next to the sources
   dir) → offer to run `download_sources.py`, then a continue / re-check / abort
   loop that points at `download_report.md` and the inbox + `ingest_downloads.py`
   path. A missing refs mapping entirely (no refs file, no `[References]` block)
   also gets a warn-and-confirm (default: abort).
3–5. Output dir, model/key menu, run options. The model menu (2026-07-16) offers
   the two Gemini picks, **claude-code** ($0 via the local CLI; haiku/sonnet
   follow-up, no key question, menu re-asked when the `claude` CLI is absent),
   Ollama, and a raw litellm string. After the key step a **print-only arbiter
   note** states whether the arbiter will run (claude-code/sonnet $0 under that
   backend; DeepSeek key found) or be skipped (no key — with instructions).
   Run options also offer `--second-opinion` as a y/N (default No; cost note
   adapts to the backend). Argument-map / `--full` / other flags stay CLI-only
   (owner, 2026-07-16).

Pipeline scripts run as subprocesses of the same interpreter (`_run_script`,
injectable in tests — `tests/test_wizard.py::TestWizardPipeline`). No LLM calls in
the module; the only network is the consented download step. Wizard runs always
confirm before spending.

## 6. Additive judging passes

House rule for everything here: **nudge, never veto** — no pass ever flips a verdict;
disagreements become viewer chips + lowered confidence.

### 6.1 Own-split (`own_claims.py`, default ON)
Tags each `own` claim `structural` / `opinion` / `fact` with ~1 tiny LLM call
(`pt_own_claim_class_prompt.txt`). Cache key: model + prompt SHA (stored on the
claim, carried through incremental runs). Unparseable → left untagged, retried next
run (honest gap beats a guessed class). `fact` → amber "citation needed?" chip +
filter — a prompt, not a verdict.

### 6.2 Second opinion (`second_opinion.py`, `--second-opinion`)
A different model (default plain `gemini/gemini-2.5-flash`, same key) re-reads the
same evidence for every judged verdict, both directions. Agreement accepted on 1
call; a disagreement needs a majority-of-3 confirm before flagging. Skips claims
with `owner_flag` (author's ruling from `verdict_feedback.json` outranks any model —
chipped "author disputed"; a flag only applies while the claim text is unchanged).
Result: `second_opinion` chip + low confidence, never a verdict change.

### 6.3 Partial-check (`matcher._partial_flags`, default ON since 2026-07-05)
Targets the over-support FP class (any-1-of-N cited source ⇒ supported; on
single-citation claims, any-1-component matching ⇒ supported) without flipping
verdicts. Runs on every supported CITED claim — single- and multi-citation since
2026-07-07 (owner walkthrough t6/t8: a single-citation compound claim carried an
EU figure found in no evidence). `combined*` and `component_rescue` verdicts are
exempt (they already cleared a component-complete bar). Three-round ladder
(`docs/STREAMC_PARTIAL_FIX.md`):
1. hybrid retrieval — judge against each source's lead/title/abstract zone + the
   candidate window (component-complete combined judge);
2. NEI escalation — on a negative, re-judge against escalated context (title zone +
   top verbatim sentences + cached decomposed source-claims, RRF-ranked); all 3
   votes cast, only a *unanimous* negative proceeds;
3. verify-the-verifier — regex-extract the judge's named "missing" components and
   probe each alone via chunked full-text extraction; if every component is found,
   the flag refuted itself and is dropped.

Survivors → `partial_support` ("partial support?" chip + low confidence + filter).
For each named component that survives round 3 (in NO cited source), a
**component hunt** searches the project's OTHER sources: rank by cosine of the
component vs each source's cached decomposed-claim vectors, extraction-probe the
top `COMPONENT_HUNT_SOURCES` (2); the result lands in
`partial_support.component_hunt` (`[{component, found_in: [{paper_id,
source_title, key}], searched}]`) so the card can say "X may be supported by
<source>" or, honestly, that nothing on disk backs it. The ALCE-precision side
emits `over_citation` (a cited source the others fully cover → grey dismissible
"over-cited?" chip; needs ≥2 cited sources by construction). A source the claim
NAMES in prose (`_claim_names_source`: the citation key's surname appears in the
claim text, e.g. "…noted by Drago and Laine") is exempt from the over-cite probe —
explicit attribution is not over-citation (item 18, t36). Disable with
`--no-partial-check`.

### 6.4 Component rescue (`matcher._component_rescue`, since 2026-07-07)
The false-unsupported mirror of 6.3 (owner walkthrough t23): a multi-component
claim whose support is SPREAD across a source fails every single-window judgment —
the judge names one component as missing while the other components crowd it out
of the retrieval window. On a fulltext-unsupported verdict (`llm_fulltext` /
`combined_fulltext`), `_evaluate` gets the claim's components — **since round 6
(2026-07-11) from a real LLM split** (`_split_components`,
`pt_component_split_prompt.txt`, one tiny call, ≤4 components; the
reason-regex `_missing_components` is the fallback when the split fails). The
split closed two round-5 false-unsupported holes: an unmatched judge phrasing
no longer skips rescue entirely (r5 t1), and unnamed extra components can no
longer sneak past the all-found bar (r5 t3 — ALL components are probed, so
"every component found" means every part of the claim). Each component is
probed alone via chunked full-text extraction against the cited sources
(lexical scoring canonicalizes digit-grouped numbers — `_canon_tok`: '100,000'
= '100 000' = '100000', round 6). If EVERY component is found, the whole
claim is re-judged on the union of the original windows + the per-component
evidence; only a **unanimous all-votes positive** flips the verdict
(`method="component_rescue"`, the component evidence becomes the claim's
evidence). Anything less keeps the verdict but records
`component_check` (`{found, missing, rescued, evidence:[{component, paper_id,
source_title, sentence, page}]}`) so the card shows which parts ARE individually
backed. **Symmetric component display (P2 owner ruling 2026-07-11, shipped that
night)**: the unsupported card renders `component_check` as a green
"Partly proven despite the verdict" block — each found component WITH its proof
sentence, always visible — plus an amber "✗ Not found in the cited sources"
line listing every missing component (`compcheck-note` / `compcheck-missing`).
Verdicts unchanged; the found evidence no longer disappears into a flat
"unsupported" (WiCE b2 t13: the 1988-Wales-cap proof was found and hidden). Tail-suffix re-evaluations skip the rescue (cost without signal); a
rescued verdict is exempt from the partial-check (it already cleared
per-component verification).

### 6.5 Covering-set evidence display (`matcher._covering_set`, default ON since 2026-07-10)
The improvement-loop round-1 fix (owner-approved; `docs/LOOP_ROUND_1_REPORT.md`).
The owner standard says the SHOWN sentences must prove EVERY component of a
claim, but the grounding chain accepts on the first supporting sentence — so a
supported card typically displayed one sentence covering ≤1 component while the
full proof existed in the source (round 1: 8/11 rows; eggs 25/27; paper1 16/16).
After the verdict, every **supported cited** claim gets ONE extra small LLM call
(`pt_covering_set_prompt.txt`): a hybrid (cosine+lexical `_rrf`) candidate pool —
top `COVER_CANDS_PER_SOURCE` (8) sentences per cited source, seeded with the
already-shown evidence sentences, capped at `COVER_MAX_CANDS` (24) — and the
model maps the claim's citable components to the candidate sentences proving
them (≤4 distinct sentences) plus the components NO candidate proves. Writer's
own voice (framing/transitions) is excluded from components by prompt. Result
lands on the claim as `covering` (`{covered: [{component, paper_id, source_title,
sentence, page, snippet, via?}], uncovered: [..], spans: [{paper_id,
source_title, text, n_used}]}`), `covering_checked: true`. Two follow-ups from
the improvement loop: **escalation** (round 2) — each uncovered component is
probed alone via chunked full-text extraction (`COVER_ESCALATE_MAX=3`); a hit
moves it off the amber line with `via:"escalation"`, so honest ambers survive
by construction. **Reading spans** (owner request 2026-07-11) —
`_covering_spans` builds, per cited source, the used sentences plus ALL the
original text between them (used sentences further apart than
`COVER_SPAN_GAP=8` split into ellipsis-joined segments; a segment over
`COVER_SPAN_MAX_CLUSTER=30` sentences falls back to ±2 windows); pure string
work, no LLM; rendered as the card's "Read it in context" toggle and passed to
the loop harness's sufficiency columns.
**DISPLAY-ONLY, strictly**: any failure is swallowed (no `covering` field, card
unchanged) — the pass can never touch a verdict; verdict-from-coverage is a
deliberately deferred later round. Tail-rescued claims are covered on the TAIL
text only. Incremental reuse: a cached supported claim without
`covering_checked` buys the pass once (mirrors the partial-check buy-once); the
mark carries forward. The estimator prints a "+1 small call per supported cited
claim" caveat.

**Proof-state badge (round-4 fix, 2026-07-11).** After the covering pass,
`matcher._set_proof_state` derives `proof_state` on supported cited claims:
`"partial"` when `covering.uncovered` is non-empty POST-escalation, `"full"`
when the covering covers everything; a claim with no parsed `covering` block
gets no `proof_state` key at all. Pure derivation, zero LLM calls, and the
`verdict` FIELD NEVER CHANGES (a hard flip manufactures false negatives —
audit t8/t28). Viewer: `proof_state=="partial"` renders the amber badge
variant **"NOT PROVEN AS WRITTEN"** (`badge supported partly`; round-8 fix B,
night 2026-07-11→12 — the badge no longer contains the word "supported" when
the card's own amber admits real gaps; owner ruled the class 4×: r5 t0, r6
t1, first-check t1/t4. Rounds 4–7 wording was "SUPPORTED — PARTLY PROVEN"),
its own "Not proven as written" filter chip + "(of which N not proven as
written)" header total, and drops the confidence chip to low; the amber "no
evidence shown for" line is unchanged, and `proof_state` is included in the
review.json claim payload. The change is DISPLAY-ONLY — verdict field,
`proof_state` derivation, and all scorers are untouched.
Incremental reuse: a cached claim whose covering was already bought but that
predates `proof_state` gets it re-derived from the stored block ($0); when the
covering itself re-buys, `proof_state` is recomputed with it. Loop-harness
note: `loop_round.shown_block` must NOT expose `proof_state` (or the amber
line's conclusion) to the sufficiency columns — judges stay independent.

**Pick-verify audit (round-5 fix, 2026-07-11).** After the covering pass, ONE
batched call per supported cited claim (`pt_pick_verify_prompt.txt`,
`matcher._verify_covering`) audits the block in place: **(a) verify** — a
pick that doesn't genuinely prove its component is dropped (the part gets one
escalation re-probe, `PICK_VERIFY_REPROBE_MAX=2`, else goes amber; class-2
pick slack, r2 t7/r3 t3); **(b) dedup** — same-part duplicate picks collapse
to the best one (owner r4 t5); **(c) entity check** — a named specific in the
claim missing from the component list is added and probed
(`PICK_VERIFY_NEW_MAX=2`; kills the r4-t6 false-full class where the splitter
forgot "Finnish"); **(d) common-knowledge tag** — proof-less components that
are everyday commonplaces land in `covering.common_knowledge` (owner r4 t1
ruling): the viewer renders them as a GREY "Not checked — commonly known"
line instead of amber, and `proof_state` counts only REAL gaps (uncovered
minus common_knowledge) — all gaps common ⇒ "full". FAIL-OPEN by design: an
unparseable response or an unreviewed part leaves the covering exactly as
built (display may only tighten, never wreck); an exception is NOT marked
`pick_verified` (failures-never-cached ⇒ retried next run). Reading spans are
recomputed after the audit (dedup changes the used-sentence set). Incremental
reuse: cached covering without `pick_verified` buys the audit once. Cost: +1
small call per supported cited claim.

**Round-7 fix A (night 2026-07-11→12; double-confirmed class r4 t6 Finland +
r6 t3 Agta — the LLM entity check above was single-vote and missed both):**
(1) **deterministic entity check** — `_named_specifics(claim)` (proper-noun
runs incl. "X of the Y" connectors, sentence-initial words skipped, 4-digit
years, percentages; `_SPEC_STOP` drops broad adjectives like "English") is
matched hyphen-normalized against kept pick sentences + component names; an
absent specific becomes a component via regex, not model attention (probed,
else amber; `ENTITY_CHECK_MAX=2`). (2) **majority-of-3 pick verification** for
components that themselves carry a named specific: two extra batched calls on
JUST those parts (`_pick_verify_call`), a pick survives with ≥2 keep votes
(an unparsed/unreviewing vote counts keep — fail-open both directions, so it
also protects against a single flaky DROP). Fires only when such components
exist. Consequence for cached runs: a reused "full" covering whose pick never
shows a claimed year now honestly rederives partial (test
`test_cached_full_covering_goes_partial_on_unshown_year`).

### 6.6 Arbiter (`arbiter.py`, `--arbiter [model]` — **DEFAULT ON since
### 2026-07-14 on every backend**, owner ruling; `--no-arbiter` opts out)
Default-on mechanics (2026-07-14): the argparse default IS the arbiter model,
so bare runs get the tier; `--no-arbiter` disables it (and drops carried
arbiter results + reverts any previous amber resolution on incremental runs);
a missing DeepSeek key downgrades to ONE info-level note + skip (softened from
a WARNING 2026-07-20, judge F-7 — the no-key Gemini path is a documented run)
(`arbiter_skipped_no_key` — previous results are kept, the tier wasn't
declined); under `--backend claude-code` the default routes to
`claude-code/sonnet` ($0) via `apply_backend` — sonnet-as-arbiter validation
is tracked post-ship (task #93, MODEL_SWAP_PROTOCOL §6a). **Gate runs are
pinned `--no-arbiter`** (check_all.sh comment, §14): the gate scores the
frozen judge core; the arbiter tier is additive with its own battery.

Light-touch tier-2 pass (plan + evidence: `docs/ARBITER_PLAN.md`,
`docs/GEMINI_FAILURE_BREAKDOWN_2026-07-12.md` — the trigger set caught 15/15
owner/Fable-confirmed verdict-level failures). A strong-but-cheap model
(default `deepseek/deepseek-v4-flash`; bare flag under `--backend claude-code`
routes to `claude-code/sonnet`, $0; DeepSeek key from `DEEPSEEK_API_KEY` or
`config/deepseek_api_key.txt` via `arbiter.resolve_key` — never the primary
`--api-key`) re-reads ONLY flagged claims with the grader-style
`pt_arbiter_v1.txt` prompt (shown-evidence block + up to ~20k-word relevant
source section per cited paper; the prompt carries the owner's
common-knowledge exemption and an explicit conflict check). **Trigger set**
(`arbiter.trigger`): unsupported verdict (not missing-file), supported with
`partial_support` or post-audit `covering.uncovered`, or a displayed evidence
sentence judged not-supporting (conflict candidate); `own`/`owner_flag` claims
never escalate, clean supported-full rows never escalate. **Verbatim quote
gate** (mandatory, `verify_quotes`): each proof/conflict quote is normalized
(lowercase, alnum, ﬁ/ﬂ/ﬀ/ﬃ/ﬄ folded) and must substring-match the cited
sources' normalized text; failures are dropped + counted (`quotes_dropped`) —
the gate exists because the ONE hallucinated quote in the printing table came
from the strongest grader. Result on the claim:
`arbiter = {model, prompt_sha, trigger, action, missing_subclaim,
rewrite_suggestion, proofs≤4, quotes_dropped, conflict, why}`; reused across
incremental runs while (model+prompt) sha matches; stale results are dropped
when the flag is off; unparseable responses leave NO field (retried next
run). Viewer: "proof may exist" / "better proof exists" (blue),
"arbiter: author fix?" (purple, with rewrite suggestion), "conflicting
evidence?" (amber) chips + notes; quotes shown are gate-verified only.
Metadata: `metadata.arbiter = {model, checked, actions, proof_may_exist,
conflicts, rescued}`. The arbiter itself NEVER decides a verdict.

**Arbiter rescue** (`arbiter.rescue`, default ON with `--arbiter` since
2026-07-12, owner "solve t21"; `--no-arbiter-rescue` keeps chips-only): the
verdict-path half of the proof-may-exist chip, with the component-rescue
contract. For an unsupported claim where the arbiter returned
`wrong_or_insufficient_evidence` WITH gate-verified proofs, each proof quote
is located in its cited source (±2-sentence window; unlocatable quotes are
never judged) and the windows are re-judged by the **PRIMARY judge** with the
standard combined prompt, all votes cast — the arbiter fetches, the primary
judge decides. Only a UNANIMOUS positive flips: verdict→supported,
`method="arbiter_rescue"`, the fetched proofs become the evidences
(via="arbiter_rescue"), any stale `citation_scope` tag is dropped, and
`arbiter.rescued=true` keeps the card's history (viewer: teal "⛑ arbiter
rescue" chip + note). A held attempt records `rescued=false` and is not
re-bought on incremental runs (`--full` retries). Headline totals are
recounted after flips. Never attempted for author-ruled or missing-file
claims. Validation 2026-07-12: regret t21 flipped (the flagship
false-unsupported, both proofs judged 3-0); fresh paper1 `--full --arbiter`
probe (`data/p1_arbrescue_probe/`) scored CLEAN against the hand-audited
ground truth — 24/24 expectations hold, t68/t74 stay unsupported, 0 rescues
attempted (the arbiter's two tool-fetch actions were on supported claims,
never rescue-eligible; t68's fresh read was the author-fix mixed case).

**Amber resolution** (`arbiter.resolve_ambers`, 2026-07-14, owner ruling —
runs whenever the arbiter ran; pure post-processing, no LLM calls): the
display-layer mirror of rescue. A supported claim flagged NOT PROVEN AS
WRITTEN (`proof_state == "partial"`) whose arbiter ruled the claim PROVABLE —
`action` "supported" (shown evidence holds) OR "wrong_or_insufficient_evidence"
(shown evidence wrong, but the arbiter fetched the proof; the t5/Eskelson live
case arrived under this action) — AND returned ≥1 gate-verified proof quote gets
`proof_state = "arbiter_resolved"` +
`covering.arbiter_resolution = {model, proofs, why}` — the badge reverts to
plain SUPPORTED with a teal "⛑ gap closed by arbiter" chip + note quoting the
verified proofs (the original amber line stays in the card's details for
history). Anything else holds the amber — and a held amber now means a second
model with the whole source also failed to produce verified proof. A stale
resolution from a previous run is REVERTED when the current arbiter no longer
confirms it. DISPLAY ONLY: the verdict field was already `supported` and never
moves; the "Not proven as written" filter/count naturally excludes resolved
cards (they key on `proof_state == "partial"`). Only
"add_citation_or_rewrite" (= the source does NOT prove it) holds the amber.
Live validation (pp_arb_validation, sonnet arbiter, 2026-07-14): t5/Eskelson
RESOLVED — the arbiter fetched both missing literacy sentences verbatim
("below twenty percent … Middle Ages" + the "prior to the seventeenth
century … eighteen percent" extension the audit had missed; t1/Eisenstein
correctly HELD — the "containment/snuffed-out heresies" framing genuinely
isn't in the source (a real writer over-claim, the surviving-amber class).
Logged as "Arbiter amber resolution: k/n cleared";
`metadata.arbiter.amber_resolved` carries the ids.

### 6.7 Citation-scope check (`citation_scope.py`, default ON since 2026-07-12)
Owner ask, born from the foi/regret real-paper runs: on imported published
papers the LARGEST unsupported class was citation scope, not judge error — a
passage describing the AUTHORS' OWN study (their power analysis, design,
conclusions) that carries a methods/related-work citation; the tool correctly
finds the cited source doesn't prove the passage, but the author never
asserted it would, so the red card answers a question nobody asked. One tiny
call per **unsupported, judged, cited, un-ruled** claim
(`citation_scope.eligible`; missing-file and `owner_flag` claims skip) with
`pt_citation_scope_v1.txt` classifies the citation's scope: `full` (the
default — the source is expected to prove the passage) / `methods` /
`concept` / `related`. The prompt is deliberately biased: an ATTRIBUTION TEST
("X found/shows/reveals…" → always full), a SAFETY RULE (any doubt → full — a
wrong scoped tag hides a real unsupported claim; a wrong full merely leaves a
red card red), and four worked examples (flash-lite followed the rules but
mislabeled without them). Result:
`citation_scope = {scope, scoped_assertion, reason, model, prompt_sha}`;
reused across incremental runs while (model+prompt_sha) match; dropped when
the claim's verdict flips (e.g. component rescue), when the author rules, or
under `--no-citation-scope`. **DISPLAY ONLY, but a full separate class in the
viewer** (owner 2026-07-12, "so it does not get confused with real
unsupported"): scoped cards re-badge indigo "SCOPED CITATION (<kind>)" with a
note naming the scoped assertion, an "✎ <kind> citation" chip, and a
repair-brief line; they carry the `scopedcite` card/highlight class (indigo
left-column tint like `own`), get their OWN totals entry and "Scoped
citation" filter, are EXCLUDED from the viewer's Unsupported count/filter,
and ride the own segment of the ratio bar — while `analysis.json` still says
`verdict: "unsupported"` (house rule; the 3-paper gate reads verdicts).
Metadata:
`metadata.citation_scope = {counts, scoped_ids}`. Known conservative misses
(accepted): pure own-methods passages with no comparison framing sometimes
stay full/red — the safe direction.

`viewer.py::generate` writes one self-contained `viewer.html` — **server-free and
persistent** (inviolable rule: no localhost server, no embedded PDF.js). Two-column
layout: the author's text (claims highlighted by verdict) | claim cards in
**document order** (owner requirement; verdict grouping is a filter, never the layout).

- **Simple vs expert view (2026-07-14, MVP judge-path M5 — owner decision):**
  the page loads in **simple view** (`<body class="simple">`): each card shows
  ONLY badge + claim id + claim text + confidence chip + the proof sentence
  blocks, plus one always-visible key note — the unsupported reason
  (`unsupp-note`), which is the evaluation itself. The amber covering-gap
  line (`covset-miss`) is ADVANCED since 2026-07-14 (owner ruling: the
  NOT PROVEN AS WRITTEN badge carries the signal in simple view; the line
  naming the unproven part sits behind details — the amber class is mixed,
  retrieval misses vs real gaps). Everything else — all nudge chips (2nd-opinion /
  arbiter / partial / overcite / scope / rescue / date / kind / changed /
  owner / lead-in), all advisory notes (wrapped in `div.adv` at card build),
  the cosine·method meta, the fix box, and the review triage row — hides
  behind a per-card **"▸ details & review"** button (`toggleMore` adds
  `.open`) and a header **expert view** toggle (`applyMode`, persisted in
  `localStorage ptui:expert`) that restores the full pre-2026-07-14 card.
  Exceptions kept visible in simple view: the loud `📎 citation needed?` chip
  (own+fact — the card's whole point) and the `NOT PROVEN AS WRITTEN` /
  `SCOPED CITATION` badge variants (they ARE the verdict display). Also
  simple-view-only decluttering: the own-card "No citation —" explainer box,
  the Partial/Over-cited filter buttons, and the review bar's Save location…/
  Copy research request buttons hide; the assessment panel starts collapsed.
  PURE DISPLAY: progressive disclosure only, nothing removed from the DOM,
  `analysis.json` untouched. Split implemented via `key_note` (always shown)
  vs `note` (advanced) in `_claim_card`.

- **Server-free mechanics:** PDFs open via native-viewer deep-links
  `sources/<file>#page=N&search=<term>`; `.txt` sources are embedded whole in a JS
  constant and rendered client-side with highlighting via `window.open` +
  `document.write`. One reused named side window (`pt_source_side`) lets the user
  dock/resize once and reuse forever.
- **Confidence chip** — `_confidence()` is a deterministic proxy (no LLM, works
  retroactively): low = second-opinion disagreement / partial_support / any 2-1
  vote; medium = combined/tail_rescue/component_rescue methods, no-evidence
  unsupported, weak-cosine fulltext support; high = direct accept or unanimous
  rejection.
- **Chips:** confidence, "⚠ 2nd opinion disagrees", "partial support?",
  "over-cited?", "secondhand evidence?" (render-time regex `_SECONDHAND_RE`: the
  supporting sentence itself carries a citation — cite the original; item 12),
  "sources may disagree?" (`_DISAGREE_RE` on a co-cited row's negative reason;
  item 14), "author disputed", "citation needed?" (own fact) / grey kind chips,
  "✎ changed" (incremental diff, with previous text/verdict), lead-in (tail rescue),
  grey "date inferred from article date" (`datechip`, P3: claim `date_inferred`).
- **Evidence coverage block (§6.5, since 2026-07-10):** a supported card with
  `covering` renders an always-visible amber `covset-miss` line ("⚠ No evidence
  shown for: X") for uncovered components, plus a collapsible "Evidence
  coverage" details mapping each component to its proving sentence (with the
  standard open-source actions) and a nested "Read it in context" toggle per
  source (`covering.spans` — the used sentences with the original text between
  them). Coverage gaps also land in the "Copy repair brief" export.
- **Filters:** all / supported / unsupported / own + conditional **partly proven**
  (proof_state, §6.5), partial, overcite,
  citeneeded, changed + **Low/Medium confidence** (item 1 — cards carry a
  `conf-<level>` class from `_confidence()`; high stays chip-only).
- **Covset grouping (owner r2 t5, 2026-07-11):** in the "Evidence coverage"
  block, ADJACENT parts proven by the same sentence render as one row — parts
  listed together, the sentence quoted once beneath; every (part, sentence)
  pick renders (a part proven by several sentences shows them all).
- **UX (owner walkthrough items 2-5/7, 2026-07-07):** a 4th triage mark
  **`other`** (free comment; the fixer surfaces it, never guesses); review
  exports save as **`review_<run>_<date>.json`** (`run.run_name` sanitized, since
  2026-07-11 from the text's frontmatter `title:`, falling back to the text
  file's stem, then the output-dir basename — loop rounds all shared basename
  "app"; every consumer accepts the newest `review*.json`);
  **Save location…** (Chromium File System Access API — directory handle
  persisted in IndexedDB `ptui/handles`; plain `<a download>` fallback
  elsewhere); a **hide header** master toggle collapses everything above the
  two columns (persisted in `localStorage ptui:tophidden`); `brush(id, from)`
  scrolls the OPPOSITE panel only (two-way sync) and activates EVERY text span
  of the claim — selected by `.claim[data-card="card-<id>"]`, not just
  `text-<id>` — so a tail-rescue claim's indigo lead-in (which carries no `id`)
  highlights together with its verdict-colored tail (fix 2026-07-18, friend
  feedback bug #4; the lead-in used to look like part of the following sentence).
  `clearActive()` is scoped to `.claim.active, .card.active` — a bare `.active`
  selector also stripped the active FILTER button (and v2's mode segments),
  silently resetting the filter to All on every sentence click (latent bug
  found + fixed 2026-07-18 during the focus-view work).
- **Focus view (2026-07-18, friend feedback overhaul item 1 — the DEFAULT):**
  the right pane shows ONLY the selected claim's card (list-detail pattern;
  `<body class="detailview">`, CSS hides `#claimList > .card` except `.active`
  with `!important` so it beats the filter's inline display). Empty state =
  "click any sentence" placeholder (never auto-selects claim 1); a prev/next
  nav bar + `←`/`→` arrow keys (typing fields exempt; up/down untouched so the
  text column still scrolls) step through **the cards the active filter
  matches** (`visibleFilterCards`); position indicator "i / N". Selecting a
  claim scrolls the detail pane to top (not center). In focus view, ✓-checking
  the shown card under a non-matching filter auto-advances to the next matching
  card — check the last one and the empty state returns (review-done flow).
  The omitted section hides in focus view. Toggle "show all cards" in the cards
  column header restores the classic scrolling list; preference persisted as
  `localStorage ptui:listview` (shared v1/v2, UI preference never run state).
  `updateDetailPane()` keeps nav/empty state in sync; called from `brush`,
  `reapplyActiveFilter`, and view toggles.
- **Color budget (2026-07-18, friend feedback overhaul item 2):** hue is
  reserved for verdict states — teal/green = supported (+ the owner-ruled teal
  ⛑ rescue chip), red = unsupported, amber = partial / NOT-PROVEN-AS-WRITTEN /
  omitted / borderline / covering-gap lines, indigo = own + scoped-cite. Every
  other chip and note (confidence, second-opinion, deep-check, arbiter, date,
  kind, over-cite, secondhand, disagree, changed, owner) is a neutral grey
  **ghost (outline) chip**; the review bar / triage buttons / source-open
  buttons / argument panel chrome are neutral slate (`#334155` fills); header
  counters keep verdict hues only; the coverage-ratio bar segments use the SAME
  hex values as the cards (one hue per meaning). Link blue and the warnbanner's
  amber (a real run warning) are the deliberate exceptions.
- **Selected state (2026-07-18, friend feedback overhaul item 3):** the yellow
  `#facc15` ring is gone; selection = a Material-style state layer — inset
  `box-shadow rgba(15,23,42,.16)` darkening the claim span's OWN background
  (`.05` on cards + a grey border). In focus view the lone visible card drops
  the darkening (it's the only card on screen).
- **Slim review strip (2026-07-18, friend feedback round 2):** the review bar
  (export buttons) and the verdict-mix ratio bar collapse behind one small
  "▸ export review" toggle (renamed from "review tools" 2026-07-18, owner —
  clearer job description; collapsed by default; persisted
  `localStorage ptui:revtools`). Always visible instead: a 4px two-color
  **progress line** (`#chkFill` slate on grey — neutral, review progress is
  not a verdict) + a "N left to check" / "✓ all N checked" label, updated by
  the shared `REVIEW_JS updateReviewBar()` on every ✓ toggle. The filter chips
  stay visible — in focus view they define the prev/next set. A **"↦ last
  checked"** button on the strip (hidden until something is checked) resumes a
  review: jumps to the most recently ✓-checked claim (`REVIEW_KEY:last` in
  localStorage, recorded by the check handler; stale/missing record falls back
  to the last checked card in document order). Both panels navigate — text
  scrolls to the claim; focus view shows its card, list view centers it.
- **Card explainers (owner walkthrough items 10/11, 2026-07-07):** a supported
  multi-citation card with non-supporting co-cited rows states the OR semantics
  ("supported via X; the others did not independently support it"); per-row chips
  carry explanatory tooltips; a null-sentence row renders "No relevant passage
  found in this source" **with open/side-window buttons anyway** (2026-07-19,
  friend feedback — PDF opens at the start, text un-highlighted; the "couldn't
  locate the sentence" warning is suppressed when no sentence was passed), and
  a card with NO evidence rows at all still lists each cited source with open
  buttons (v1: appended rows; v2: an "open the cited sources" expander); EVERY evidence row gets a "Context — what the judge read"
  expander (the judged window); >700-char evidence "sentences" clamp behind a
  show-all toggle; `component_check` (§6.4 no-flip) renders which parts WERE
  individually found + per-part quotes; `partial_support.component_hunt` renders
  where else the missing part may be covered.
- **Review triage** — per card: `wrong_source` / `rewrite` / `more_support`
  ("find proof / rewrite", 2026-07-18 owner — evidence-first escalation: hunt
  the sources' full text for verbatim proof of the unproven part, rewrite only
  if none exists; the natural mark for amber NOT-PROVEN cards) /
  `verdict_wrong` / `needs_citation` (2026-07-11, owner r3 t6 — pairs with the
  louder `📎 citation needed?` chip on own+fact cards) + note;
  persisted in localStorage under `ptreview:<RUN_ID>` (`RUN_ID =
  sha1(text_file|timestamp)[:12]` — a re-run is a fresh review). Exports: **Copy
  repair brief** (markdown for any LLM), **Download review.json** (contract in
  `docs/REPAIR_PLAYBOOK.md`, consumed by `/apply-review` and
  `find_replacement_sources.py`), **Copy research request** (wrong_source
  claims only; renamed from "Copy Claude Science request" 2026-07-06 — the
  brief works with any deep-research tool).
- **"✓ checked" coverage tag** (2026-07-10, improvement-loop v1): each card has a
  green `✓ checked` toggle (`review[id].checked`, same localStorage record) that
  records THAT the human reviewed the card — checked with no repair marks =
  human-confirmed good. Filter chips `✓ Checked (n)` / `Unchecked (n)`
  (JS-counted via an `hchecked` card class), review bar shows `n/total checked`.
  Toggling ✓ checked re-runs the active filter (`refilterAfterToggle`), so a card
  that no longer matches (e.g. just-checked under the `Unchecked` filter) leaves
  the list immediately with a ~200ms fade — the shared filter apply logic lives
  in `reapplyActiveFilter` in both viewers (fix 2026-07-18, friend feedback bug #5).
  `review.json` export gains a top-level `checked: [claim ids]` array (additive —
  existing consumers ignore it; the loop harness merges it as the owner column,
  see `docs/IMPROVEMENT_LOOP_V1.md`).
- **Header honesty:** `source_file_missing` unsupporteds are counted separately as
  "unverifiable"; a scope note says supported = "the cited document contains the
  statement", not that the source is strong or the claim true.
- **Omitted caps:** 15 shown, up to 200 embedded behind "show more", the rest only
  in `analysis.json` (real runs can have ~30k omitted claims → multi-MB viewers).
- **Assessment panel** (when `--argument-map` ran): full-width collapsible
  "Argument structure" panel with three columns — cruxes, evidence independence,
  argument-map edges (§8).

### 7.1 Viewer v2 (`viewer_v2.py`, 2026-07-15 — comparison period)

The owner-approved card redesign (`docs/VIEWER_V2_DESIGN.md`). Every run now
ALSO writes **`viewer_v2.html`** next to the untouched v1 `viewer.html`
(both emission sites in `verify_my_text.py`; standalone regen:
`python3 -m modules.papertrail.viewer_v2 <run_dir>` — no LLM, no network).
Both viewers derive the same `RUN_ID` and reuse `REVIEW_JS`, so review-triage
marks are shared. All differences are display-only; `analysis.json` and every
verdict field are untouched:

- **Three display states**: a supported claim whose NOT-PROVEN-AS-WRITTEN flag
  SURVIVES the arbiter is a full **amber card** (`_display_class` — card color,
  left-panel highlight, its own "Not proven as written" filter; the "Supported"
  filter counts fully-proven green cards only). The gap line naming the unproven
  part and the arbiter's suggested rewrite are always visible (reverses the
  2026-07-14 one-click-away ruling — justified by the default-on arbiter
  auto-clearing retrieval-miss ambers).
- **Proof rows are the main evidence display** on supported/amber cards: the
  covering-set per-part rows (part → proving sentence → source links), grouped
  adjacent same-sentence; an arbiter-resolved gap renders as a teal ⛑ proof row
  quoting the arbiter-fetched verbatim sentences. Cards without a covering
  payload fall back to the v1 per-source rows.
- **Unsupported cards**: reason always visible; "◐ Partly proven despite the
  verdict" is its own named expander WITH source links (v2 fix — v1 quoted the
  sentences link-less); non-supporting evidence rows, arbiter reading, fix
  suggestions etc. sit behind a "▸ more checks — <named contents>" expander.
- **Triage row always visible** (slim) on every card; no per-card
  "details & review" button. **Expert view** = show internals chips
  (`.xchips`) + open every expander; shares the `ptui:expert` preference.
- Own-fact cards keep the loud 📎 nudge + now an always-visible cite-note.

**Round 2 (owner comments, same day — see docs/VIEWER_V2_DESIGN.md for the
11-point record):** proof rows collapsed behind their own one-click expander
("✓ show proof sentences (N)"); segmented `[simple view | expert view]`
control that shows the CURRENT state; full tooltip sweep; legend rebuilt for
cold visitors (intro + how-it-works + "Expert corner" explaining judge and
arbiter + a "This run" line naming the judge/arbiter/second-opinion models
and run date); header counts use the filter vocabulary (green count +
"not proven as written" as its own term); the source-coverage panel and an
empty unused-points section are expert-view-only; unsupported cards get a
"▸ what was checked" expander directly under the reason box; the `--fix-claim`
CLI command box is removed from v2; the text↔card sync click is narrowed to
the card header + claim text.

**Rounds 3–4 (same day):** legend jargon spelled out; the three export
buttons get tooltips + a dedicated legend block (no `/apply-review` mention
in reader-facing text); page-chrome tooltip sweep; the stacked ratio bar
uses `_coverage_ratio_bar_v2` (amber segment, green-only "supported");
collapsing the text panel hands its width to the cards. One SHARED-helper
change (affects v1 output too): `viewer._coverage_bars` drops the
"N / M source claims in evidence" count when `total_source_claims == 0`
(decomposition off — the default), instead of rendering "0 / 0".

v1 (`viewer.py`) stays the shipped default until the owner retires it after
the side-by-side comparison; `deep_check.py` still regenerates v1 only.
Offline tests: `tests/test_viewer_v2.py`. Owner sign-off on the v2 format:
2026-07-15 ("it is great now"); v2 pages generated for pp_arb_validation
and eggs_run.

## 8. Assessment layer & other extras

All read a finished analysis, are failure-isolated (try/except per pass), and never
touch verdicts.

### 8.1 `argument_map.py` (Stream A)
`build_map` — nodes = the author's text claims; `infer_edges` = ONE LLM pass over
the numbered claim list (`pt_argmap_edges_prompt.txt`) → support/attack/elaborates
edges; `classify_roles` from topology (thesis/premise/sub/aside); `to_argdown`.
Writes `argument_map.json` + `.argdown`. Edge cache keyed sha1(nodes + model +
prompt SHA) in the run dir; a failed inference returns `None` and is **never
cached**. LLM output caps scale with input (`max(2048, 128×n_claims)` for edges,
`64×n_pairs` for the variants confirm) — a fixed 2048 cap truncated paper1's
81-claim edge list into a silent 0-edge map (found + fixed in the A5 audit,
2026-07-06). Also holds `find_variants` (lexical → cosine → LLM tiers; only STRONG
pairs group; negation-guarded) and `diff_maps` (pure argmap diff), not wired to the CLI.
**Validation status (docs/ASSESSMENT_AUDIT.md, 2026-07-06): argmap edges + crux
ranking FAILED their paper1 hand audit** (systematic forward-in-document-order
direction bias, heading-node noise, 20 inflated theses → the self-declared crux
t38 missed); parked prompt/node fixes P1–P3 there. Independence + dedup passed.

### 8.2 `evidence_independence.py` (slot A1)
Detects correlated cited sources: shared authors (S2 author-ID intersection =
strong; local surname match = weak), direct citation (one's reference list contains
the other), bibliographic coupling (shared-refs ratio), content overlap (from
`dedup.json` if present). **Strong/weak policy:** only STRONG pairs enter clustering
and effective-independent-source counts; weak pairs are surfaced as questions.
Missing metadata = unknown, never a flag. S2 lookups disk-cached
(`s2lookup_*.json`/`s2refs_*.json`); transient failures never cached. Writes
`independence.json`; feeds crux v2 fragility; also a standalone CLI
(`python3 -m modules.papertrail.evidence_independence <run_dir>`).

### 8.3 `crux.py` (Stream A)
`find_cruxes` ranks non-thesis nodes by structural leverage (1/dist to thesis +
contested bonus + degree) × fragility from the run's own verdicts (unsupported=1.0,
own-fact=0.85, partial/2nd-opinion-flag=0.7, single-effective-cluster=0.6, …; zero
new calls). Optional LLM confirm pass (not wired by the CLI). Writes `crux.json`.

### 8.4 `dedup.py` (slot A4, standalone)
Cross-source duplicate claims over the cached decompositions: cosine blocking
(≥0.95, top-3/claim) → near-verbatim lexical or batched LLM confirm → union-find
over STRONG pairs only. Negation- and numeric-mismatch guarded (a "first-order" vs
"second-order" pair scored 0.983 cosine). CLI: `python3 -m modules.papertrail.dedup
<run_dir>`; the independence pass reads `dedup.json` if it exists — the main CLI
does not generate it.

### 8.5 `provenance_export.py` (slot B4, `--provenance-export`)
Pure reshape of `analysis.json` into nanopub-isomorphic `provenance.json`: one
record per verdict with the assertion / provenance / publication_info split,
Web-Annotation-style evidence targets (exact quote + page), `content_sha1` per
source, JSON-LD-ish `@context`. Spec: `docs/PROVENANCE_FORMAT.md`. No LLM.

### 8.6 `origin_trace.py` + `origin_viewer.py` (Stream B — **design stub, not CLI-wired**)
Claim genealogy: walk cited → relay → origin via per-hop attribution judgments
(`pt_origin_attribution_prompt.txt`, cached) + bibliography resolution + S2 lookup;
depth 2, confidence-gated, opt-in claim ids only. `origin_viewer` renders the chains
(sibling of viewer.py, same server-free discipline, own simple `origin-review.json`
export). Document as interface-complete, not production.

### 8.7 `claim_fixer.py` (`--fix-claim`)
Rewrites ONE unsupported claim from a finished run's caches: fresh gated evidence
extraction from the cited sources → one rewrite call
(`pt_rewrite_claim_prompt.txt`) → majority-vote re-judgment of the rewrite. Writes
`fix_suggestion {text, changes, verified_supported, verify_reason, passages}` back
into `analysis.json`; the viewer renders it with a ✓/⚠ chip and a copy button.
Driven by `/rewrite-unsupported-claim`; propose-only.

## 9. Source acquisition & import tooling

All no-LLM (one optional $0 gate in §10.3). Layered wrong-document defense
everywhere: `content_check` at download AND ingest time, thin/no-text flags, report
mismatch section — a wrong source silently manufactures false "supported" verdicts.

### 9.1 `import_claude_research.py` (+ `claude_research_importer.py`)
Pandoc `[@key]` markdown + `.bib` → project dir. Citations are stripped and
re-inserted as `[[key]]` **at sentence end** (a mid-sentence marker would truncate
the claim), with abbreviation/decimal/initial-aware sentence-boundary detection.
Dependency-free brace-counting BibTeX parser. Bib resolution order: `--bib` →
the frontmatter `bibliography:` entry → a sibling `<input-stem>.bib` next to
the input (added 2026-07-20, judge F-8; same default the wizard always had);
`--merge-into` uses the same order. Extension seams:
`PandocCitationRecognizer` (add recognizers for other syntaxes) and
`load_bibliography` (only `.bib` today). Bare `@key` narrative citations are
deliberately ignored. The export's frontmatter `title:` is preserved as a
frontmatter block atop `my_text.md` (2026-07-11) — the decomposer skips it
(§5.1); the viewer names review exports after it. An export with no title
falls back to the input file's stem (so `violence_decline.md` still yields
`review_violence_decline_*.json`, never `review_my_text_*`).
`--merge-into <project>`: merge ONLY an export's bibliography into an existing
project — dedupe by DOI → normalized URL → normalized title (≥20-char guard); key
collisions with different works get numeric suffixes; refs lines appended; a
`merges` provenance entry recorded. Re-import is non-destructive to existing refs
mappings.

### 9.2 `download_sources.py` (+ `direct_downloader.py`, `semantic_scholar_api.py`)
Per manifest entry: classify paper-shaped vs web-shaped, then a PDF cascade —
S2 `oa_pdf_url` → direct `.pdf` URL → arXiv → Unpaywall (DOI) → OpenAlex (DOI) →
PMC → doi.org + publisher URL patterns → S2 direct PDF → landing-page fallback
(`citation_pdf_url` meta tag, then `.pdf` anchors, else extracted page text —
which strips nav/figure/chrome tags, boilerplate-classed divs, link-dense blocks
(≥70% anchor text = related-articles/nav dump), and runs
`webtext.drop_boilerplate_lines` for bylines/date stamps/photo credits —
owner walkthrough item 8).
Open-access only; paywalls are reported, never bypassed. Validation:
Content-Type/`%PDF` signature, ≥10 kB, `content_check` (do the first pages mention
the cited title/authors? mismatch → discard and try the next candidate),
`pdf_has_text` (scan detection), thin-text flag (<900 words).
`needs_search` entries (no url/DOI) go through Semantic Scholar title lookup with a
hard confidence gate (title similarity ≥0.90 AND year ±1; no match = safe outcome);
a circuit breaker skips remaining lookups after 2 consecutive search failures.
Politeness: 2 s±random delay, backoff on 429, contact email for
Unpaywall/OpenAlex polite pools — the user's own email, resolved once per
process from `config/unpaywall_email.txt`, then `UNPAYWALL_EMAIL`, then an
interactive ask-once-and-save prompt (TTY only, answer written to the config
file, which is gitignored); with no email available Unpaywall is skipped
(never queried with a fake address) and OpenAlex is queried without `mailto`
(2026-07-19 — no personal default ships in the code). Optional S2 key
(env `SEMANTIC_SCHOLAR_API_KEY` or `config/semantic_scholar_api_key.txt`).
Outputs: files in `sources/`, refs extensions fixed, `download_report.md` (full
status of every entry: mismatch / missing-with-link / needs-search / thin / ready;
`--report-only` regenerates from disk with no network).

### 9.3 `ingest_downloads.py` (+ `source_ingestor.py`)
Files `inbox/` drops into `sources/`. Match strength order: filename stem == key →
key in filename → DOI found inside the file → title ≈ filename (ratio ≥0.8, ≥3-word
titles) → title inside first pages (≥4 words). Ingests only on exactly ONE match at
the strongest level — ambiguity blocks, never guesses. Replacing an existing source
requires a key/DOI-level match. HTML converts to extracted text. Post-ingest: refs
fixed, report regenerated, advisory `content_check` even on key-named files.
`--copy` for shared folders, `--dry-run` to preview.

### 9.4 `paper_search.py` (Stream B — snowball search backend)
`neighbors(paper_id, direction)` — the shared citation-graph primitive: S2 graph API
with OpenAlex fallback, disk-cached per (id, direction), failures never cached,
returns `[]` rather than fabricating edges. Since 2026-07-07 an EMPTY S2 edge list
also falls through to OpenAlex (S2 knows some papers but has zero parsed
references for them — found live on an AEA journal: S2 had 0, OpenAlex 39; an
empty answer is kept only when both backends agree). `pick_relevant` — SPECTER cosine of
abstracts vs the target, optional single batched LLM gate that only drops explicit
off-target hits. `snowball(target, keywords)` — S2 keyword seed → rank → bounded BFS
over references+citations with provenance paths (`found_via`) and edges. Offline-
tested; a full live run wants an S2 API key (keyless public pool 429-storms).
`snowball_viewer.py` renders results with pursue/skip triage →
`snowball-review.json` (a discovery nudge; fetches/grounds nothing).

### 9.5 `import_paper.py` (+ `paper_importer.py`, `crossref_api.py`) — generic-paper importer
Converts ANY published paper (PDF / `--doi` / `--arxiv` / `--url` / `--title`)
into the input format so its claims can be verified against its own cited
sources. **Database-first, PDF-parsing last** (owner directive 2026-07-07); no
LLM calls; every network callable injectable (`fetchers=`) so tests are fully
offline. Three ladders:

- **A — identify:** explicit id → DOI/arXiv stamp printed on the PDF's first
  pages → page-1 title candidates through `find_paper_by_title`'s ≥0.90
  never-guess gate → **hard stop** (`PaperImportError`). S2 `get_paper` enriches
  with DOI/arXiv/PMC/`oa_pdf_url`. Since 2026-07-17 (import-loop round-1 fix
  F2) the printed-DOI rung is hardened: zero-width/invisible chars are
  stripped first (a U+200B-truncated supplementary-URL DOI hijacked a PNAS
  manuscript's identity), ALL DOI-shaped candidates are collected and ranked
  (doi.org-printed first, `lookup/suppl` last), tried until one RESOLVES in
  S2; an unresolvable printed DOI falls through to the title gate, and only
  if that also fails is the best candidate kept (marked "no S2 record") so
  genuinely-unindexed papers still reach the accurate unindexed stop.
- **B — reference list:** `paper_search.neighbors(id, "references")` is the
  canonical SET (rich ids for the downloader); `crossref_api.get_references(doi)`
  supplies the publisher-deposited ORDER (+ trailing-number publisher keys) as a
  numbering witness. The PDF's own reference section is used only as the second
  alignment witness, never as bibliography data. Since 2026-07-17 (import-loop
  round-1 fix F7a) STRUCTURED Crossref deposits missing from the DB list are
  unioned in for AUTHOR-YEAR resolution only (`_crossref_only_refs`: needs
  title + (author+year | DOI); dupe check = title similarity AND
  year-or-author agreement — title alone conflates same-titled classics).
  The union is scoped: numeric alignment keeps the pristine DB-only list —
  near-twin entries with divergent metadata poison its closed-set blob
  matching (live num1 regression, caught and reverted).
- **C — body:** the user's `--pdf`, else the paper's own OA copy via the §9.2
  cascade. Since 2026-07-17 (import-loop round-1 fix F3) every paper file is
  magic-byte-sniffed BEFORE extraction: `%PDF` proceeds, `PK` (zip/docx) or
  other binary = instant actionable stop, plain text stays allowed; title
  candidates containing control characters are dropped before any S2 search
  (a docx passed as --pdf used to burn minutes of S2 429 backoff on binary
  garbage queries). Extraction prefers poppler `pdftotext` over PyPDF2 (PyPDF2 collapsed a
  64-page paper into ONE paragraph — paragraph structure is what the claim
  splitter eats); `trim_body` strips front matter/abstract (`--keep-abstract`),
  references, backmatter, captions, appendices (`--keep-appendix`); `_reflow`
  joins hard-wrapped lines, de-hyphenates, and deglues superscript footnote
  digits fused to a sentence-final period (requires a letter before the period
  and a capitalized next sentence, so real decimals survive).

**Resolution (never-guess everywhere):** author-year citations resolve against an
`AuthorYearIndex` — first-author (surname, year) first, then an ANY-author
fallback (DB records sometimes flip author order), unique match required, accents
+ hyphens folded (`Núñez-Peña` == `NúñezPeña`, a pdftotext line-break artifact).
Numeric `[n]` citations need the paper's own numbering: `align_numeric` requires
**two witnesses** — Crossref deposited order vs a light closed-set match of the
PDF ref-section blobs against the KNOWN DB list (`_match_blob_to_bib`: title-token
overlap + surname + year, unique best with margin). Agreement → aligned; one
witness → aligned ONLY when corroborated (since 2026-07-17, import-loop
round-1 fix F1: single-witness rows need ≥3 two-witness agreements elsewhere
in the same run — `MIN_SINGLE_WITNESS_CORROBORATION` — else the index stays
unmapped as `*-uncorroborated`; an unvalidated Crossref order minted the only
wrong markers of the round-1 audit); disagreement → that index unmapped. The
PDF witness also tries a `pdftotext -layout` extraction of the ref section
(same witness, better-of-two by entry count; heading matcher tolerates the
page number -layout leaves on the heading line) — NOT exempt from the gate:
a round-1 audit showed multi-column -layout blobs mis-assign ~1/3 of rows via
neighbor-column contamination, all caught by the gate. Column-aware parsing
(F1c) is a round-2 proposal. Style is
auto-detected (more RESOLVED hits wins). Any unresolvable citation gets NO marker
and is listed in `import_report.md` — a missing marker becomes an own-split nudge,
a wrong marker manufactures a false verdict. Multi-cite parens are all-or-nothing.
Keys: `<surname><year>` slugs in the `[A-Za-z0-9_-]` charset, `_2` suffixes on
collision; junk/missing author metadata falls back to the first meaningful
title token (`giving2021`, not `ref`/`ref2001_2` — F8, 2026-07-17). Coverage tripwire: <30% of the DB list resolved → loud warning (causes:
superscript/footnote styles, or an OA copy that's an earlier working-paper version
whose citations differ from the published reference list — both observed live).
Outputs: the four standard artifacts (shared `write_artifacts`, extracted from
`run_import` 2026-07-07 so both importers share refs-merge semantics and the
manifest schema — manifest entries now carry optional `arxiv_id`/`pmc_id`/
`s2_paper_id`/`oa_pdf_url` rich ids that `normalize_entry` honors) plus
`import_report.md` (identification evidence, alignment table, unresolved list with
context, stripped sections, next steps). Fallback for docx/superscripts/unindexed
papers: the `/import-paper` prompt-only command (§10.4 family). Live-validated
2026-07-07 on economics (JPE, author-year, 38% — working-paper version mismatch),
psychology (Frontiers, author-year, 44%), political science (PLOS ONE, numeric,
65%, 21/21 two-witness agreement), geoscience (Copernicus ACP, author-year, 57%).

## 10. The review-and-repair loop

1. **Verify** → viewer. Author triages cards, exports `review.json`.
2. **Branch per mark:**
   - `verdict_wrong` → `/apply-review` writes it to `verdict_feedback.json`
     (feedback only, never touches the text; outranks models thereafter).
   - `rewrite` → `/rewrite-unsupported-claim` or `/apply-review` (propose-only,
     author approves, every edit logged to `<run>/changes.md`).
   - `wrong_source` → either `find_replacement_sources.py` (below) or the external
     path: viewer's "Copy research request" → deep research →
     `import_claude_research.py --merge-into` → `download_sources.py`.
3. **Re-cite** the new `[[key]]` (human / `/apply-review`, only after quoting the
   passage that establishes the claim).
4. **Re-run incrementally** into the same `--output-dir` — unchanged claims cost $0;
   the viewer gains the Changed filter + ✎ diff notes. **One repair→verify cycle
   per review** (Goodhart guard, `docs/REPAIR_PLAYBOOK.md`): leftovers go back to
   the author.

### 10.3 `find_replacement_sources.py` (+ `review_paper_finder.py`, Stream D)
For every `wrong_source` claim: snowball search → keep fetchable top-k → mint a key
(`<titleword><year>`) → download OA candidates → register usable ones in the
manifest + refs. Writes `replacements.json` + `replacement_report.md` with a
`suggested_key` per claim. **Propose-only (owner rule):** never edits the text or
picks the citation. `--dry-run` searches without downloading; `--model
claude-code/haiku` adds the $0 relevance gate. `search_fn`/`download_fn` injectable
→ fully offline-tested.

### 10.4 Slash commands (`.claude/commands/`, prompt-only)
- `/apply-review` — applies a `review.json` per the REPAIR_PLAYBOOK contract: minor
  rewrites direct, conceptual ones approved; `[[key]]` swaps only with a verbatim
  quote; `verdict_wrong` → `verdict_feedback.json`; log to `changes.md` before
  re-running; never invent citations/evidence; never weaken a quantitative claim.
- `/download-failed-papers` (Stream F) — web-search OA copies of what the
  auto-downloader missed → `inbox/` → `ingest_downloads.py` → refreshed report.
  Never hand-edits manifest/refs; does not re-run the verifier.
- `/rewrite-unsupported-claim` (Stream F) — drives `--fix-claim` per unsupported
  claim, presents ✓-verified vs ⚠-inconclusive suggestions, applies only on
  approval, one incremental re-verify.

## 11. Caching summary

| Cache | Location | Key |
|---|---|---|
| Source decomposition | `<run>/source_claims/<paper_id>.json` | SHA-256 of file bytes + schema 7; `paper_id` = SHA-1 of *filename* |
| SPECTER sentence/claim vectors | `<run>/embeddings/<pid>.{sents,claims}.npz` | sha1(model + texts), float16 |
| Verdict reuse (incremental) | previous `analysis.json` | normalized text + marker set + same model + unchanged source SHA-1 |
| Own-split tag | on the claim in `analysis.json` | model + prompt SHA |
| Second opinion | on the claim | model |
| Argmap edges | `<run>/argmap_<key>.json` | sha1(nodes + model + prompt SHA) |
| S2 lookups (independence) | `<run>/s2lookup_*.json`, `s2refs_*.json` | query; determinate answers only |
| Citation-graph neighbors | `neighbors__<id>__<dir>.json` in the given cache dir | (paper_id, direction) |
| Origin-trace attribution | `<cache>/origin_attr__<digest>.json` | claim id + paper_id + model + prompt SHA |

Universal rule: **failures are never cached** (transient S2 errors, failed edge
inference, unparseable classifications are retried next run).

## 12. Output artifacts & schemas

**`analysis.json`** (abridged; the full shape is in the code, `matcher.run` +
`main()` assembly):

```jsonc
{
  "text_claims": [{
    "id": "t0", "text": "...", "markers": ["key1"], "paper_ids": ["<sha1(filename)>"],
    "verdict": "supported|unsupported|own",
    "method": "llm|llm_fulltext|combined_fulltext|tail_rescue|component_rescue|none",
    "cosine": 0.83, "reason": "...", "votes": "2-0",
    "evidence": { "paper_id", "source_title", "supported", "sentence", "page", "snippet", "cosine", "reason", "window" },
    "evidences": [ /* all cited sources, same shape */ ],
    "tail_rescue": { "supported", "reach", "lead_in", "tail" },
    "partial_support": { "reason", "votes", "escalated", "component_hunt?" }, // nudge
    "over_citation": { "sources": [...] },                      // nudge
    "component_check": { "found", "missing", "rescued", "evidence" }, // §6.4
    "covering": { "covered": [{ "component", "paper_id", "source_title",
                  "sentence", "page", "snippet" }], "uncovered": ["..."],
                  "common_knowledge": ["..."],  // §6.5 pick-verify: grey, not amber
                  "pick_verified": true },      // §6.5 audit buy-once mark
    "covering_checked": true,                                    // §6.5 buy-once mark
    "proof_state": "full|partial",  // §6.5 badge: derived from covering; absent when no covering parsed
    "alternatives": [...],                                       // for unsupported
    "own_kind": { "kind", "reason", "model", "prompt_sha" },
    "citation_scope": { "scope", "scoped_assertion", "reason", "model", "prompt_sha" }, // §6.7 nudge
    "second_opinion": { "model", "verdict", "agrees", "reason", "votes" },
    "owner_flag": { "author_says", "note", "timestamp" },
    "prev": { "changed": true, "text?", "verdict?" }
  }],
  "omitted": [{ "paper_id", "source_title", "source_claim_id", "text", "evidence", "page", "snippet", "relevance" }],
  "coverage": { "per_source": { "<pid>": { "title", "total_source_claims", "used",
                 "cited_by", "citing_supported", "supported" } },   // last 3 since 2026-07-07:
                 // cited_by = claims citing it; citing_supported = of those, judged supported;
                 // supported = claims THIS source's own evidence backed (the viewer's row label)
                "totals": { "claims", "supported", "unsupported", "own", "omitted" } },
  "metadata": { "text_file", "sources_dir", "output_dir", "model", "timestamp",
                "marker_errors", "processing_time_seconds",
                "source_hashes": { "<filename>": "<sha1>" },
                "llm_usage": { "<model>": { "calls", "prompt_tokens",
                  "completion_tokens", "cost_usd" } },  // ACTUAL spend this run
                  // (2026-07-11, owner ask) — litellm-reported tokens + computed
                  // cost, accumulated in llm_client._record_usage; the pre-run
                  // estimator remains a prediction. claude-code calls not counted ($0).
                "incremental?", "own_split?", "second_opinion?" },
  "sources": [{ "paper_id", "key", "filename", "title", "num_claims" }]
}
```

**`review.json`** — `{run: {text_file, sources_dir, output_dir, project_dir, model,
timestamp}, exported, marks: [slimmed claims + marks[] + note]}` (only
marked/annotated claims; contract in `docs/REPAIR_PLAYBOOK.md`).

Other artifacts: `verdict_feedback.json` (author verdict rulings; list),
`changes.md` (edit ledger), `argument_map.json`/`.argdown`, `independence.json`,
`crux.json`, `provenance.json`, `replacements.json` + `replacement_report.md`,
`download_report.md`, `sources_manifest.json` (§9.1), `analysis_prev.json`.

## 13. Config, API keys, models, cost

1. **Model default:** `config/gemini_config.json` → `claim_validation.model_name`
   (`gemini-2.5-flash-lite` since 2026-07-04 — ≈6× cheaper output than flash, 0-FP
   judge on the paper1 bench). Only the `claim_validation` block is used; the other
   sections are monorepo leftovers. See `docs/MODEL_OPTIONS.md`.
2. **API key resolution** (`LLMClient._resolve_api_key`): `--api-key`
   (file path or raw) → `config/google_api_key.txt` (gemini only) → provider env var
   via litellm (`GEMINI_API_KEY`, `OPENAI_API_KEY`, …). claude-code backend ignores
   all of this (CLI's own login).
3. **Cost estimator** (`cost_estimator.py`, no API): prices parsed from
   `docs/MODEL_OPTIONS.md`'s Option-B table; mirrors the pipeline's call structure
   (decomposition `ceil(words/1200)` per uncached source — always zeroed since
   the 2026-07-16 flag removal (`estimate(..., decompose=False)` is the only
   wiring left); 2 judge calls per
   claim×source pair; 60% assumed fallback fraction — the upper-bound driver; 40%
   tail-rescue fraction). Range = point ×/÷ 2. `CONFIRM_THRESHOLD_USD = 1.0`.
   Also emits free pre-flight warnings (missing files, no-text sources).
   The conditional add-on passes (own-split, partial-check, covering display)
   stay OUT of the headline number — they run only on cited claims that end up
   judged supported, so their size isn't knowable pre-run; each gets a caveat
   line with its upper-bound count, plus (2026-07-12, owner ask) one priced
   ceiling line via `addon_worst_case()` ("worst case if every cited claim is
   judged supported … adds ~$X"; constants `OWN_*`/`PARTIAL_CALLS_WORST`/`COVER_*`).
4. **Prompts** (`config/prompts/`): `pt_extract_claims` (Stage 1),
   `pt_support_judgment` (per-source judge), `pt_combined_judgment` (multi-source /
   fallback / partial-check / second-opinion / fixer — overridden by the Haiku
   rubric under claude-code), `pt_extract_evidence` (fallback extraction),
   `pt_own_claim_class`, `pt_rewrite_claim`, and the assessment prompts
   (`pt_argmap_edges`, `pt_argmap_variants`, `pt_crux_confirm`, `pt_dedup_confirm`,
   `pt_independence_confirm`, `pt_origin_attribution`).
5. First-run source decomposition used to dominate cost (~85% of calls); opt-in
   since 2026-07-10, removed from the CLI 2026-07-16 — every run pays only
   judging. Existing content-hash caches with claims stay valid, model-agnostic.

## 14. Benchmarks, quality gates, tests

### 14.1 The ship gate (owner rule 2026-07-04; coverage layer added 2026-07-10)
**No prompt/matcher/config change ships without `benchmarks/check_all.sh` passing.**
**Gate runs are pinned `--no-arbiter` (2026-07-14, when the arbiter became
default-on):** the gate scores the frozen judge core. The arbiter tier is
additive, has its own validation battery (MODEL_SWAP_PROTOCOL §6a +
`data/arbval_*` runs), and its amber resolution / rescue could legitimately
change what a gate row shows, which the gate would misread. The fresh-run
commands in check_all.sh's comments carry the flag.
The script now has TWO layers (owner decision 2026-07-10 — the 3-paper ground
truth encodes the OLD truth-based standard, so it was demoted to a stability
check and a new-standard layer added):
1. **Verdict stability** — fresh runs of ALL THREE hand-audited papers — paper1
   (35 GT claims), bentonite (6), chimpanzee (12) via `regression_check.py`.
   Proves a change didn't move verdicts.
2. **Coverage gate v2 (the owner evidence standard; triple-confirmed
   2026-07-11)** — `coverage_check.py` scores the covering-set output (§6.5)
   of fresh scratch-dir runs of THREE loop texts against
   `benchmarks/coverage_ground_truth_{essay,bohemia,pots}.json`. Every hard
   row is TRIPLE-confirmed — grader ruling + an independent Fable ruling
   (rounds 1-4 fully Fable-graded 2026-07-11 before access ended; archive in
   `docs/gate_v2/`) + owner review — with disputes demoted to watch by owner
   ruling (docs/GATE_V2_CANDIDATES.md; 12 hard + 7 watch). The eggs and
   round-5 violence texts are EXCLUDED (owner: their content trips Fable's
   safety layer — do not re-add). Supersedes coverage_ground_truth_round1.json
   (retired 2026-07-11). Proves the SHOWN evidence still proves what it should.
Any failure or missing run → exit 1 "do not ship". Both scorers make **zero API
calls** — the cost is producing the fresh `analysis.json` files first (< ~$0.40
total with warm caches; paper1 needs `--full`).
**Frozen gate inputs (owner rule, loop rounds):** the coverage gate's fresh runs
read `data/loop_rounds/round_{1,3,4}/project/` as immutable input fixtures —
never re-run anything INTO a `round_N/` dir once its `table.md` exists; validate
fixes in scratch dirs. Ground-truth JSONs (`benchmarks/*_ground_truth*.json`)
are owner-edit-only. (Lifted 2026-07-18 from the round-4/7 handoffs before
their archival.)

### 14.2 `regression_check.py` + `coverage_check.py` scoring
`regression_check.py`: finds each GT claim by id (text-verified) else by text
(→ `drifted`); compares `expect` to the run verdict. `expect: watch` entries
never fail (gray areas; the scorer notes IMPROVED/changed vs
`at_creation`/`improves_if`). Exit 1 iff any hard expectation fails.
Ground-truth JSONs: `benchmarks/{paper1,bentonite,chimpanzee}_ground_truth.json`.

`coverage_check.py`: three ground-truth kinds. `must_cover` (grader tool-fetch
rows — the proof exists and was quoted): claim must be supported, have a
covering set, and every `anchors` entry (short distinctive substring of the
grader-quoted proof; normalized casefold/whitespace/quote/dash matching) must
appear in the union of covered sentences — catches over-strictness and
retrieval regressions. `must_flag` (grader author-fix rows — a component is
NOT provable from the cited source): claim must be unsupported OR carry an
uncovered component matching a `flag_terms` entry — an un-flagged supported
claim is OVER-CLAIMING, the worst failure for trust. `watch`: tracked, printed,
never fails (e.g. t3's known false-unsupported). The negatives-only worry about
the Fable audits doesn't apply: every tool-fetch row carries positive labels at
the sentence level (the quoted proof), so both failure directions are gated.
Ground truth: `benchmarks/coverage_ground_truth_round1.json`; more files are
added as later rounds/texts get grader-audited.

### 14.3 Live-API tuning benches (pennies)
- `judge_bench.py` — 12 hard-coded judgment cases from the paper1 audit (7 true
  supports + 4 must-stay-unsupported overstatements); variants a–d select
  prompt/model; ~$0.005–0.02/run.
- `extract_bench.py` — does the full-text fallback recover 9 known needle sentences;
  modes whole/chunked/gated/matcher.
- `partial_check_validation.py` — replays `_partial_flags` on a cached run's flagged
  claims against EXPECT_CLEAR/EXPECT_STAY lists; `--dry-run` = $0.
- `compare_runs.py` — no-API diff of two `analysis.json` snapshots (verdict flips,
  evidence changes).

### 14.4 $0 judge-validation benches (claude-code)
- `claude_code_judge_check.py` — re-judges GT claims through the real claude-code
  path + Haiku rubric; exits 1 on any false positive.
- `error_injection_eval.py` — corrupts supported claims deterministically (number
  ×3.7, polarity flips) and checks the judge catches them; the headline metric is
  missed injections; exits 1 on any miss.
- **WiCE policy (owner ruling 2026-07-11, binding):** WiCE benchmark batches are
  scored automatically against their gold labels ONLY — never surfaced to the
  owner for manual review; disagreements with WiCE labels are calibration /
  strictness data, not review items. (Lifted 2026-07-18 from
  OPEN_PROBLEMS_2026-07-11 before its archival.)

### 14.5 Tests
~37 offline `unittest` files (no pytest; LLM/network/subprocess all mocked;
`tests/fixtures/` holds recorded S2 payloads). Run:
`venv/bin/python3 -m unittest discover -s tests` (quiet, no `-v`; delegate to a
cheap subagent per the token-economy rule). Coverage groups: matcher/judging
(partial support, tail rescue, segmentation, garble fallback, confidence),
assessment layer, own-split, second opinion, rerun/caching/concurrency/cost,
review loop + claim fixer + paper finder, import/download/ingest, paper search /
snowball, claude-code backend, provenance, viewer, wizard, regression scorer.

### 14.6 New-paper trust ritual
`docs/NEW_PAPER_AUDIT.md` — before trusting a run on a NEW paper, hand-audit a
fixed-seed sample of 5 supported + 3 unsupported verdicts in the viewer. 7–8/8 →
trust as a review-priority map; ≤6 → check for wrong source files first, then
`--second-opinion`, consider a fuller audit. Disagreements filed via the viewer's
"verdict wrong" → `verdict_feedback.json`.

## 15. Design invariants

- **Viewer is server-free and persistent** — never reintroduce a localhost server or
  embedded PDF.js.
- **Nudge, never veto** — partial-support, over-citation, second opinion, own-split,
  independence, crux, origin-trace are additive flags; no pass flips a verdict.
- **Claim cards render in document order**; verdict grouping is a filter only.
- **Propose-only text edits** — no tool edits the author's prose or picks a citation;
  the author approves every edit; edits are logged to `changes.md`.
- **Never guess on ambiguity** — ingest blocks ambiguous matches; title lookups have
  hard confidence gates; unparseable LLM output leaves a gap rather than a guess.
- **Failures are never cached**; determinate answers are.
- **Strong/weak policy** (dedup, variants, independence): only STRONG pairs enter
  arithmetic; cosine can propose, never merge (negation/numeric flips can score 0.95+).
- **Open access only** — paywalls are reported, never bypassed.
- **Ship gate** — §14.1; runs on a new paper get the mini-audit first.
- Cosine is only a cheap off-topic filter + near-verbatim shortcut; the LLM makes
  every real support decision, and displayed evidence is always real source sentences.

## 16. Gotchas

- `paper_id = sha1(filename)`, not content — renaming a source orphans its caches;
  replacing content under the same name is caught by the content hashes.
- Two hash algorithms coexist: SHA-256 for the decomposition cache, SHA-1 for
  `paper_id_for` and `metadata.source_hashes`.
- Legacy runs without `metadata.model` force a full re-run.
- Claim IDs are positional; reuse matches on text+markers, not ID.
- `--partial-check` is a silent accepted no-op (default on); `--no-partial-check`
  disables.
- claude-code backend ignores temperature/max_output_tokens and any provider
  `--model` passed alongside it; runs from a neutral cwd.
- Rate-limit retries sleep a fixed 65 s — a big first run on a tight quota can look
  hung.
- Bare `@key` (unbracketed) citations are ignored by the importer.
- `data/` is gitignored — run artifacts and reviews are not committed.
- Keyless Semantic Scholar 429-storms; put a key in
  `config/semantic_scholar_api_key.txt`.
- Judge nondeterminism: flash-lite flips borderline verdicts even at temp 0 — hence
  majority-of-3 voting and `watch` entries in the ground truth (ROADMAP §7).

## 17. docs/ index

| Doc | What it is |
|---|---|
| `PAPER1_TUNING_STATE.md` | Verdict-quality handoff: paper1 ground truth, run history, what to check — read before touching prompts/matcher |
| `STREAMC_PARTIAL_FIX.md` | The partial-check 3-round ladder + garble fallback (why it's default-on) |
| `GENERALIZATION_CHECK.md` | 2026-07-04 overfitting check → 3-paper gate |
| `NEW_PAPER_AUDIT.md` | The 8-verdict human mini-audit ritual |
| `MODEL_OPTIONS.md` | Model/price table (the estimator parses it) + local-model options |
| `REPAIR_PLAYBOOK.md` | review.json ↔ fixing-agent contract (Goodhart guards) |
| `REVIEW_LOOP_CHECKLIST.md` | Owner's first-use checklist for the review loop |
| `PROVENANCE_FORMAT.md` | provenance.json spec |
| `ASSESSMENT_DESIGN.md` | Independence + crux v2 design |
| `DEDUP_DESIGN.md` / `ARGMAP_FEASIBILITY.md` | Dedup and argmap-upgrade designs |
| `STREAM_B_NOTES.md` / `STREAM_E_BACKEND.md` | Paper-search and claude-code-backend stream state |
| `HAIKU_VS_GEMINI_JUDGE.md` / `HAIKU_HELDOUT_VALIDATION.md` | Haiku-as-judge comparisons/validation |
| `GEMINI_VS_DEEPSEEK_RUN.md` | DeepSeek comparison (rejected) |
| `HUMAN_VS_TOOL_DIVERGENCE_LAYER3.md` | What a human reader sees that the grounder collapses |
| `PRIOR_ART.md` / `PRIOR_ART_REUSE.md` / `IMPROVEMENTS_FROM_PRIOR_ART.md` | Landscape + adopt/skip decisions |
| `submission/` (SUBMISSION_*, COMPETITION_*, EPISTACK_*, FLF_*) / `CLAUDE_SKILLS_RESEARCH.md` | FLF competition context |
| `TAIL_RESCUE_REVIEW.md` | Owner review guide for the tail-rescue fix |
| `archive/PAPER1_TUNING_HISTORY.md` | Archived per-run analyses (runs 1–9) |
| `claude_science_example/` | Example importer inputs |
