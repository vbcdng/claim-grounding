"""
Provider-agnostic LLM client for the papertrail tool, backed by litellm.

One interface (`.call` / `.call_json`) over any litellm-supported provider, selected by a
`provider/model` string:  gemini/gemma-4-31b-it (default, free Google tier), openai/gpt-4o-mini,
anthropic/claude-sonnet-4-..., ollama/llama3, openrouter/...  Auth comes from --api-key
(a file path or a raw value) or the provider's env var (OPENAI_API_KEY, ANTHROPIC_API_KEY,
GEMINI_API_KEY, ...); for the Gemini default we fall back to config/google_api_key.txt.
"""

import os
import re
import json
import time
import hashlib
import datetime
import logging
import threading
from typing import Optional, Any, Dict

logger = logging.getLogger(__name__)

# ---- Actual-usage ledger (owner ask, 2026-07-11: track what each run REALLY
# cost, not just the pre-run estimate). Process-wide, thread-safe; every real
# API call through LLMClient.call records its reported token usage + the
# litellm-computed cost. verify_my_text writes the summary into
# metadata.llm_usage and prints it at the end of the run.
# Top-level counters are per-API-attempt (a truncation retry counts twice);
# the nested "by_purpose" rollup (task #20) counts LOGICAL calls — one per
# LLMClient.call() — which is also why the claude-code backend (own _call_impl,
# no token reporting) still shows up there with zero-token rows.
_USAGE_LOCK = threading.Lock()
_USAGE: Dict[str, Dict[str, Any]] = {}

# ---- Per-call log (task #20 phase 1, 2026-08-01): one JSONL line per LOGICAL
# LLM call, written to <output-dir>/llm_calls.jsonl once verify_my_text calls
# set_call_log(). Off (None) until then — library use / benchmarks unaffected.
# Responses are always recorded (they are small); prompts are hash-only unless
# PAPERTRAIL_LOG_PROMPTS=1 (they are huge, ~38k tokens/claim). The goal is a
# durable record that lets a later session reconstruct or re-test any call
# (the property benchmarks/arbiter_replay.py had to rebuild after the fact).
_CALL_LOG_LOCK = threading.Lock()
_CALL_LOG_PATH: Optional[str] = None
_CALL_SEQ = 0

# Per-thread context for the call in flight: lets _record_usage (which fires
# once per API attempt, inside the retry loop) accumulate tokens/cost onto the
# enclosing logical call() without changing its signature.
_TLS = threading.local()


def set_call_log(path: Optional[str]) -> None:
    """Enable (or disable, with None) the per-call JSONL log. Called once by
    verify_my_text when the output dir is known; appends thereafter. `seq`
    restarts at 1 per installed log — an incremental re-run appending to the
    same file starts its own 1..N sequence (lines are ordered by `ts`)."""
    global _CALL_LOG_PATH, _CALL_SEQ
    with _CALL_LOG_LOCK:
        _CALL_LOG_PATH = path
        _CALL_SEQ = 0


def _log_prompts_enabled() -> bool:
    return os.environ.get("PAPERTRAIL_LOG_PROMPTS", "").strip().lower() in ("1", "true", "yes")


def _log_call(model: str, purpose: str, claim_id: Optional[str], prompt: str,
              response: Optional[str], ctx: Dict[str, Any], latency_s: float,
              temperature: float, max_output_tokens: int) -> None:
    """Append one JSONL line for a finished logical call. Never raises — a
    logging failure must not look like a model failure."""
    global _CALL_SEQ
    if _CALL_LOG_PATH is None:
        return
    try:
        rec: Dict[str, Any] = {
            "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "seq": 0,  # assigned under the lock below
            "purpose": purpose,
            "model": model,
            "prompt_tokens": ctx["prompt_tokens"],
            "completion_tokens": ctx["completion_tokens"],
            "cached_prompt_tokens": ctx["cached_prompt_tokens"],
            "cost_usd": round(ctx["cost_usd"], 6),
            "latency_s": round(latency_s, 3),
            "api_attempts": ctx["api_calls"],
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "prompt_chars": len(prompt),
            "failed": response is None,
            "response_text": response,
        }
        served = ctx.get("served_by")
        if served:
            rec["served_by"] = served[0] if len(served) == 1 else served
        gens = ctx.get("generation_ids")
        if gens:
            rec["generation_ids"] = gens[0] if len(gens) == 1 else gens
        if claim_id is not None:
            rec["claim_id"] = claim_id
        # Card #141: the exact-request fingerprint a later arm's answer store
        # matches on, and a mark on every answer served from that store. Only
        # written while PAPERTRAIL_REUSE_FROM / PAPERTRAIL_RECORD_REQUESTS is
        # set, so a run with both unset logs byte-for-byte what it logged before.
        if ctx.get("request_sha256"):
            rec["request_sha256"] = ctx["request_sha256"]
        if ctx.get("reused_from"):
            rec["reused"] = True
            rec["reused_from"] = ctx["reused_from"]
        if _log_prompts_enabled():
            rec["prompt_text"] = prompt
        with _CALL_LOG_LOCK:
            if _CALL_LOG_PATH is None:
                return
            _CALL_SEQ += 1
            rec["seq"] = _CALL_SEQ
            with open(_CALL_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"llm_calls.jsonl write failed (call not logged): {e}")


