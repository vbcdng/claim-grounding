# Model options for verify_my_text.py (cheaper APIs + local models)

_Compiled 2026-06-03. Hosted prices and the Qwen3.6 / Gemma 4 details were verified by
web search in June 2026 (sources at the bottom); the rest is reasoning about this tool +
the P52 hardware. Pricing and Ollama tags drift — re-verify before relying on exact figures._

## How the tool spends money / compute
- The tool is **provider-agnostic via litellm** — switching model = changing `--model`
  (and a key). See `modules/papertrail/llm_client.py`.
- **Embeddings stay local** (SPECTER, CPU) — no LLM cost for retrieval.
- **Two cost centers:**
  1. **Source decomposition** — several claim-extraction calls per paper. **Cached to disk**
     (`<output-dir>/source_claims/<id>.json`), so paid *once per source*.
  2. **Judgments + full-text extraction fallback** — small calls per claim.
- Net effect: re-running on the same sources is nearly free; the bill/slow-part is
  **first runs on new papers**. Delete `source_claims/` to force re-decomposition with a
  new model.
- The default is `gemini-2.5-flash-lite` since 2026-07-04 (config change). With the old
  default (`gemini-2.5-flash`, $2.50/M out) **output-token cost dominated** because it is
  a *thinking* model that bills hidden reasoning tokens.

---

## Option B — Cheaper hosted APIs (one flag)

Per-million-token pricing, verified June 2026:

| Model | litellm string | Input | Output | Notes |
|---|---|---|---|---|
| Gemini 2.5 Flash | `gemini/gemini-2.5-flash` | $0.30 | **$2.50** | thinking model; expensive output; old default |
| **Current default** Gemini 2.5 Flash-Lite | `gemini/gemini-2.5-flash-lite` | $0.10 | $0.40 | **same Google key, ~6× cheaper output, not heavy-thinking; 0-FP judge on the paper1 bench** |
| Gemini 2.0 Flash-Lite | `gemini/gemini-2.0-flash-lite` | $0.075 | $0.30 | cheapest in-family |
| GPT-4.1 nano | `openai/gpt-4.1-nano` | $0.10 | $0.40 | needs `OPENAI_API_KEY`; 128K ctx |
| GPT-4o-mini | `openai/gpt-4o-mini` | $0.15 | $0.60 | very reliable JSON, 128K ctx |
| Mistral Small 3.2 | `mistral/mistral-small-latest` | $0.075 | $0.20 | cheapest output of the hosted set |
| DeepSeek chat (V3.x) | `deepseek/deepseek-chat` | ~$0.14 | ~$0.28 | needs `DEEPSEEK_API_KEY` |
| DeepSeek V4 Flash | `deepseek/deepseek-v4-flash` | ~$0.09 | ~$0.18 | **tested 2026-07-03: 7/11 on the judge bench — too strict on entailment (refused t49/t35/t27/t68); negatives all held. Do not switch; flash-lite stays** |

Auth: export the provider env var, or pass `--api-key <raw-key-or-file>`.

