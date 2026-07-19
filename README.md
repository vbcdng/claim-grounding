# Claim grounding

Verify writing against its cited sources, sentence by sentence, and produce a
self-contained HTML review viewer. Provider-agnostic LLM backend (litellm); local
SPECTER embeddings for candidate retrieval; disk-cached source decomposition.

**Checking the accuracy claims:** `FOR_REVIEWERS.md` explains without
jargon how the tool decides, what each benchmark tests, and how to re-run
the scoring yourself — the benchmark run outputs and human labels are
checked into `benchmarks/`, so most of it needs no API key.

## Setup
    python3 -m venv venv
    venv/bin/pip install -r requirements.txt

(`requirements.txt` pins the CPU-only torch wheel on Linux — the default PyPI
torch is a multi-GB CUDA build this tool doesn't need. Python ≥ 3.10. Optional:
install `poppler-utils` for `pdftotext`, which rescues PDFs that other
extractors garble; and `python -m nltk.downloader punkt` for slightly better
sentence splitting — both degrade gracefully if absent.)

Put a Google API key in `config/google_api_key.txt` (gitignored), or pass
`--api-key` / use another provider via `--model` (any litellm provider string
works, e.g. `openrouter/google/gemini-2.5-flash-lite` with `OPENROUTER_API_KEY`
set — one OpenRouter key covers every model the tool uses). Each run starts
with one tiny test call and stops immediately with a clear message if the key
doesn't work.

## Run
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 venv/bin/python3 verify_my_text.py \
      --text examples/chimpanzee_validation/my_text.md \
      --sources examples/chimpanzee_validation/sources \
      --output-dir data/chimpanzee_verification \
      --model gemini/gemini-2.5-flash-lite \
      --api-key config/google_api_key.txt --open

`--open` opens the `file://` viewer directly — no server needed. First run downloads
the SPECTER model (~440 MB) and decomposes each source (cached in
`<output-dir>/source_claims/`); re-runs are fast.

See `INPUT_FORMAT.md` for the input contract, `docs/MODEL_OPTIONS.md` for cheaper /
local model options, `LOCAL_MODELS.md` for the GPU-less setup.

## Reviewing and repairing your text

Each claim card in the viewer shows a **judge-confidence chip** (high / medium /
low, derived from vote splits and how the verdict was reached) and **triage
buttons**: mark a card *wrong source*, *rewrite text*, or *verdict wrong*, add a
note, and export — **Copy repair brief** (a self-contained markdown brief for any
LLM) or **Download review.json** (for the `/apply-review` Claude Code command).
An agent then fixes your text following `docs/REPAIR_PLAYBOOK.md` — including its
guardrails: every edit is logged to `changes.md` with the evidence quote, citation
swaps require the quoted passage, quantitative claims are never weakened to pass,
and one repair→verify cycle is the limit before a human read-through.

Add `--second-opinion` to any run to have a **second model** (default: plain
Gemini flash, same API key, ~a cent per run) re-read the same evidence for every
verdict. A confirmed disagreement never changes the verdict — it adds a
"⚠ 2nd opinion disagrees" chip and drops the confidence chip to low, in both
directions: a supported claim the second judge rejects (false-positive risk) and
an unsupported claim it accepts (judge too strict). Claims you already ruled on
via *verdict wrong* (`verdict_feedback.json`) are skipped and shown as
**author disputed** instead.

Uncited passages (the indigo "your own claim" cards) are also classified —
structural text, your own argument, or a **factual assertion with no citation**.
The last kind gets an amber **"citation needed?"** chip and a *Citation needed*
filter: a nudge to cite a source, never a verdict (nothing was checked). One tiny
LLM call per uncited claim, skipped with `--no-own-split`.

Before trusting a run on a **new paper**, do the 15-minute mini-audit in
`docs/NEW_PAPER_AUDIT.md` — hand-check 8 sampled verdicts against their sources.
All the tool's accuracy numbers come from one hand-audited paper; a new paper is
new territory.

Re-running the verifier on the fixed text is **incremental**: claims whose text
and citations didn't change keep their previous verdicts at zero API cost, and
the new viewer gets a **Changed (N)** filter plus ✎ markers showing what each
edited claim replaced (`--full` forces a complete re-run).

For a deeper audit of a finished run there is `deep_check.py`: a stronger model
re-reads every judged claim with source context and writes an independent
verdict plus commentary onto each card. It never changes the run's verdicts —
it exists to make a human review fast (`docs/DEEP_CHECK.md`).

## Writing a NEW text with a deep-research tool

If you don't have a draft yet, let a deep-research tool — Claude (research
mode / Claude Science), Elicit, Perplexity, GPT deep research, or any capable
LLM — write the cited text in a format that drops straight into this tool.
The demo text ("eggs as food") was produced this way, by Claude Science with
Opus 4.8. Two things decide whether the result verifies cleanly: **one
citation per sourced sentence** (never one citation covering a paragraph),
and **every citation points at a source that genuinely says that claim**.
The prompts below bake both in.

Pick by tool: **Claude Science** cites natively with pandoc `[@key]` + a
`.bib` export (Variant A; `import_claude_research.py` converts it) — every
other tool gets asked for `[[key]]` markers directly (Variant B).

<details>
<summary><b>Variant A — Claude Science</b> (native <code>[@key]</code> + <code>.bib</code>)</summary>