# ---- Answer store (card #141, 2026-09-28; OFF unless PAPERTRAIL_REUSE_FROM is
# set). A second arm of the same test re-asks thousands of questions the first
# arm already asked word for word (measured offline: 93-100% of calls for a
# one-switch pair, 63-71% after a prompt change — benchmarks/card141_reuse_measure.py).
# With PAPERTRAIL_REUSE_FROM=<llm_calls.jsonl>[<os.pathsep><more>] the client
# serves a request whose fingerprint (sha256 over the EXACT request body minus
# the key: model, messages, temperature, clamped max_tokens, provider params,
# api_base) matches a usable recorded answer, and asks the model only for the
# rest. Multiset: a request recorded k times serves at most k asks, in recorded
# order (votes/retries ask the same request on purpose). Failed answers are
# never loaded. Refused outright with PAPERTRAIL_STABILITY_RUN set: a
# verdict-stability measurement needs fresh samples by definition.
# PAPERTRAIL_RECORD_REQUESTS=1 writes the fingerprint without serving anything
# (the gate script sets it, so every gate arm can donate later).
_REUSE_LOCK = threading.Lock()
_REUSE_STORE: Optional[Dict[str, list]] = None   # fingerprint -> [(answer, donor path), oldest first]
_REUSE_STORE_SRC: Optional[str] = None           # env value the store was loaded from
_REUSE_STATS = {"loaded": 0, "served": 0, "asked_live": 0}


def _reuse_env() -> str:
    return os.environ.get("PAPERTRAIL_REUSE_FROM", "").strip()


def _record_requests_enabled() -> bool:
    return bool(_reuse_env()) or os.environ.get(
        "PAPERTRAIL_RECORD_REQUESTS", "").strip().lower() in ("1", "true", "yes")


def _stability_run() -> bool:
    return os.environ.get("PAPERTRAIL_STABILITY_RUN", "").strip() not in ("", "0")


STABILITY_REFUSAL = (
    "Answer reuse (PAPERTRAIL_REUSE_FROM / the gate's REUSE_FROM) is switched on, "
    "but this run is a verdict-stability measurement (PAPERTRAIL_STABILITY_RUN, "
    "or the verdict vote). A stability measurement asks the same questions again "
    "to see whether the answers change, so copying the earlier answers would make "
    "it measure nothing. Switch one of the two off.")


def request_fingerprint(body: Dict[str, Any]) -> str:
    """sha256 over a request body (everything sent except the key). Canonical
    JSON, so key order never matters and any byte change in any field does."""
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def load_answer_store(paths: list) -> Dict[str, list]:
    """fingerprint -> [(answer, path)] usable recorded answers (oldest first).
    A line without request_sha256 (recorded before card #141, or with
    recording off) cannot be matched exactly and is skipped. Raises
    FileNotFoundError for a missing file: asking to reuse a file that is not
    there is a typo, not a request to run everything live."""
    store: Dict[str, list] = {}
    for path in paths:
        if not os.path.isfile(path):
            raise FileNotFoundError(
                f"PAPERTRAIL_REUSE_FROM names {path}, which does not exist.")
        rows = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        rows.sort(key=lambda r: (str(r.get("ts", "")), r.get("seq", 0)))
        for r in rows:
            fp = r.get("request_sha256")
            ans = r.get("response_text")
            if not fp or r.get("failed") or not isinstance(ans, str) or not ans:
                continue
            store.setdefault(fp, []).append((ans, path))
    return store


def answer_store_active() -> bool:
    """Load the store for the current env value (once) and say whether reuse
    is on. Raises RuntimeError (STABILITY_REFUSAL) when combined with a
    stability run, FileNotFoundError on a missing donor file."""
    global _REUSE_STORE, _REUSE_STORE_SRC
    src = _reuse_env()
    if not src:
        return False
    if _stability_run():
        raise RuntimeError(STABILITY_REFUSAL)
    with _REUSE_LOCK:
        if _REUSE_STORE is None or _REUSE_STORE_SRC != src:
            paths = [p for p in src.split(os.pathsep) if p.strip()]
            _REUSE_STORE = load_answer_store(paths)
            _REUSE_STORE_SRC = src
            _REUSE_STATS.update(loaded=sum(len(v) for v in _REUSE_STORE.values()),
                                served=0, asked_live=0)
            logger.info(f"Answer reuse ON: {_REUSE_STATS['loaded']} recorded answers "
                        f"loaded from {len(paths)} call log(s)")
            if not _REUSE_STATS["loaded"]:
                logger.warning("Answer reuse ON but no answer could be loaded: the "
                               "earlier arm's call log carries no request "
                               "fingerprints (recorded before card #141 or with "
                               "recording off). Every call is asked live.")
    return True


def _take_answer(fp: str) -> Optional[tuple]:
    """(answer, donor path) for the oldest unused recorded answer, or None."""
    with _REUSE_LOCK:
        lst = (_REUSE_STORE or {}).get(fp)
        if lst:
            _REUSE_STATS["served"] += 1
            return lst.pop(0)
        _REUSE_STATS["asked_live"] += 1
        return None


def reuse_summary() -> Optional[Dict[str, int]]:
    """{loaded, served, asked_live} for this process, or None when reuse is off."""
    if not _reuse_env():
        return None
    with _REUSE_LOCK:
        return dict(_REUSE_STATS)


