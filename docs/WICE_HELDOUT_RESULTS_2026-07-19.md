# Held-out WiCE evaluation — results (run 2026-07-19)

Pre-registered plan: `docs/WICE_HELDOUT_PREREG_2026-07-19.md` (commit fa79fb4,
frozen before any run). Execution guide: `docs/WICE_HELDOUT_RUN_GUIDE_2026-07-19.md`.
Published as they came out, per the prereg. **These rows are now consumed for
this tool version — no prompt/matcher/config change may ever be justified by
any row below** (freeze rule, prereg §4).

- **Tool commit:** `94c0d65` (master; only change beyond the prereg commit is
  the permitted converter extension — verified `git log fa79fb4..94c0d65` is
  otherwise docs-only). Scorer (`wice_bench.py score`) byte-identical to fa79fb4.
- **Config:** shipped defaults — `gemini/gemini-2.5-flash-lite` judge, arbiter
  ON (`deepseek/deepseek-v4-flash`), partial-check / citation-scope / own-split
  on, `--full` fresh runs, no verdict reuse.
- **Scored artifacts:** `benchmarks/wice_heldout/` (per-batch ground truth +
  scrubbed analysis.json + score.txt, mirroring `benchmarks/wice_runs/`;
  sources not redistributed — copyright).

## 1. Core set: WiCE `test` split, all 358 rows

14 batches (`test_b01..test_b14`), 358 emitted + 0 excluded = 358 ✓
(labels: 111 supported / 215 partially_supported / 32 not_supported).

| layer | agreement |
|---|---|
| strict (verdict-level 3-way) | **132/358 (37%)** |
| arbiter-adjudicated | **275/358 (77%)** |

False-supports on the test split's 32 refuted rows: **base 2, adjudicated 2**
(`bartsimpson`/test02384 in b02, `fcbayernmunichjuniorteam`/test02095 in b13).

Strict confusion (wice → tool):

| wice \ tool | not_supported | partially_supported | supported |
|---|---|---|---|
| not_supported (32) | 28 | 2 | 2 |
| partially_supported (215) | **140** | 44 | 31 |
| supported (111) | 30 | 21 | 60 |

Adjudicated confusion (wice → adjudicated):

| wice \ adj | not_supported | partially_supported | supported |
|---|---|---|---|
| not_supported (32) | 24 | 6 | 2 |
| partially_supported (215) | 12 | **166** | 37 |
| supported (111) | 2 | 24 | 85 |

Reading (direction only, no fixes derived): the strict layer is far stricter
than WiCE — the dominant cell is partially_supported→not_supported (140), i.e.
the tool refuses claims WiCE part-credits. The adjudicated layer recovers most
of that (166 land on partial) and errs almost never in the dangerous direction
on this split (supported→not_supported: 2). Strict 37% is lower than the
dev-split batches used during development (~50–65%); the adjudicated 77% is in
line with them. Consistent with the label-noise note (§6): WiCE credits
generously relative to our standard.

## 2. Refuted stress set: fresh `train` not_supported rows

6 batches (`refuted_b01..refuted_b06`). Pool: 167 train not_supported − 11
already-used (160 used wice_ids checked) = 156; emitted 154 + excluded 2 = 156 ✓.

**THE headline safety line — false-supports on 154 fresh refuted rows:**

| layer | false-supports | rate |
|---|---|---|
| base verdict | **1/154** | 0.6% |
| arbiter-adjudicated | **4/154** | 2.6% |

Agreement: strict **150/154 (97%)**; adjudicated 102/154 (66%) — adjudication
*hurts* on refuted rows: the arbiter's verified-quote promotion moved 48 rows
to partial and 3 extra rows to supported. The four adjudicated false-supports:
`democraticleftalliance`/train10375 (b01, also the one base false-support),
`deeqsulaimanyusuf`/train32073 (b03), `cityofdavid`/train43095 (b04),
`michellewilliamsactress`/train02851 (b05).

Combined across both sets: 186 refuted rows → base **3/186 (1.6%)**,
adjudicated **6/186 (3.2%)**.

## 3. Per-batch table

