# For reviewers — how the tool works and how to run its tests yourself

The submission states test results. This page explains how the tool reaches
a verdict, what each test actually tests, and gives the commands to re-run
the scoring on your own machine. Test 1 is files to read — no API key, no
setup; Test 2 runs the full pipeline for a few cents with your own key;
Test 3 covers the WiCE benchmarks, which you can re-score offline in
seconds or re-run end to end for about $1.40.

## How the tool works, in plain terms

You give it a text and a folder with the cited sources. Citations in the
text look like `[[smith2020]]` and point at files in that folder.

1. **Split.** The text is cut into claims — each sentence or passage plus
   the sources it cites.
2. **Find candidate evidence.** For each cited claim, the tool finds the
   most similar sentences in the cited source. This is a local similarity
   search on your machine, not an AI service.
3. **Judge.** A small language model reads the claim and the candidate
   sentences and answers: do these sentences prove the claim as written?
   If they don't, the tool does not give up — it re-reads the whole source
   in chunks and takes a majority of three votes, so a claim is not
   penalized just because the first search looked in the wrong place.
4. **Non-AI safety checks.** Two checks are deterministic code, not a
   model's opinion: the specific names in a claim must actually appear in
   the sentences offered as proof (added after a real failure on an
   external benchmark, where a sentence about a team's result was accepted
   as proof about a player the source never names — the record is in
   `docs/SUBJECT_GUARD.md`), and every quote shown in the report is string-matched against
   the source file; a quote that is not literally there is dropped and
   counted as a failure.
5. **Second-model re-check.** A different model re-reads every claim the
   first pass flagged, with a much larger window of the source. If it finds
   proof sentences, they are verified verbatim against the source, and the
   original judge re-reads exactly those sentences; only a unanimous vote
   flips the verdict. The second model itself never decides a verdict — it
   can only point at sentences for the first judge to re-read.
6. **Output.** A machine-readable `analysis.json` and a self-contained HTML
   report. Every verdict in the report shows the sentences it rests on.

The design bias throughout: when the tool errs, it should err toward
"not proven" — a false "this is fine" is the dangerous direction for a
fact-checking tool.

**Names you will meet in the documents.** The "owner" is the author of this
tool — the docs were written as working notes during development and the
word stayed. "Fable 5" and "Opus" are Claude models, "K3" is Kimi K3, and
"Gemini flash-lite" is the tool's default judge — where a document names one
of them, it names which model produced or graded a verdict. A "gate" is the
regression benchmark a change must pass before it ships.

## Test 1 — the 236-claim audit (no key — files to read)

**What it is.** The hard cases from all our test texts — 236 claims — were
each re-read by a stronger model (Claude Fable) under a written grading
rubric (`docs/JUDGING_RUBRIC_FABLE_2026-07-17.md`). To be clear about who
did what: this grading pass was done by the model, not a human. Its
accusations of tool error were then re-checked by a second, independent
model pass instructed to *defend* the tool, and every surviving ruling
rests on a verbatim source quote stored in the row — so each one is
checkable against the source wording by anyone. The human layer sits on
top: the author personally re-ruled every row the model graders disputed
(their verdicts are stored in the same rows as `owner_verdict` and
outrank the model labels), and separately hand-audited the three gate
papers and the gate essays claim by claim (Test 2 below runs against one
of those).

**Where the rows are.** `benchmarks/gold_labels/corpus/*.jsonl` (194
claims) plus the four files starting `paper1_hard`, `bentonite_hard`,
`eggs_priority`, `eggs_supported_audit` next to them (42 claims). Each line
is one claim, with plain-named fields: `pipeline_verdict` (what the tool
said), `fable_verdict` (what the auditing model concluded), `agreement`,
`verbatim_quote` (the source sentence the ruling rests on), and `reasoning`.