def reset_answer_store() -> None:
    """Forget the loaded store (tests; a process that changes the env value
    reloads on its own)."""
    global _REUSE_STORE, _REUSE_STORE_SRC
    with _REUSE_LOCK:
        _REUSE_STORE = None
        _REUSE_STORE_SRC = None
        _REUSE_STATS.update(loaded=0, served=0, asked_live=0)


# Built-in per-model provider params, applied when PAPERTRAIL_LLM_EXTRA_BODY
# doesn't name the model itself. Matched on the model string's PREFIX (not
# substring) so an "openrouter/deepseek/..." route is never touched — OpenRouter
# has its own reasoning switch and may not pass a native `thinking` param through.
_BUILTIN_EXTRA_BODY: Dict[str, Dict[str, Any]] = {
    # gpt-5.6-luna via OpenRouter (the default arbiter since 2026-08-30):
    # OpenRouter's unified reasoning switch keeps it thought-free — verified in
    # the 2026-08-01 replay comparison (524 output tokens/call, clean JSON;
    # commit a2bf0b9 verified the switch itself). Hidden reasoning would burn
    # the output budget and, on other models, measurably hurt quote fidelity.
    "openrouter/openai/gpt-5.6-luna": {"reasoning": {"enabled": False}},
    # deepseek-v4-flash (default arbiter until 2026-08-30, still a supported
    # override): DeepSeek's 2026-07-31 refresh of
    # this alias burns the whole output budget on hidden thinking and returns
    # EMPTY on arbiter-length prompts — the arbiter then silently annotates
    # nothing (it never errors by design). Disabling thinking restores normal
    # answers (verified 2026-08-01; `enable_thinking: false` does NOT work).
    # Escape hatch: PAPERTRAIL_LLM_EXTRA_BODY='{"deepseek/deepseek-v4-flash": {}}'.
    "deepseek/deepseek-v4-flash": {"thinking": {"type": "disabled"}},
    # deepseek-flash = V4.1 Flash, DeepSeek's own service since 2026-09-10; it
    # RETIRED v4-flash and the old name now silently routes to this model, so
    # any measurement keyed to "deepseek-v4-flash" describes a model that is
    # gone. Same hidden-thinking behaviour, same switch — verified live on
    # 2026-09-10 (task #32 q6 panel: 27 calls, ~460 visible output tokens each,
    # no empty answers). The OpenRouter route is prefix-excluded as usual.
    "deepseek/deepseek-flash": {"thinking": {"type": "disabled"}},
    # gemma-4 on Google's own API (free tier only): hidden thinking eats the
    # output budget and can return EMPTY under a tight cap; thinkingLevel
    # MINIMAL zeroes it (verified live 2026-08-02, MODEL_HOSTING_LANDSCAPE §6).
    # NOTE: litellm mishandles extra_body for the gemini/ provider on both
    # versions we tested: 1.74.0 silently DROPS it, and 1.84.0 places it at
    # the request ROOT, where Google does not read generation settings
    # (request capture + live probe, 2026-09-01, task #71 — the no-workaround
    # arm returned EMPTY). So _call_impl merges gemini/ payloads as TOP-LEVEL
    # litellm kwargs instead — thinkingConfig is a GenerationConfig field
    # litellm forwards. KEEP this workaround even though 1.84.0 "supports"
    # extra_body for gemini/. The OpenRouter gemma route is prefix-excluded
    # as usual.
    "gemini/gemma-4": {"thinkingConfig": {"thinkingLevel": "MINIMAL"}},
}


def _extra_body_for(model: str) -> Optional[Dict[str, Any]]:
    """Per-model provider params. PAPERTRAIL_LLM_EXTRA_BODY — a JSON dict
    mapping a model-string substring to an extra_body payload, e.g.
    '{"qwen3.7-flash": {"reasoning": {"enabled": false}}}' — wins when it names
    the model (an empty payload {} disables any built-in default); otherwise
    the _BUILTIN_EXTRA_BODY prefix table applies. For gemini/-prefixed models
    the payload is merged as top-level litellm kwargs, not extra_body (which
    litellm drops for that provider)."""
    raw = os.environ.get("PAPERTRAIL_LLM_EXTRA_BODY")
    if raw:
        try:
            mapping = json.loads(raw)
        except ValueError:
            mapping = None
            logger.warning("PAPERTRAIL_LLM_EXTRA_BODY is not valid JSON — ignored")
        if mapping is not None and not isinstance(mapping, dict):
            mapping = None
            logger.warning("PAPERTRAIL_LLM_EXTRA_BODY must be a JSON object — ignored")
        if mapping:
            for pattern, body in mapping.items():
                if pattern in model and isinstance(body, dict):
                    return body or None
    for prefix, body in _BUILTIN_EXTRA_BODY.items():
        if model.startswith(prefix):
            return body
    return None


def _usage_field(u: Any, *path: str) -> Any:
    """Walk `path` through `u`, which may be a pydantic-ish object or a plain
    dict (or nested mix of both) at each step. Returns None on any miss."""
    cur = u
    for key in path:
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(key)
        else:
            cur = getattr(cur, key, None)
    return cur


def _cached_prompt_tokens(u: Any) -> int:
    """Cache-hit token count off a response `usage` object, checking known
    provider shapes in order (first hit wins). A cache-write count
    (Anthropic's cache_creation_input_tokens) is a cost, not a hit, so it's
    not counted here. Providers that don't report caching -> 0."""
    for path in (("prompt_cache_hit_tokens",),          # DeepSeek/OpenAI-compat
                 ("cache_read_input_tokens",),           # Anthropic-shaped
                 ("prompt_tokens_details", "cached_tokens")):  # OpenAI-shaped
        v = _usage_field(u, *path)
        if v:
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
    return 0