```text
Research the topic below and write an evidence-based text with citations.
This is a research-synthesis task: summarize what the published literature
reports and attribute each claim to its source, as input for a
citation-verification tool. Report the state of the evidence with citations,
whatever the subject — this is a literature summary, not personalized advice.

TOPIC: <your topic and any angle/length you want>

How to cite (this matters more than style — my text will be machine-checked
against the actual source PDFs):
1. Place a citation directly on EACH sentence that states something from a
   source — never group citations at the end of a paragraph, and never let one
   citation cover a run of sentences. One sourced sentence = one citation.
2. If a sentence mixes a sourced fact with my own reasoning, cite only the
   sourced clause: "Using their pooled method [@zhong2019], the panic looks
   overblown" — the cite backs the method, not the conclusion.
3. Sentences that are your own framing, transition, or interpretation carry
   NO citation. Leave them uncited on purpose.
4. Every cited claim must match what the source actually says — its direction,
   magnitude, and hedges. Do not round a "modest association" up to a "strong
   effect."
5. PARAPHRASE in your own words. If you use a direct quotation in quotation
   marks, the words must appear verbatim in the source you cite for it — if you
   are not certain, paraphrase instead.
6. Prefer open-access sources with a downloadable PDF or a DOI/arXiv id, so the
   sources can actually be fetched and checked. Use real, existing papers only —
   never invent a citation or a result.

Export as markdown with pandoc [@key] citations plus a .bib bibliography.
```

</details>

<details>
<summary><b>Variant B — Elicit / Perplexity / GPT deep research / any LLM</b> (<code>[[key]]</code> markers)</summary>

```text
Research the topic below and write an evidence-based text with citations.
This is a research-synthesis task: summarize what the published literature
reports and attribute each claim to its source, as input for a
citation-verification tool. Report the state of the evidence with citations,
whatever the subject — this is a literature summary, not personalized advice.

TOPIC: <your topic and any angle/length you want>

Output format (my text will be machine-checked against the actual source files,
so follow this exactly):

TEXT: flowing prose. After EACH sentence that states something from a source,
append a marker ` [[key]]` (e.g. ` [[smith2020]]`). Rules:
- One marker per sourced sentence — never group markers at the end of a
  paragraph, never let one marker cover several sentences.
- Two sources for one sentence: two markers ` [[a]] [[b]]`.
- If a sentence mixes a sourced fact with my own point, put the marker right
  after the sourced clause, even mid-sentence.
- Sentences that are my own framing/transition/interpretation get NO marker.
- Keys: lowercase author+year, letters/digits/_/- only; same source = same key.

Then a REFERENCES block, one line per key:
  key = Full citation (Title, authors, year, DOI/arXiv/URL if available)

Rules for the content:
- Every marked claim must match what the source actually says — direction,
  magnitude, and hedges. Don't overstate.
- Paraphrase. A direct quotation ("...") must be verbatim in the source cited
  for it; if unsure, paraphrase instead.
- Prefer open-access sources with a downloadable PDF or DOI/arXiv id. Use real,
  existing papers only — never invent a citation or a finding.
- End with an "Unresolved" note listing any claim you could not confidently
  attribute and any source you could not find a real reference for.
```

</details>

After the research tool answers: fetch the actual source files
(`download_sources.py` gets the open-access ones; paywalled ones go into
`inbox/` by hand + `ingest_downloads.py`), and give the markers a two-minute
skim — each on the one sentence it supports, your own framing left unmarked.

## Importing a Claude Science report
    venv/bin/python3 import_claude_research.py \
      --input report.md --output-dir data/my_article

Converts a pandoc-style export (`[@key]` citations + `.bib` bibliography) into the
input format above, plus a `sources_manifest.json` listing each source's url/DOI.
No API calls; free to re-run.

For the review loop there is also a **merge mode**: when a follow-up Claude
Science report found replacement sources for rejected claims (use the viewer's
"Copy Claude Science request" button to ask for them), merge just its
bibliography into your existing project — the report text is discarded:

    venv/bin/python3 import_claude_research.py \
      --input followup_export.md --merge-into data/my_article

Duplicates are skipped (matched by DOI, then title), colliding keys get a
suffix, and new keys are appended to the refs file. Then run
`download_sources.py`, cite the new `[[key]]`s, and re-verify (incremental).

## Downloading the cited sources
    venv/bin/python3 download_sources.py \
      --manifest data/my_article/sources_manifest.json

Fetches every open-access source into `sources/` (PDF where available, extracted
page text for web sources) and writes `download_report.md` listing what it could
not fetch — paywalled papers (with landing-page links) and references that need a
literature search. Drop those in manually, then re-run (already-present files are
skipped) and run `verify_my_text.py`. Test on a subset first with
`--keys key1,key2`.

References with no url/DOI are looked up by title on Semantic Scholar; the public
API rate-limits aggressively, so for manifests with many such entries get a free
API key (semanticscholar.org) into `config/semantic_scholar_api_key.txt`.

Paper metadata and lookups are provided by the [Semantic Scholar Open Data
Platform](https://www.semanticscholar.org/) (attribution per their API license).

## Filing manually-downloaded sources
    venv/bin/python3 ingest_downloads.py \
      --manifest data/my_article/sources_manifest.json

Drop the papers you downloaded yourself (any filename; .pdf/.txt/.html) into
`<manifest dir>/inbox/` and run this — it matches each file to its reference
(key in the filename, DOI inside the PDF, or title match), renames it to
`<key>.pdf`/`.txt`, moves it into `sources/`, updates the refs file, and
refreshes `download_report.md`. Unrecognized or ambiguous files are left
where they are with a note — nothing is ever guessed. `--dry-run` previews.
No API calls.

Any folder works as the inbox — e.g. scan your browser's download folder
directly, with `--copy` so the originals stay put:

    venv/bin/python3 ingest_downloads.py --manifest ... \
      --inbox ~/Downloads --copy

## License

MIT — see `LICENSE`.
