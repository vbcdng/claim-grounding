"""
Provider-agnostic LLM client for the papertrail tool, backed by litellm.

One interface (`.call` / `.call_json`) over any litellm-supported provider, selected by a
`provider/model` string:  gemini/gemini-2.5-flash-lite (default), openai/gpt-4o-mini,
anthropic/claude-sonnet-4-..., ollama/llama3, openrouter/...  Auth comes from --api-key
(a file path or a raw value) or the provider's env var (OPENAI_API_KEY, ANTHROPIC_API_KEY,
GEMINI_API_KEY, ...); for the Gemini default we fall back to config/google_api_key.txt.
"""

import os
import re
import json
import time
import logging
import threading
from typing import Optional, Any, Dict

logger = logging.getLogger(__name__)

# ---- Actual-usage ledger (owner ask, 2026-07-11: track what each run REALLY
# cost, not just the pre-run estimate). Process-wide, thread-safe; every real
# API call through LLMClient.call records its reported token usage + the
# litellm-computed cost. The claude-code backend bypasses this (own call
# path, $0 subscription). verify_my_text writes the summary into
# metadata.llm_usage and prints it at the end of the run.
_USAGE_LOCK = threading.Lock()
_USAGE: Dict[str, Dict[str, float]] = {}


def _record_usage(model: str, resp: Any) -> None:
    try:
        u = getattr(resp, "usage", None)
        pt = int(getattr(u, "prompt_tokens", 0) or 0)
        ct = int(getattr(u, "completion_tokens", 0) or 0)
    except Exception:
        pt = ct = 0
    cost = 0.0
    try:
        import litellm
        cost = float(litellm.completion_cost(completion_response=resp) or 0.0)
    except Exception:
        pass                        # unknown model pricing -> tokens still counted
    with _USAGE_LOCK:
        m = _USAGE.setdefault(model, {"calls": 0, "prompt_tokens": 0,
                                      "completion_tokens": 0, "cost_usd": 0.0})
        m["calls"] += 1
        m["prompt_tokens"] += pt
        m["completion_tokens"] += ct
        m["cost_usd"] += cost


def usage_summary() -> Dict[str, Dict[str, float]]:
    """Per-model actuals accumulated so far in this process:
    {model: {calls, prompt_tokens, completion_tokens, cost_usd}}."""
    with _USAGE_LOCK:
        snap = {k: dict(v) for k, v in _USAGE.items()}
    for v in snap.values():
        v["cost_usd"] = round(v["cost_usd"], 6)
    return snap

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_GEMINI_CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "gemini_config.json")
DEFAULT_GEMINI_KEY_PATH = os.path.join(PROJECT_ROOT, "config", "google_api_key.txt")

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
    return "gemini/gemini-2.5-flash-lite"


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
        self.api_base = api_base
        self.api_key = self._resolve_api_key(api_key)

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

    def _resolve_api_key(self, api_key: Optional[str]) -> Optional[str]:
        if api_key:
            # Accept either a path to a key file or a raw key value.
            if os.path.exists(api_key):
                with open(api_key, "r", encoding="utf-8") as f:
                    return f.read().strip()
            return api_key.strip()
        # No key given: for the Gemini default, fall back to the project key file.
        if self.provider == "gemini" and os.path.exists(DEFAULT_GEMINI_KEY_PATH):
            with open(DEFAULT_GEMINI_KEY_PATH, "r", encoding="utf-8") as f:
                key = f.read().strip()
                if key:
                    return key
        # Otherwise rely on the provider's env var (litellm reads it automatically).
        return None

    # Count of call() invocations that ended in None (retries exhausted / empty
    # content). Consumers snapshot it around a unit of work to tell "the model
    # said no" apart from "the model never answered" — a failed call must never
    # be indistinguishable from a genuine negative (rerun.py refuses to reuse
    # verdicts minted under failures; verify_my_text tallies them at run end).
    failed_calls = 0

    def call(self, prompt: str, temperature: float = 0.1, max_output_tokens: int = 8000) -> Optional[str]:
        out = self._call_impl(prompt, temperature=temperature,
                              max_output_tokens=max_output_tokens)
        if out is None:
            self.failed_calls += 1
        return out

    def _call_impl(self, prompt: str, temperature: float = 0.1, max_output_tokens: int = 8000) -> Optional[str]:
        """Call the model; return response text or None. Retries on transient/rate
        errors. The requested cap is clamped to the model's output ceiling, and a
        response cut off at the cap (finish_reason == "length") retries with a
        doubled cap — a silently truncated batched-JSON answer parses to None
        downstream and looks like "the model found nothing" (the 0-edge bug
        class), which is worse than paying one more call."""
        kwargs = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": min(max_output_tokens, self._output_cap),
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base

        max_retries = 3
        for attempt in range(max_retries):
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
                        continue
                    logger.warning(f"LLM output truncated at max_tokens={kwargs['max_tokens']} "
                                   f"with no retry headroom; returning the truncated text")
                if content:
                    return content.strip()
                logger.error("LLM returned empty content")
                return None
            except Exception as e:
                msg = str(e).lower()
                # Don't retry errors that won't fix themselves (auth, bad model/request).
                if any(k in msg for k in ("auth", "api_key", "api key", "not found",
                                          "invalid", "permission", "badrequest", "bad request")):
                    logger.error(f"LLM call failed (non-retryable): {e}")
                    return None
                is_rate = any(k in msg for k in ("rate", "quota", "429", "overloaded", "529"))
                if attempt < max_retries - 1:
                    wait = 65 if is_rate else 2 ** attempt
                    logger.warning(f"LLM call failed (attempt {attempt + 1}/{max_retries}): {e}. "
                                   f"Retrying in {wait}s")
                    time.sleep(wait)
                    continue
                logger.error(f"LLM call failed after {max_retries} attempts: {e}")
                return None
        return None

    def call_json(self, prompt: str, temperature: float = 0.1, max_output_tokens: int = 8000) -> Optional[Any]:
        """Call the model and parse the response as JSON (tolerant of code fences)."""
        raw = self.call(prompt, temperature=temperature, max_output_tokens=max_output_tokens)
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