def _record_usage(model: str, resp: Any) -> None:
    try:
        u = getattr(resp, "usage", None)
        pt = int(_usage_field(u, "prompt_tokens") or 0)
        ct = int(_usage_field(u, "completion_tokens") or 0)
        cached = _cached_prompt_tokens(u)
    except Exception:
        pt = ct = cached = 0
    cost = 0.0
    try:
        import litellm
        cost = float(litellm.completion_cost(completion_response=resp) or 0.0)
    except Exception:
        pass                        # unknown model pricing -> tokens still counted
    with _USAGE_LOCK:
        m = _USAGE.setdefault(model, {"calls": 0, "prompt_tokens": 0,
                                      "completion_tokens": 0, "cost_usd": 0.0,
                                      "cached_prompt_tokens": 0})
        m["calls"] += 1
        m["prompt_tokens"] += pt
        m["completion_tokens"] += ct
        m["cost_usd"] += cost
        m["cached_prompt_tokens"] += cached
    # Accumulate onto the enclosing logical call (task #20): retries fold into
    # one line/rollup entry instead of appearing as separate calls.
    ctx = getattr(_TLS, "ctx", None)
    if ctx is not None:
        ctx["api_calls"] += 1
        ctx["prompt_tokens"] += pt
        ctx["completion_tokens"] += ct
        ctx["cached_prompt_tokens"] += cached
        ctx["cost_usd"] += cost
        # Who actually served it, and that answer's receipt number (task #31,
        # 2026-08-20). A reseller like OpenRouter fulfils one model name from
        # many suppliers holding copies at different numerical precisions, and
        # those copies answer differently — the 4-bit copy flipped one fixed
        # question's verdict 5 times in 30 where the 16-bit copy never did. With
        # neither field recorded, a finished paid run cannot be attributed
        # afterwards (the reseller's lookup is per-request and needs the receipt
        # number), which is exactly what happened to task #31's first two paid
        # runs. Both come free with the response.
        try:
            prov = getattr(resp, "provider", None)
            if isinstance(prov, str) and prov:
                seen = ctx.setdefault("served_by", [])
                if prov not in seen:
                    seen.append(prov)
            hp = getattr(resp, "_hidden_params", None)
            if isinstance(hp, dict):
                hdrs = hp.get("additional_headers") or hp.get("headers") or {}
                gen = hdrs.get("x-generation-id") if isinstance(hdrs, dict) else None
                if isinstance(gen, str) and gen:
                    ctx.setdefault("generation_ids", []).append(gen)
        except Exception:
            pass                    # provenance is a nice-to-have, never a failure


def _roll_purpose(model: str, purpose: str, ctx: Dict[str, Any]) -> None:
    """Fold one finished logical call into llm_usage[model]['by_purpose']."""
    with _USAGE_LOCK:
        m = _USAGE.setdefault(model, {"calls": 0, "prompt_tokens": 0,
                                      "completion_tokens": 0, "cost_usd": 0.0,
                                      "cached_prompt_tokens": 0})
        bp = m.setdefault("by_purpose", {})
        p = bp.setdefault(purpose, {"calls": 0, "prompt_tokens": 0,
                                    "completion_tokens": 0, "cost_usd": 0.0,
                                    "cached_prompt_tokens": 0})
        p["calls"] += 1
        p["prompt_tokens"] += ctx["prompt_tokens"]
        p["completion_tokens"] += ctx["completion_tokens"]
        p["cached_prompt_tokens"] += ctx["cached_prompt_tokens"]
        p["cost_usd"] += ctx["cost_usd"]


def usage_summary() -> Dict[str, Dict[str, Any]]:
    """Per-model actuals accumulated so far in this process:
    {model: {calls, prompt_tokens, completion_tokens, cost_usd, cached_prompt_tokens,
    by_purpose: {purpose: {calls, prompt_tokens, completion_tokens,
    cached_prompt_tokens, cost_usd}}}}.
    cached_prompt_tokens is a subset of prompt_tokens (provider cache hits,
    ~10x cheaper than a cache miss); 0 for providers that don't report it.
    Top-level `calls` counts API attempts; by_purpose `calls` counts logical
    LLMClient.call() invocations (retries fold in) — the claude-code backend
    appears only in by_purpose, with zero token counts (the CLI reports none)."""
    with _USAGE_LOCK:
        snap: Dict[str, Dict[str, Any]] = {}
        for k, v in _USAGE.items():
            d = dict(v)
            if "by_purpose" in d:
                d["by_purpose"] = {p: dict(pv) for p, pv in d["by_purpose"].items()}
            snap[k] = d
    for v in snap.values():
        v["cost_usd"] = round(v["cost_usd"], 6)
        for pv in v.get("by_purpose", {}).values():
            pv["cost_usd"] = round(pv["cost_usd"], 6)
    return snap

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_GEMINI_CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "gemini_config.json")
DEFAULT_GEMINI_KEY_PATH = os.path.join(PROJECT_ROOT, "config", "google_api_key.txt")