**What you will find.** 58 rows where the tool said "unsupported" but the
final label (model audit, corrected by the author's re-rulings) found real
support in the source — the safe error direction, a claim sent back to the
author unnecessarily. In the other direction, four real
false "supported" verdicts survived verification: `newsys_wice_dev1.jsonl`
rows t11 and t16, one planted bug appearing in both `newsys_synth_11`
and `newsys_synth_12` (row t5), and one essay row
(`coverage_gate_run.jsonl` t9) where a rescue step mistakenly accepted the
claim's own text as its proof. The verification of each, quote by quote,
is `docs/VERIFIED_FINDINGS_2026-07-17.md`; the two code bugs behind these
failures (one on the synthetic text, one on the essay) were fixed after
the audit.

## Test 2 — run the whole pipeline (your API key, a few cents)

One hand-audited paper ships complete: text, source (openly licensed,
CC-BY), and the human claim-by-claim ground truth. You can run the full
verification and score it against the human audit:

    python3 -m venv venv && venv/bin/pip install -r requirements.txt
    venv/bin/python3 verify_my_text.py \
        --text examples/chimpanzee_validation/my_text.md \
        --sources examples/chimpanzee_validation/sources \
        --output-dir /tmp/chimp_check \
        --model gemini/gemini-2.5-flash-lite --api-key <your key> \
        --no-arbiter --yes
    venv/bin/python3 benchmarks/regression_check.py \
        --analysis /tmp/chimp_check/analysis.json \
        --ground-truth benchmarks/chimpanzee_ground_truth.json

`regression_check.py` compares every verdict in your fresh run against the
hand audit and prints any difference. (`--no-arbiter` matches the
configuration the ground truth was audited under.) This same script, run
over three papers from different fields plus three essays, is the
regression gate: no change to the tool's prompts or matching logic ships
without it passing. The ground-truth files are readable without running
anything: `benchmarks/*_ground_truth.json` and
`benchmarks/coverage_ground_truth_*.json`.

The larger bentonite gate paper is also in `examples/`, but two of its
sources are subscription articles whose extracted text we may not
redistribute (DOIs 10.1007/s10450-020-00263-y and 10.1007/s10967-024-09627-y)
— fetch those two yourself to reproduce it fully.

## Test 3 — WiCE: the development benchmark and the held-out test

WiCE is a public research dataset of Wikipedia claims checked by human
annotators (Kamoi et al., EMNLP 2023). We used it twice, and the
distinction matters:

**Development benchmark (159 claims).** These ran during development; one
failure found there led to a permanent code fix, and the published score
(80% agreement with the second-model re-check, 52% without, zero false
"supported" on its 35 refuted claims) was measured after that fix. We
audited exactly which rows influenced code changes — 12 of the 159 — and
marked them (`benchmarks/wice_anchor/README.md` has the list with evidence
pointers). Run outputs, labels and scorer: `benchmarks/wice_runs/` +
`benchmarks/wice_bench.py`.

**Held-out test (512 claims, pre-registered).** Because the development
rows influenced fixes, we froze a test plan in the repository BEFORE
running anything (`docs/WICE_HELDOUT_PREREG_2026-07-19.md` — the commit
timestamp proves the order): the entire WiCE test split (358 claims) plus
every unused claim WiCE labels as refuted (154 more). Results, published
as they came out (`docs/WICE_HELDOUT_RESULTS_2026-07-19.md`): 37% strict /
77% with the re-check on the test split, and on all 186 refuted claims
3 false "supported" at the strict layer (1.6%), 6 after the re-check
(3.2%). Per-batch outputs: `benchmarks/wice_heldout/`.

**Re-score everything yourself (no API key, seconds):**

    for d in benchmarks/wice_heldout/*_b*/; do
      venv/bin/python3 benchmarks/wice_bench.py score \
        --analysis "$d/analysis.json" --ground-truth "$d/wice_ground_truth.json"
    done

(the same loop works over `benchmarks/wice_runs/*/` for the development
batches — the false-support alarm fires visibly on the stored pre-fix
`train2_preguard` run).

**Or run the whole held-out benchmark yourself (your API key, ~$1.40,
about an hour if you run a few batches in parallel — that is what our run
took; the script as shipped runs them one after another, which is closer
to 2–3 hours):** `benchmarks/run_wice_heldout.sh` does everything — converts the WiCE
dataset (fetch its three jsonl files from github.com/ryokamoi/wice first;
we do not redistribute them), runs the full pipeline batch by batch,
scores each batch with the unchanged scorer, and prints totals to compare
against our published table. The script header lists the prerequisites.
The free Claude Code path (`--backend claude-code`) technically works here
too, but is not practical at this size: measured at ~34 minutes per
8-claim batch-equivalent with Haiku, the 512 claims mean roughly a day and
a half of continuous running, and Sonnet is a multiple of that — your
Claude plan's usage limits will likely interrupt long before the end. It
also uses different judge models, so the numbers are not comparable to the
published run (Gemini judge + DeepSeek re-check). Use the free path for
Test 2's single paper; use an API key here.

Two properties of WiCE to know when reading the numbers: the sources are
raw web captures — frequently stats-site pages where the fact lives in a
table, and in about a fifth of our rows the capture is mostly non-prose
scaffolding; and the labels themselves are imperfect — on the rows we
examined most closely, two frontier models and the author all found WiCE's
"supported" more generous than this tool's standard (details and the
excluded non-English rows: `benchmarks/wice_anchor/README.md`,
`wice_bench.py`).

## Other tests in the repo
- **Corruption test.** Claims the tool correctly marks supported were
  deliberately corrupted (a negated mechanism, a changed number) to see
  whether it catches the change: `benchmarks/gold_labels/mutation_*`.
- **Grader cross-check.** The audit rows were independently re-graded by a
  second model, Claude Opus (`benchmarks/gold_labels/opus_pass/`).
- **Against existing checkers.** Six judges side by side on six hand-ruled
  claims — ours, MiniCheck, SemanticCite's fine-tuned Qwen3 checker, and
  three general models: `docs/PRINTING_SIX_JUDGE_TABLE.md`. The same two
  open-weight checkers also ran as comparison columns on 31 more
  human-reviewed claims across the improvement-loop rounds
  (`docs/loop_rounds/round_2` through `round_6`, one table per round).
  The survey of prior tools is `docs/PRIOR_ART.md`.

## About the development notes

`docs/` contains internal working notes written during development, many of
them AI-assisted session records. They are the audit trail, not the
evidence: the evidence is the data and code in `benchmarks/`, which
everything above runs on.

## What you will not find here

- The downloaded source papers and full run outputs of every experiment are
  not in the repository (copyright; some are large). The graded rows quote
  the sentences that decisions rested on, so the grading is checkable
  without them.
- API keys and paid-run configuration are excluded.
- Re-running the pipeline end to end needs an API key and the sources; the
  scoring programs (`wice_bench.py score`, `regression_check.py`) make no
  API calls and run on stored data.
