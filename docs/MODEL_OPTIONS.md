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
- The default is `gemini/gemma-4-31b-it` on Google's free tier since 2026-08-30 (author
  ruling, task #63/#66) — the judge pick since 2026-08-02 written into the config. Free
  means slow: the free tier limits requests per minute, so long runs pace themselves.
  For faster paid runs use `openrouter/google/gemma-4-31b-it`.
- History: `gemini-2.5-flash-lite` was the default 2026-07-04 → 2026-08-30 (it retires
  2026-10-16). Before that, `gemini-2.5-flash` ($2.50/M out) — **output-token cost
  dominated** because it is a *thinking* model that bills hidden reasoning tokens.

---

## Models available as of 2026-09-10 (web check for task #32 question q6)

_Checked on 2026-09-10 by web search plus OpenRouter's public model list (no money spent). The prices are per million tokens, written as input / output. A "flash" model is a company's small fast model; "open weights" means the model file itself can be downloaded and run elsewhere. Every price here drifts; the OpenRouter list (`https://openrouter.ai/api/v1/models`, no key needed) is the fastest re-check._

**What changed since the last check (2026-08-30).**

- **DeepSeek retired V4 Flash on 2026-09-10** and replaced it with **V4.1 Flash** (model string `deepseek-flash` on DeepSeek's own service, `deepseek/deepseek-v4.1-flash` on OpenRouter). The old name `deepseek/deepseek-v4-flash` is still accepted but now silently runs the new model, so the "supported arbiter override" in this repository is a different model than the one that was measured. DeepSeek's own service charges half price off-peak: $0.15 / $0.60 off-peak against $0.30 / $1.20 in peak hours (peak is 01:00-04:00 and 06:00-10:00 UTC, Monday to Friday). OpenRouter charges the peak price all day. The built-in "thinking off" switch in `llm_client.py` matches the old name only; whether the new model accepts the same switch is being tested by the q6 run.
- **DeepSeek V4 Pro** is retired from 2026-09-14 (its requests are routed to V4.1 Flash).
- **Alibaba released Qwen 3.8 Flash** (2026-08-26 on OpenRouter, $0.15 / $0.47, weights public under Alibaba's own licence). Qwen 3.7 Flash, the one the August panel used, is still listed.
- **Z.ai released GLM 5.3 Flash** (2026-08-26, open weights under the MIT licence, $0.15 / $0.50 list; OpenRouter still showed a $0.075 / $0.25 promotion on 2026-09-10). A new company for the panel.
- **Xiaomi MiMo V2.5** ($0.14 / $0.28 on OpenRouter) is another new company at the cheap end.
- **GPT 5.6 Luna** (OpenAI, the default arbiter) is $0.20 / $1.20 on OpenRouter; the $0.10 / $0.60 recorded in August was a discounted seller.
- **Kimi K2.6** is cheaper on Moonshot's own service ($0.60 / $3.41 measured in August) than on OpenRouter ($0.95 / $4.00). **Kimi K3** (2026-07-16, 2.8 trillion parameters) costs $3 / $15 and is not worth it for the checker.
- **Gemini 3.8 Flash** (Google, 2026-09-02, $0.75 / $3.75) is Google's newest small model. It is the same company as the default judge (Gemma 4), so it adds no independence to a panel.
- Also new but not useful here: Claude Fable 5.1 and Mythos 5.1 (2026-09-01, expensive), GPT 6 Astra (2026-09-04, expensive), Tencent Hy4 preview (2026-08-28, open weights, $0.83 / $2.50), Grok 4.6 (2026-08-12, $2 / $6), Meta Muse Spark 1.3 (2026-09-02). Claude Haiku is still at 4.5 (no Haiku 5). No public leaderboard measures these models on this tool's exact task (read a source, judge one citation); the closest ones, FACTS Grounding and Vectara's hallucination leaderboard, were last updated before these models came out.

**Cheap models by company, for a panel of independent checkers (one arm per company):**

| Company | Model | Route | Price in / out | Thinking off |
|---|---|---|---|---|
| DeepSeek | V4.1 Flash | `deepseek/deepseek-flash` (own service, off-peak) | $0.15 / $0.60 | `thinking: {type: disabled}` — verified working on 2026-09-10 (about 460 visible output tokens per call, no empty answers) |
| OpenAI | GPT 5.6 Luna | `openrouter/openai/gpt-5.6-luna` | $0.20 / $1.20 | built in (`reasoning.enabled=false`) |
| Alibaba | Qwen 3.8 Flash | `openrouter/qwen/qwen3.8-flash` | $0.15 / $0.47 | `reasoning: {enabled: false}` — and keep it off: with `reasoning: {enabled: true}` this model returned an EMPTY answer on 6 of 17 arbiter calls (2026-09-10), 115 to 196 seconds each. Cause is not refusal but truncation: `finish_reason=length` — the hidden reasoning spends the whole output budget before the visible answer starts, and `llm_client`'s own escalation to 6,000 then 12,000 max tokens does not always save it. It also hits OpenRouter rate limits on long-context calls, so re-ask unanswered rows |
| Z.ai | GLM 5.3 Flash | `openrouter/z-ai/glm-5.3-flash` | $0.15 / $0.50 list | **cannot be switched off** — OpenRouter answers "Reasoning is mandatory for this endpoint and cannot be disabled" (400) and every call fails; run it with `reasoning: {effort: low}` and treat it as a thinking model, which is not directly comparable with thought-free arms |
| Xiaomi | MiMo V2.5 | `openrouter/xiaomi/mimo-v2.5` | $0.14 / $0.28 | `reasoning: {enabled: false}` |
| Moonshot | Kimi K2.6 | `openai/kimi-k2.6` + `--api-base https://api.moonshot.ai/v1`, temperature 0.6 | $0.60 / $3.41 | `thinking: {type: disabled}` |
| Anthropic | Sonnet 5 | `claude-code/sonnet` | subscription, no bill | n/a |
| Mistral | Small 4 | `mistral/mistral-small-latest` | $0.15 / $0.60 | weak on public tests (intelligence index 11), not recommended |
| xAI | Grok 4.1 Fast | own service | $0.20 / $0.50 | untested here |

**The three model roles and their defaults as of 2026-09-10.** The tool asks about a claim in up to three places, and each place should be a different company, or asking twice tells you nothing new. The **judge** decides the verdict and is Google's `gemini/gemma-4-31b-it` on the free tier. The **arbiter** re-reads only the flagged claims with the whole source and is OpenAI's `openrouter/openai/gpt-5.6-luna` (on by default, paid, skipped with one note when no key is present). The **second opinion** (`--second-opinion`, off by default) re-reads the judge's own evidence for every verdict and became Anthropic's `claude-code/sonnet` on 2026-09-10 (task #67), replacing `gemini/gemini-2.5-flash`, which retires 2026-10-16. Three reasons for that pick: the old default rode the judge's own Gemini key and so shared the judge's family; Google's other small model, Gemini 3.8 Flash, has the same problem (see the note above that it "adds no independence to a panel"); and of the third-family candidates only Sonnet 5 is already measured in this project, because `deep_check.py` has used it as its own default reader since 2026-07-10. It costs nothing on a Claude subscription, needs no key, and when the `claude` command is not installed the pass is skipped with one note instead of failing the run. The cheap paid alternative to name explicitly is DeepSeek V4.1 Flash (`deepseek/deepseek-flash`, about a cent per run); do not point the second opinion at the arbiter's own model, because then two of the three layers are one model.

**Hidden reasoning makes the second checker STRICTER, measured 2026-09-10** (task #32, `docs/task32_thinking_2026-09-10/`): the same model and the same 27 rows, thinking off versus on, dropped the tool's own complaint 12 times versus 4 (OpenAI's Luna) and 13 times versus 1 (Alibaba's Qwen). Since 18 of those 27 rows carry a citation a human called accurate, a stricter checker is WORSE here: it keeps false alarms on the page. Output tokens per call roughly doubled (505 → 1,166 on Luna), which is how the switch is proven to have taken effect. Do not turn thinking on for the arbiter without re-measuring.

The seven-arm q6 run on 2026-09-10 (`benchmarks/run_q6_panel_2026-09-10.sh`, results under `docs/arbiter_replay_2026-09-10/`) is the first live use of the four new arms; its logs are the measured record of their real token counts and behaviour.

## Option B — Cheaper hosted APIs (one flag)

Per-million-token pricing, verified June 2026:

| Model | litellm string | Input | Output | Notes |
|---|---|---|---|---|
| Gemini 2.5 Flash | `gemini/gemini-2.5-flash` | $0.30 | **$2.50** | thinking model; expensive output; old default |
| Gemini 2.5 Flash-Lite | `gemini/gemini-2.5-flash-lite` | $0.10 | $0.40 | default 2026-07-04 → 2026-08-30; retires 2026-10-16; 0-FP judge on the paper1 bench |
| Gemini 2.0 Flash-Lite | `gemini/gemini-2.0-flash-lite` | $0.075 | $0.30 | cheapest in-family |
| GPT-4.1 nano | `openai/gpt-4.1-nano` | $0.10 | $0.40 | needs `OPENAI_API_KEY`; 128K ctx |
| GPT-4o-mini | `openai/gpt-4o-mini` | $0.15 | $0.60 | very reliable JSON, 128K ctx |
| Mistral Small 3.2 | `mistral/mistral-small-latest` | $0.075 | $0.20 | cheapest output of the hosted set |
| DeepSeek chat (V3.x) | `deepseek/deepseek-chat` | ~$0.14 | ~$0.28 | needs `DEEPSEEK_API_KEY` |
| DeepSeek V4 Flash | `deepseek/deepseek-v4-flash` | ~$0.09 | ~$0.18 | default ARBITER 2026-07-12 → 2026-08-30, still a supported override. As a judge, tested 2026-07-03: 7/11 on the judge bench — too strict on entailment; do not switch |
| GPT-5.6 luna (default arbiter) | `openrouter/openai/gpt-5.6-luna` | $0.10 | $0.60 | **default arbiter since 2026-08-30** (won the 2026-08-01 replay comparison); needs `OPENROUTER_API_KEY` or `config/openrouter_api_key.txt`; thought-free builtin |
| **Current default** Gemma 4 31B Instruct | `gemini/gemma-4-31b-it` | $0.00 | $0.00 | **default judge since 2026-08-30**; Google free tier only (rate-paced — slow but $0); what the gate runs on; the same model paid on OpenRouter lists $0.08 in / $0.35 out (checked 2026-08-18) |

Auth: export the provider env var, or pass `--api-key <raw-key-or-file>`.

### What a gate run actually costs and how long it takes (measured 2026-08-18, task #18)

Measured from `llm_calls.jsonl`, not estimated. One six-document ship-gate arm on
free Google gemma = **~3,450 calls, ~6.4M input tokens, ~0.21M output tokens**
(mean 1,874 input / 60 output tokens per call, median output 46). Priced at
OpenRouter's listed rate for the same model that is **~$0.59 per gate arm**;
~90% of the bill is input, which is why task #50 (trim the per-claim context)
is the lever that matters. All of task #18's gemma work to date — 28,470 calls,
52.5M in / 1.68M out — would have been **~$4.79**. The same volume on
flash-lite would be ~$5.90, so the free tier saves the whole amount rather than
a discount.

Speed, same source: median call **2.3s**, but the MEAN is 7.4s because free-tier
pacing waits are counted inside the call (they do not increment `api_attempts`).
On one arm, 12% of calls took ≥10s and consumed **69% of all model time**; one
call waited 6,179s. Measured wall clock 3h47 at concurrency 2, which matches
sum-of-latency/2 — so the free tier's cost is ~2.5 hours of waiting per arm, not
model slowness. OpenRouter's published per-provider throughput for this model is
38–200 tok/s against our effective ~18 tok/s, and a paid endpoint also lifts the
concurrency-2 pin (4 dropped claims on free Google, task #37 makes a drop look
like a red card), so a paid arm projects to **~15 min at concurrency 8**.
Caveat: OpenRouter's OWN free variant is unusable here — 20 requests/minute and
50/day (1,000/day after ever buying $10 of credit), i.e. ~a month for this task.

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
   *whole paper* in one call. (This supersedes the old note — now in
   `docs/archive/LOCAL_MODELS_ANALYSIS_2026-05-29.md` — that context
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