# Models served ONLY on a free, hard-rate-limited tier (Google's gemma-4:
# ~16K tokens/min + 14K requests/day PER KEY, no paid tier exists —
# MODEL_HOSTING_LANDSCAPE §6). A 429 there is expected steady-state, not an
# anomaly: waiting IS the pacing. Calls on these models retry rate errors up
# to _MAX_RATE_WAITS without consuming regular attempts, so an overnight or
# background run self-paces instead of dropping verdicts after 3 tries.
# Google's Gemini Flash models on the free seat behave the same way (card #100,
# 2026-09-09): the free tier throttles per minute AND per day, so a per-minute
# 429 is the pacing, not a failure, and must not consume one of the three
# regular attempts. Only the gemini/ routes are listed — an OpenRouter-hosted
# Gemini would be a paid seat with ordinary retries.
_FREE_TIER_PACED_PREFIXES = ("gemini/gemma-4", "gemini/gemini-3", "gemini/gemini-flash")

# The free Google seat rejects any request over roughly 52,000 characters
# (measured live 2026-08-08, task #15). A bigger prompt can NEVER succeed
# there — but the patient pacing above would keep retrying it anyway; one run
# sat stalled for about an hour on a ~71k-token source before this guard
# existed (working-style review lesson 3, approved 2026-08-09). Oversized
# prompts on free-tier-paced models fail immediately with a clear line
# instead: same downstream handling as any failed call, an hour sooner.
_FREE_TIER_MAX_PROMPT_CHARS = 52_000
_MAX_RATE_WAITS = 40   # worst case ~43 min on one key; a daily-quota 429 still gives up
# Google-side server errors (litellm InternalServerError / ServiceUnavailableError,
# "An internal error has occurred", 502/504) on the same free seat are also
# transient and also come in waves: the two day75b gate runs of 2026-09-08/09
# lost 341 and 52 calls to the three fast retries (1 s, 2 s) below and were
# unscorable (task #37 rule). On paced models such errors now wait 60 s each,
# up to _MAX_SERVER_WAITS, without consuming a regular attempt (author go 2026-09-09).
_MAX_SERVER_WAITS = 20   # worst case ~20 min per call
_SERVER_ERROR_MARKERS = ("internalservererror", "internal server error",
                         "internal error has occurred", "serviceunavailableerror",
                         "service unavailable", "status code 500", "status code 502",
                         "status code 503", "status code 504", "bad gateway",
                         "gateway timeout", '"code": 500', '"code": 503')


def _free_google_only() -> bool:
    """FREE_GOOGLE_ONLY=1 (env) = money-lock mode: only Google keys from files
    named config/google_api_key*_free.txt (keys with NO billing attached, so
    Google cannot charge them) may be used — except by a Gemma model, which is
    free on every key and rotates over all key files (card 143, see
    _gemini_key_files) — and every non-Google paid provider
    is refused at client construction. The $0 claude-code backend is unaffected
    (it never runs this class's __init__). Set by the /free-google-api skill."""
    return os.environ.get("FREE_GOOGLE_ONLY", "").strip() not in ("", "0")


def _is_gemma(model: Optional[str]) -> bool:
    """Exactly the litellm `gemini/gemma-*` family. Gemma on Google direct is
    free of charge on every key (Google offers it on the free tier only — no
    paid tier exists), so under FREE_GOOGLE_ONLY a Gemma call may use a billed
    key without any charge (card 143, author 2026-09-29)."""
    return str(model or "").startswith("gemini/gemma-")


def _gemini_key_files(model: Optional[str] = None) -> list:
    """config/google_api_key*.txt, sorted — google_api_key.txt (the primary)
    first, then google_api_key2.txt, google_api_key3.txt… One key per file.
    Each extra Google account's key adds its own free-tier quota; calls
    round-robin across them and a rate-limited call switches to the next key.
    Under FREE_GOOGLE_ONLY only *_free.txt files (no-billing keys) are eligible,
    EXCEPT for a Gemma model (_is_gemma), which gets every key file so a locked
    Gemma job rotates over both Google accounts (card 143). model=None keeps
    the strict free-only list (callers that don't name a model)."""
    import glob
    strict = _free_google_only() and not _is_gemma(model)
    pattern = "google_api_key*_free.txt" if strict else "google_api_key*.txt"
    return sorted(glob.glob(os.path.join(PROJECT_ROOT, "config", pattern)))

# Output-token ceiling used when litellm doesn't know the model (the flash
# family's real cap). Requests are clamped to the model's ceiling so batched
# callers whose cap scales with input size (argument_map edges: 128/claim;
# dedup: pairs-chunked) degrade to a truncation retry instead of a provider 400.
FALLBACK_OUTPUT_CAP = 65536


