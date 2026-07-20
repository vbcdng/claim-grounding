# Run with a local model (Ollama)

No API key, no cost, everything stays on your machine. The catch: the
hand-audited benchmark gates were run on Gemini, not on local models — treat a
local run as a cheaper draft, and re-check with an API model when the verdicts
matter.

Embeddings are always computed locally (SPECTER) no matter which model you
pick; the LLM is only used for the judgment calls.

## Steps

1. Install Ollama — `curl -fsSL https://ollama.com/install.sh | sh`, or the
   desktop app from ollama.com.
2. Pull a model: `ollama pull qwen2.5:14b-instruct` (recommended default; see
   "Picking a model" below).
3. Run the tool against the local endpoint:

   ```bash
   python3 verify_my_text.py \
     --text your_text.md --sources sources/ --references your_text.refs.txt \
     --model ollama/qwen2.5:14b-instruct \
     --api-base http://localhost:11434 \
     --no-arbiter --concurrency 1
   ```

4. Open the `viewer.html` the run prints (or pass `--open`).

Why those two extra flags: `--no-arbiter` skips the arbiter pass, whose default
model is a DeepSeek API model — without a key it only warns and skips, so the
flag just silences the warning. To keep the arbiter fully local instead, pass
`--arbiter ollama/qwen2.5:14b-instruct`. `--concurrency 1` because Ollama
serves one request at a time.

## Picking a model

- `qwen2.5:14b-instruct` — the best fit we found: reliable JSON output, sound
  entailment-style judgment, 32K context.
- `qwen2.5:7b-instruct` or `llama3.1:8b-instruct` — faster; good enough to
  validate the pipeline before committing to a bigger model.
- Avoid models under ~7B and "Coder" variants — this is claim/entailment work
  that needs clean JSON, not code completion.
- Prefer ≥32K context: the full-text extraction fallback sends a whole paper
  in one call.
- Quantization: Q4_K_M is the sweet spot; Q5/Q8 if RAM allows.
- Current-generation candidates and hardware notes: `docs/MODEL_OPTIONS.md`,
  Option A.

## Known gaps (deferred TODOs — referenced from docs/MODEL_OPTIONS.md)

1. Ollama's `format` (JSON-schema) parameter is not wired through litellm yet.
   It grammar-forces valid JSON, which would make small local models far more
   reliable; today the tool relies on prompting plus tolerant parsing.
2. `--chunk-words` and the output-token caps are not CLI-configurable, so a
   small-context model can overflow the full-text extraction fallback.
3. No head-to-head benchmark against the Gemini baseline has been run — the
   3-paper gate has only ever been scored on API models.

The original hardware analysis these notes grew from (Lenovo P52, CPU
inference, tokens/sec math) is preserved in the development repo's archive
(`docs/archive/LOCAL_MODELS_ANALYSIS_2026-05-29.md`; not part of the public
snapshot). Its cost math predates the 2026-07-16 removal of source
decomposition — runs now pay only the much smaller judging calls, so first
runs are far faster than it estimates.
