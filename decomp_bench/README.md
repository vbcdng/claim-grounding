# decomp_bench — claim-decomposition benchmark harness

Compares how well tools split a document into proper claims. Mostly scripts,
$0; LLM only ever writes converters (contract + `validate.py` = the checker)
or fills the L column of a review file. Design: `docs/DECOMP_BENCH_TODO.md`.

## Canonical JSONL schema (one claim per line)

```json
{"claim_id": "str, unique within file",
 "doc_id":   "str, source document key",
 "corpus":   "str, e.g. paper1",
 "tool":     "str, e.g. papertrail-flashlite-prejunk",
 "tool_version": "str, git sha or 'unknown'",
 "claim":    "str, the claim text",
 "evidence": ["source sentences the claim came from"],
 "evidence_pages": [1],
 "order":    0,
 "auto_flags": ["junk", "cite-header", "ref-fragment"],
 "human_ok": null,
 "llm_ok":   null,
 "llm_reason": "",
 "note":     ""}
```

`order` = index of the first evidence sentence in the document (-1 = unknown,
sorts last). `human_ok`/`llm_ok`: `"y"` good claim, `"n"` bad, `null`
unreviewed. `auto_flags` can only say *definitely bad*, never good.
Sidecar `docs_<corpus>__<tool>.json` holds per-document stats
(sentences, chars, pages) for the fragmentation metric.

## Scripts

| script | does |
|---|---|
| `convert_papertrail.py` | our `source_claims/` cache (schema 3–8) → canonical JSONL + sidecar, auto-flags filled |
| `convert_evidence.py` | **evidence mode**: a run's `analysis.json` → one row per (claim × source) SUPPORTING SENTENCE, `[verdict/method] claim => sentence` display, auto-flags on the sentence — for eyeballing bullshit evidence |
| `validate.py` | PASS/FAIL a JSONL against the schema — the checker any LLM-written converter must pass |
| `make_review.py` | JSONL → `review_*_ALL.txt` + `review_*_SAMPLE.txt` (fixed seed, stratified) + `claims_*.txt`; prints meld commands |
| `merge_review.py` | edited review file → writes H/L/note back into the JSONL (backup kept) |
| `metrics.py` | dataset stats: claims, junk-rate, fragmentation /1k words, human/LLM agreement + Cohen's kappa |

## Review line format (both ALL and SAMPLE — same format)

```
id0007 | H:[ ] | L:[ ] | A:[junk] | <claim text> | note:
```

Fill `H:[y]` or `H:[n]` (LLM fills `L:[..]`), free text after `note:`.
Only id, H, L and note are read back — the claim is an echo.

## Typical flow

```bash
python3 decomp_bench/convert_papertrail.py data/paper1_haiku/source_claims \
    --corpus paper1 --tool papertrail-flashlite-prejunk --tool-version unknown
python3 decomp_bench/validate.py decomp_bench/runs/paper1__papertrail-flashlite-prejunk.jsonl
python3 decomp_bench/make_review.py decomp_bench/runs/paper1__papertrail-flashlite-prejunk.jsonl
# ... human edits review_*_SAMPLE.txt (and eyeballs _ALL.txt) ...
python3 decomp_bench/merge_review.py decomp_bench/runs/review_paper1__papertrail-flashlite-prejunk_SAMPLE.txt
python3 decomp_bench/metrics.py decomp_bench/runs/paper1__papertrail-flashlite-prejunk.jsonl
```