def _default_model() -> str:
    """Gemini model from gemini_config.json (claim_validation section), litellm-prefixed."""
    try:
        with open(DEFAULT_GEMINI_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        name = cfg.get("claim_validation", {}).get("model_name")
        if name:
            return f"gemini/{name}"
    except Exception as e:
        logger.warning(f"Could not read default model from gemini_config.json: {e}")
    return "gemini/gemma-4-31b-it"


class LLMClient:
    """Minimal multi-provider chat client with JSON helpers.

    A model of "claude-code" / "claude-code/<alias>" transparently constructs the
    $0-API ClaudeCodeClient subclass (local `claude` CLI) instead — every existing
    instantiation site gets the free backend just by passing that model string."""

    def __new__(cls, model: Optional[str] = None, api_key: Optional[str] = None,
                api_base: Optional[str] = None):
        if cls is LLMClient and str(model or "").startswith("claude-code"):
            from .claude_code_backend import ClaudeCodeClient
            return super().__new__(ClaudeCodeClient)
        return super().__new__(cls)

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None,
                 api_base: Optional[str] = None):
        self.model = self._normalize_model(model)
        self.provider = self.model.split("/", 1)[0]
        if _free_google_only() and self.provider != "gemini":
            raise RuntimeError(
                f"FREE_GOOGLE_ONLY is set, but this run asked for the model "
                f"'{self.model}' (provider '{self.provider}'). In free-only mode "
                f"the only allowed backends are Google models on the no-billing "
                f"keys (config/google_api_key*_free.txt) and the $0 claude-code "
                f"backend. Unset FREE_GOOGLE_ONLY to use a paid provider.")
        if _free_google_only() and api_base:
            raise RuntimeError(
                "FREE_GOOGLE_ONLY is set, but this run passed --api-base. A "
                "custom endpoint could route calls to a paid service, so it is "
                "refused in free-only mode.")
        self.api_base = api_base
        self._api_keys = self._resolve_api_keys(api_key)
        self.api_key = self._api_keys[0] if self._api_keys else None
        self._key_rr = 0    # round-robin cursor over _api_keys (racy under threads: harmless)
        self._patient_rate = self.model.startswith(_FREE_TIER_PACED_PREFIXES)

        import litellm
        litellm.drop_params = True            # ignore params a given provider doesn't support
        litellm.suppress_debug_info = True
        self._completion = litellm.completion
        try:
            self._output_cap = (int(litellm.get_max_tokens(self.model) or 0)
                                or FALLBACK_OUTPUT_CAP)
        except Exception:
            self._output_cap = FALLBACK_OUTPUT_CAP
        logger.info(f"LLM backend: {self.model}"
                    + (f" (api_base={api_base})" if api_base else ""))

    @staticmethod
    def _normalize_model(model: Optional[str]) -> str:
        if not model:
            return _default_model()
        if str(model).startswith("claude-code"):
            from .claude_code_backend import canonical_model
            return canonical_model(model)
        return model if "/" in model else f"gemini/{model}"

    def _resolve_api_keys(self, api_key: Optional[str]) -> list:
        """All usable keys, primary first. An EXPLICIT key (--api-key) means
        "use exactly this one" — no rotation. The Gemini default reads every
        config/google_api_key*.txt so a second Google account's key
        (google_api_key2.txt) adds its own free-tier quota automatically."""
        if api_key:
            if _free_google_only():
                raise RuntimeError(
                    "FREE_GOOGLE_ONLY is set, but this run passed an explicit "
                    "--api-key. There is no way to verify that key is one of "
                    "the no-billing ones, so it is refused in free-only mode — "
                    "the free keys in config/google_api_key*_free.txt are "
                    "picked up automatically instead.")
            # Accept either a path to a key file or a raw key value.
            if os.path.exists(api_key):
                with open(api_key, "r", encoding="utf-8") as f:
                    return [f.read().strip()]
            return [api_key.strip()]
        # No key given: for the Gemini default, fall back to the project key files.
        keys: list = []
        if self.provider == "gemini":
            loaded_files: list = []
            for path in _gemini_key_files(self.model):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        key = f.read().strip()
                except OSError as e:
                    logger.warning(f"Could not read {path}: {e}")
                    continue
                if key and key not in keys:
                    keys.append(key)
                    loaded_files.append(os.path.basename(path))
            if _free_google_only():
                if not keys:
                    raise RuntimeError(
                        "FREE_GOOGLE_ONLY is set, but no config/google_api_key*"
                        "_free.txt file was found next to this checkout. In "
                        "free-only mode the run refuses to fall back to an "
                        "environment key (it could be a paid one). Add the "
                        "no-billing key files or unset FREE_GOOGLE_ONLY.")
                if _is_gemma(self.model):
                    # Gemma is free on every key: rotate over all accounts,
                    # duplicate key values dropped above (key2 == key2_free).
                    logger.info("FREE_GOOGLE_ONLY: Gemma model, rotating over "
                                f"all Google key files: {', '.join(loaded_files)}")
                else:
                    logger.info("FREE_GOOGLE_ONLY: Google keys restricted to "
                                f"no-billing files: {', '.join(loaded_files)}")
        elif _free_google_only():
            # Unreachable in practice (__init__ refuses non-gemini providers
            # first), kept as a second lock in case a subclass skips that check.
            raise RuntimeError(
                f"FREE_GOOGLE_ONLY is set; provider '{self.provider}' has no "
                f"free no-billing key and is refused in free-only mode.")
        # Empty -> rely on the provider's env var (litellm reads it automatically).
        return keys

    # Count of call() invocations that ended in None (retries exhausted / empty
    # content). Consumers snapshot it around a unit of work to tell "the model
    # said no" apart from "the model never answered" — a failed call must never
    # be indistinguishable from a genuine negative (rerun.py refuses to reuse
    # verdicts minted under failures; verify_my_text tallies them at run end).
    failed_calls = 0

    def max_prompt_chars(self) -> Optional[int]:
        """The largest prompt this client will actually send, in characters, or
        None when it has no known ceiling.

        This is the same number the size preflight in call() enforces, exposed
        so a caller can shape its context to fit instead of having the call
        skipped (task #105). It is a measured provider limit, not a token
        estimate: on the free-tier-paced Google seats a bigger prompt is
        refused by the provider, so it can never succeed."""
        return _FREE_TIER_MAX_PROMPT_CHARS if self._patient_rate else None

    def call(self, prompt: str, temperature: float = 0.1, max_output_tokens: int = 8000,
             purpose: str = "untagged", claim_id: Optional[str] = None) -> Optional[str]:
        """One logical LLM call. `purpose` names which pipeline question is being
        asked (fixed vocabulary, see the call sites; "untagged" is visible in the
        end-of-run table, never fatal); `claim_id` is recorded in the per-call log
        when the caller has one in scope. Both are bookkeeping only (task #20) —
        they never influence the request."""
        ctx: Dict[str, Any] = {"api_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                               "cached_prompt_tokens": 0, "cost_usd": 0.0}
        # Size preflight for the free seat (see _FREE_TIER_MAX_PROMPT_CHARS):
        # an oversized prompt is a guaranteed failure, so fail it now instead
        # of letting the pacing loop retry it for up to an hour.
        if self._patient_rate and len(prompt) > _FREE_TIER_MAX_PROMPT_CHARS:
            logger.warning(
                f"Prompt of {len(prompt):,} characters SKIPPED without calling "
                f"[{purpose}{', claim ' + claim_id if claim_id else ''}]: the free "
                f"tier rejects requests over ~{_FREE_TIER_MAX_PROMPT_CHARS:,} "
                f"characters (measured 2026-08-08). Shrink the context for this "
                f"item or run it on a paid model.")
            self.failed_calls += 1
            _roll_purpose(self.model, purpose, ctx)
            _log_call(self.model, purpose, claim_id, prompt, None, ctx,
                      latency_s=0.0, temperature=temperature,
                      max_output_tokens=max_output_tokens)
            return None
        # Card #141 answer store. Only the plain litellm path is covered (a
        # subclass with its own _call_impl, e.g. the claude-code backend, never
        # fingerprints or reuses). Off = nothing below runs.
        if _record_requests_enabled() and type(self)._call_impl is LLMClient._call_impl:
            ctx["request_sha256"] = request_fingerprint(
                self._request_body(prompt, temperature, max_output_tokens))
            if answer_store_active():
                hit = _take_answer(ctx["request_sha256"])
                if hit is not None:
                    reused, ctx["reused_from"] = hit
                    _roll_purpose(self.model, purpose, ctx)
                    _log_call(self.model, purpose, claim_id, prompt, reused, ctx,
                              latency_s=0.0, temperature=temperature,
                              max_output_tokens=max_output_tokens)
                    return reused
        _TLS.ctx = ctx
        t0 = time.time()
        try:
            out = self._call_impl(prompt, temperature=temperature,
                                  max_output_tokens=max_output_tokens)
        finally:
            _TLS.ctx = None
        if out is None:
            self.failed_calls += 1
        _roll_purpose(self.model, purpose, ctx)
        _log_call(self.model, purpose, claim_id, prompt, out, ctx,
                  latency_s=time.time() - t0, temperature=temperature,
                  max_output_tokens=max_output_tokens)
        return out

    def _request_body(self, prompt: str, temperature: float,
                      max_output_tokens: int) -> Dict[str, Any]:
        """Everything _call_impl sends to litellm except the API key. One
        builder for both the real call and the card-#141 request fingerprint,
        so the fingerprint cannot drift from what is actually sent."""
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": min(max_output_tokens, self._output_cap),
        }
        if self.api_base:
            kwargs["api_base"] = self.api_base
        extra = _extra_body_for(self.model)
        if extra:
            if self.provider == "gemini":
                # litellm mishandles extra_body for gemini/: 1.74.0 DROPS
                # it, 1.84.0 puts it at the request ROOT where Google ignores
                # generation settings (request capture 2026-09-01, task #71).
                # GenerationConfig fields DO pass through as top-level kwargs
                # — keep this path (MODEL_HOSTING_LANDSCAPE §6).
                kwargs.update(extra)
            else:
                kwargs["extra_body"] = extra
        return kwargs

    def _call_impl(self, prompt: str, temperature: float = 0.1, max_output_tokens: int = 8000) -> Optional[str]:
        """Call the model; return response text or None. Retries on transient/rate
        errors. The requested cap is clamped to the model's output ceiling, and a
        response cut off at the cap (finish_reason == "length") retries with a
        doubled cap — a silently truncated batched-JSON answer parses to None
        downstream and looks like "the model found nothing" (the 0-edge bug
        class), which is worse than paying one more call."""
        kwargs = self._request_body(prompt, temperature, max_output_tokens)
        key_count = len(self._api_keys)
        key_idx = 0
        if key_count:
            if key_count > 1:
                self._key_rr += 1
                key_idx = self._key_rr % key_count
            kwargs["api_key"] = self._api_keys[key_idx]

        def _rotate_key() -> None:
            nonlocal key_idx
            key_idx = (key_idx + 1) % key_count
            kwargs["api_key"] = self._api_keys[key_idx]

        max_retries = 3
        attempt = 0
        rate_waits = 0
        server_waits = 0
        tried_keys = {key_idx}
        while attempt < max_retries:
            try:
                resp = self._completion(**kwargs)
                _record_usage(self.model, resp)
                choice = resp.choices[0] if resp and resp.choices else None
                content = choice.message.content if choice else None
                finish = getattr(choice, "finish_reason", None) if choice else None
                if finish == "length":
                    if kwargs["max_tokens"] < self._output_cap and attempt < max_retries - 1:
                        kwargs["max_tokens"] = min(kwargs["max_tokens"] * 2, self._output_cap)
                        logger.warning(f"LLM output truncated (finish_reason=length); "
                                       f"retrying with max_tokens={kwargs['max_tokens']}")
                        attempt += 1
                        continue
                    logger.warning(f"LLM output truncated at max_tokens={kwargs['max_tokens']} "
                                   f"with no retry headroom; returning the truncated text")
                if content:
                    return content.strip()
                logger.error("LLM returned empty content")
                return None
            except Exception as e:
                msg = str(e).lower()
                # Rate/quota FIRST: litellm can wrap a 429 in a BadRequestError
                # (Google's free tier does exactly this — RESOURCE_EXHAUSTED with
                # code 429 arrives as litellm.BadRequestError), and the
                # non-retryable branch below would then abandon the claim instead
                # of pacing it. Measured 2026-08-02: 4 old-100 claims died that way
                # across two retry rounds. Substrings are precise on purpose —
                # a bare "rate" also matches "generate_content" in Google's own
                # quota-metric names and URLs.
                is_rate = any(k in msg for k in ("rate limit", "rate_limit", "ratelimit",
                                                 "rate-limit", "quota", "resource_exhausted",
                                                 "429", "overloaded", "529"))
                # Don't retry errors that won't fix themselves (auth, bad model/request).
                if not is_rate and any(k in msg for k in (
                        "auth", "api_key", "api key", "not found",
                        "invalid", "permission", "badrequest", "bad request")):
                    logger.error(f"LLM call failed (non-retryable): {e}")
                    return None
                if is_rate and key_count > 1 and len(tried_keys) < key_count:
                    # Another account's quota is untouched this call — switch to
                    # it almost immediately instead of waiting out the throttle.
                    _rotate_key()
                    tried_keys.add(key_idx)
                    logger.warning(f"Rate-limited; switching to Google API key "
                                   f"#{key_idx + 1}/{key_count} and retrying in 5s")
                    time.sleep(5)
                    continue        # a fresh quota — doesn't consume an attempt
                if is_rate and self._patient_rate and rate_waits < _MAX_RATE_WAITS:
                    # Free-tier-only model: throttling is the pacing, keep waiting.
                    rate_waits += 1
                    if key_count > 1:
                        _rotate_key()
                    wait = 35 if key_count > 1 else 65
                    logger.warning(f"Rate-limited (free-tier pacing, wait "
                                   f"{rate_waits}/{_MAX_RATE_WAITS}): retrying in {wait}s")
                    time.sleep(wait)
                    continue        # pacing — doesn't consume an attempt
                is_server = (not is_rate) and any(k in msg for k in _SERVER_ERROR_MARKERS)
                if is_server and self._patient_rate and server_waits < _MAX_SERVER_WAITS:
                    # Free-tier-only model: a Google-side 5xx wave passes in
                    # minutes; waiting keeps the claim instead of minting a
                    # false 'unsupported' from a dead call.
                    server_waits += 1
                    if key_count > 1:
                        _rotate_key()
                    logger.warning(f"Server error from the free service (wait "
                                   f"{server_waits}/{_MAX_SERVER_WAITS}): retrying in 60s: "
                                   f"{str(e)[:160]}")
                    time.sleep(60)
                    continue        # pacing — doesn't consume an attempt
                attempt += 1
                if attempt < max_retries:
                    wait = 65 if is_rate else 2 ** (attempt - 1)
                    logger.warning(f"LLM call failed (attempt {attempt}/{max_retries}): {e}. "
                                   f"Retrying in {wait}s")
                    time.sleep(wait)
                    continue
                logger.error(f"LLM call failed after {max_retries} attempts: {e}")
                return None
        return None

    def call_json(self, prompt: str, temperature: float = 0.1, max_output_tokens: int = 8000,
                  purpose: str = "untagged", claim_id: Optional[str] = None) -> Optional[Any]:
        """Call the model and parse the response as JSON (tolerant of code fences)."""
        raw = self.call(prompt, temperature=temperature, max_output_tokens=max_output_tokens,
                        purpose=purpose, claim_id=claim_id)
        return extract_json(raw) if raw is not None else None


def parallel_map(fn, items, workers: int = 1) -> list:
    """
    Ordered map over I/O-bound work (LLM calls). workers<=1 -> plain loop,
    identical behavior. Threads are safe here: litellm completion is stateless
    and the work is network-bound, so the GIL doesn't matter.
    """
    items = list(items)
    if workers <= 1 or len(items) <= 1:
        return [fn(x) for x in items]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as ex:
        return list(ex.map(fn, items))


def extract_json(text: str) -> Optional[Any]:
    """Best-effort JSON extraction from an LLM response (handles ```json fences)."""
    if not text:
        return None
    candidates = []
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidates.extend(fenced)
    candidates.append(text)
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end != -1 and end > start:
            candidates.append(text[start:end + 1])
    for c in candidates:
        try:
            return json.loads(c.strip())
        except Exception:
            continue
    logger.warning("Failed to parse JSON from LLM response")
    return None