### Verified usable: `gemini-2.5-flash-lite`
Tested live through `LLMClient` with the existing `config/google_api_key.txt`:
- Model string resolves in litellm ✅, existing Google key authorizes ✅
- Support-judgment JSON ✅ (parsed even when ```json-fenced — parsers are regex-based)
- Full-text extraction JSON `{"sentences":[...]}` ✅
- Latency ~0.7s/call, no thinking-token waste

```bash
venv/bin/python3 verify_my_text.py --text ... --sources ... \
  --model gemini/gemini-2.5-flash-lite --api-key config/google_api_key.txt
```

**Recommendation (hosted):** `gemini/gemini-2.5-flash-lite` — least effort, ~6× cheaper
output, likely *more* reliable than `flash` here (no JSON-into-reasoning truncation).

---

## Option A — Local models (free), on the P52

Hardware: Lenovo P52, 64 GB DDR4, Quadro P1000/P2000 (4 GB VRAM) → **CPU + system RAM
inference via Ollama**; offload a few layers to the GPU for a small bump. See `LOCAL_MODELS.md`.

### What this tool needs from a local model
1. Reliable JSON / instruction-following (#1 factor).
2. Sound factual (entailment-style) judgment.
3. **≥32K context** — the full-text extraction fallback (`_extract_evidence`) sends a
   *whole paper* in one call. (This supersedes the old `LOCAL_MODELS.md` note that context
   is a non-issue — that predated the fallback.)
4. **general instruct** models, not "Coder" variants.

### Newest verified options (April 2026 releases)

**Gemma 4** (Apr 2, 2026) — best CPU fit:

| Variant | Total | Active | Ctx | Ollama tag | Q4 RAM |
|---|---|---|---|---|---|
| E4B | 4B | ~4.5B | 128K | `gemma4:e4b` | ~5 GB |
| **26B MoE** | 26B | **3.8B active** | **256K** | `gemma4:26b` | 14–18 GB |
| 31B Dense | 31B | 30.7B | 256K | `gemma4:31b` | ~20 GB |

The **26B MoE** activates only 3.8B params/token (8 experts + shared) → runs at ~4B-model
speed on CPU while delivering ~97% of 31B quality; 256K context fits whole papers; fits in
64 GB RAM. **Needs Ollama ≥ v0.24.0.**

**Qwen 3.6** (Apr 16, 2026) — `qwen3.6:27b` (fits 24 GB Q4), 1M context, native function
calling, top quality. **Catch:** *always-on chain-of-thought* (can't simply disable) →
slow + token-heavy on CPU and can route JSON into the reasoning channel. Wrong ergonomics
for this JSON tool on a GPU-less box unless max quality is essential.

**Proven dense fallbacks:** `qwen2.5:14b-instruct` (32K, ~16 GB, strong JSON) or `qwen3:14b`;
`llama3.1:8b` (~8 GB) as a fast smoke test; `mistral-small:24b` if you want a bigger dense model.

> Tags like `qwen3.6` / `gemma4` are past the assistant's training cutoff — confirm with
> `ollama pull <tag>` (registry renames things).

### Local recommendation (ranked for a GPU-less 64 GB box)
1. **`gemma4:26b` (MoE)** — best speed/quality on CPU, 256K ctx, cleanest on-ramp.
2. **`gemma4:e4b`** — fast pipeline smoke test first.
3. **`qwen2.5:14b-instruct` / `qwen3:14b`** — non-MoE dense fallback.
4. **`qwen3.6:27b`** — only for max quality, accepting slow + thinking overhead.

```bash
# needs Ollama >= v0.24.0 for Gemma 4; no API key needed for Ollama
ollama pull gemma4:26b
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 venv/bin/python3 verify_my_text.py \
  --text ... --sources ... \
  --model ollama/gemma4:26b --api-base http://localhost:11434
```

### Local gotchas
- **Gemma 4 "configurable thinking":** to keep JSON clean you likely need
  `enable_thinking=false` — guides note Gemma 4 otherwise routes JSON into the reasoning
  channel. If verdicts come back empty, check this first.
- **Speed/caching:** first decomposition of a new source on a 14B/26B-MoE ≈ 15–25 min once;
  re-runs ≈ 1–2 min (cached). 
- **Reliability lever (TODO):** Ollama's `format` parameter (≥ v0.5) takes a JSON schema and
  grammar-forces valid output — with it, model quality matters much less for JSON validity.
  The tool currently uses prompt + tolerant regex parsing; wiring `format` through litellm is
  the high-value upgrade for small local models (see `LOCAL_MODELS.md` TODO #1).
- **Small-context models** would overflow the full-text fallback — needs the configurable
  `--chunk-words`/output-cap work (`LOCAL_MODELS.md` TODO #2).

---

## Sources (web, June 2026)
- pricepertoken.com — LLM API Pricing 2026: https://pricepertoken.com/
- CloudZero — LLM API Pricing Comparison 2026: https://www.cloudzero.com/blog/llm-api-pricing-comparison/
- InsiderLLM — Best Local LLMs for Structured Output: https://insiderllm.com/guides/structured-output-local-llms/
- Ollama library — qwen3.6: https://ollama.com/library/qwen3.6
- Aurigai — Gemma 4 specs & run-locally guide: https://aurigait.com/blog/gemma-4-features-benchmarks-guide/
- BuildFastWithAI — Google Gemma 4: https://www.buildfastwithai.com/blogs/google-gemma-4-open-model
- Trilogy AI — Qwen 3.6 vs Gemma 4: https://trilogyai.substack.com/p/qwen-36-open-vs-opus-47-vs-gemma
- Ollama — Structured Outputs docs: https://docs.ollama.com/capabilities/structured-outputs