| batch | n | strict | adjudicated | false-supports (base/adj) |
|---|---|---|---|---|
| test_b01 | 26 | 6 | 19 | 0/0 |
| test_b02 | 26 | 5 | 19 | 1/1 |
| test_b03 | 26 | 9 | 19 | 0/0 |
| test_b04 | 26 | 12 | 20 | 0/0 |
| test_b05 | 26 | 11 | 18 | 0/0 |
| test_b06 | 26 | 12 | 21 | 0/0 |
| test_b07 | 26 | 15 | 20 | 0/0 |
| test_b08 | 26 | 12 | 23 | 0/0 |
| test_b09 | 26 | 9 | 21 | 0/0 |
| test_b10 | 26 | 9 | 19 | 0/0 |
| test_b11 | 26 | 12 | 23 | 0/0 |
| test_b12 | 26 | 7 | 19 | 0/0 |
| test_b13 | 26 | 7 | 18 | 1/1 |
| test_b14 | 20 | 6 | 16 | 0/0 |
| refuted_b01 | 26 | 24 | 14 | 1/1 |
| refuted_b02 | 26 | 26 | 17 | 0/0 |
| refuted_b03 | 26 | 25 | 18 | 0/1 |
| refuted_b04 | 26 | 26 | 16 | 0/1 |
| refuted_b05 | 26 | 25 | 23 | 0/1 |
| refuted_b06 | 24 | 24 | 14 | 0/0 |

**Exclusions (complete list, `benchmarks/wice_heldout/exclusions.json`):**

| wice_id | set | reason |
|---|---|---|
| train34557 | refuted | non-English source page (French stopwords: 242 vs en 23 of 1209 words) |
| train07644 | refuted | non-English source page (Dutch stopwords: 383 vs en 49 of 1438 words) |

Test split: zero exclusions. No unconvertible rows in either set.

## 4. Cost and runtime

- Gemini (`gemini-2.5-flash-lite`): **$0.9178** (7,326 calls)
- DeepSeek (`deepseek-v4-flash`, arbiter): **$0.4645** (519 calls)
- Total: **$1.38** (prereg estimate $0.7–1.2, guide ~$1.30 — slightly over,
  arbiter volume; no budget cap per owner ruling 2026-07-19)
- Runtime: ~51 min wall clock (3 parallel lanes, 14:20–15:11), no failures,
  no missing rows in any batch; only benign truncated-output retries.

## 5. Deviations

None from the prereg. Implementation details recorded for transparency:

- The 2026-07-18 language-probe script was not preserved; it was re-implemented
  per its documented approach (foreign stopword sets + non-Latin script count)
  and validated before use: catches all 4 known 7/18 non-English rows, 0 false
  positives on 357 already-consumed sources. Script threshold 15% calibrated on
  the 7/18 keep/exclude decisions (starbucks 20% Cyrillic excluded,
  mostly-English Myanmar page at 13% Burmese kept).
- Heldout conversion applies NO domain blocklist (prereg permits exactly two
  exclusion rules) — unlike the dev-era batches, refusal-prone-domain rows are
  included here. No refusals were observed (Gemini/DeepSeek judges).
- `--references` not passed explicitly; the pipeline auto-detects the sibling
  `my_text.md.refs.txt` (verified before running).

## 6. Label-noise context note (publish WITH these numbers)

Owner must approve this wording before anything goes external (prereg §5):

> One thing to know when reading numbers against WiCE. On the 159 WiCE claims
> we analyzed deeply during development, the labels themselves are not always
> right: two frontier models both disputed 12 of the 117 reviewed labels
> (about 8%), and when I hand-checked 9 of those disputed rows myself, the
> WiCE label was wrong in 6 and right in 3. I also disagreed with the frontier
> models themselves: even when both models agreed with each other, I overruled
> them 4 times out of 26 rows I checked (15%) — so neither the dataset nor the
> strongest models are a perfect standard, including mine. About a fifth of
> WiCE source pages are mostly table scaffolding rather than prose, and 5 rows
> had non-English sources (excluded). Direction of the label noise: in the
> disputes we examined, WiCE credits claims more generously than our standard,
> almost never the reverse. For calibration: a frontier model reading the
> sources generously agrees with WiCE labels about 90% of the time (binary) —
> so ~90%, not 100%, is the realistic ceiling for any checker scored against
> WiCE.

## 7. Freeze

All 512 judged rows (358 test + 154 refuted) are consumed for tool version
`94c0d65`. Re-evaluating a future version on them = a new versioned report.
Deriving any fix from any row above burns the set (replacement would have to
come from the remaining unused train rows).
