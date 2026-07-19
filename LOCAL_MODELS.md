# Running verify_my_text.py on a local model (notes)

_Status (2026-05-29): deferred. Plan is to test the tool with Gemini first, then revisit
local models. This file captures the analysis so we can pick up where we left off._

## Hardware in question
Lenovo P52 — **64 GB DDR4 (~2666 MHz)** RAM, **Quadro P1000/P2000 (4 GB VRAM)**.
- 4 GB VRAM is too small to hold useful models on the GPU → run on **CPU + system RAM**
  via **Ollama** or **llama.cpp**, offloading a few layers to the GPU for a small speed bump.
- 64 GB RAM means almost everything *fits*; the limiter is **speed (tokens/sec)**, not capacity.
- DDR4 (not DDR5) → tokens/sec run somewhat below 2026 benchmark figures that assume DDR5.

## What THIS tool actually needs from a model
The tool makes two kinds of LLM call (embeddings stay local on SPECTER, no LLM):
1. **Source claim extraction** — return a JSON array of atomic claims for a ~1,200-word chunk.
2. **Support judgment** — return a tiny `{supported, reason}` JSON for one claim + one passage.

Selection priorities (different from generic/coding model advice):
1. **Reliable JSON / instruction-following** — the #1 factor.
2. **Sound factual (entailment-style) judgment.**
3. **Context window is a non-issue** — sources are chunked to ~1,200 words (~4K tokens/call),
   so even an 8K-context model is plenty. (Phi-4's "small 16K context" does NOT matter here.)
4. Use **general instruct** models, **not** "Coder" variants — this is claim/entailment work.

See `modules/papertrail/source_decomposer.py` (`_CHUNK_WORD_TARGET = 1200`) for the chunk knob.

## Recommended models for this task (on the P52)
- **Best fit: Qwen2.5-14B-Instruct (Q4_K_M)** — strong structured/JSON output + factual judgment, 32K ctx.
- **Also excellent: Phi-4 (14B)** — strong reasoning; context limit irrelevant here.
- **Fast starting point: Qwen2.5-7B-Instruct or Llama-3.1-8B-Instruct** — likely good enough, faster; use to validate the pipeline first.
- **Avoid for extraction: ≤3B models** — atomic-claim extraction + clean JSON gets unreliable (fine for a smoke test only).
- **32B/70B**: better quality but slow; largely unnecessary here (see caching note).

(Benchmark figures and the latest releases like "Gemma 4" are unverified / past the assistant's
knowledge cutoff — the 14B-instruct recommendation does not depend on them.)

## Speed is better than it looks — because of caching
- **Source decomposition** is the slow part (several extraction calls per source, ~1–2k output
  tokens each), but it is **cached to disk per source** (`<output-dir>/source_claims/<id>.json`).
- **First run on a new source** (e.g. a ~15-page paper → ~6 chunks) on a 14B at ~5–9 tok/s:
  roughly **15–25 minutes, one time** (let it grind).
- **Every run after that:** source served from cache; only the small **judgment calls** run
  → iterating on your writing is **~1–2 minutes**.
- So a slower-but-smarter 14B is very tolerable: you pay the slow cost once per source.

## Practical setup
- Install **Ollama** (CLI, one-liner) or **LM Studio** (GUI).
- `ollama pull qwen2.5:14b-instruct` (and maybe `qwen2.5:7b-instruct` to compare speed).
- Run: `python3 verify_my_text.py --text ... --sources ... --model ollama/qwen2.5:14b-instruct --api-base http://localhost:11434`
- Quantization: **Q4_K_M** is the sweet spot; Q5/Q8 if RAM allows and you want a bit more quality.
- Offload a few layers to the 4 GB Quadro in Ollama/LM Studio for a small speed bump.

## High-leverage TODO when we revisit
1. **Ollama JSON/structured-output mode** (forces valid JSON — biggest reliability win for small
   local models). Judgment call maps directly; extraction (array) needs a small schema/wrapper.
   litellm can pass this through to Ollama.
2. **Make `--chunk-words` and output-token caps CLI-configurable** so they can be dialed to the
   chosen model (important for small-context models, where the current 8,000-token output request
   would overflow a 4K window).
3. Run `examples/` head-to-head: `ollama/qwen2.5:14b-instruct` vs the Gemini baseline; compare
   JSON reliability + verdicts.

## Decision
Test with **Gemini** first (works now). Revisit local models afterward using this doc.
