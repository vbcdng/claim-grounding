"""
Claude-Code backend (Stream E): LLM calls dispatched to the local `claude` CLI in
headless mode (`claude -p --model haiku`) instead of a paid API — $0 marginal cost
on a Claude subscription. This is what makes development/iteration testing free
(the "Haiku-first" policy); the SHIP-GATE backend stays Gemini for reproducibility.

Selection — either spelling reaches this class through LLMClient's __new__ dispatch:
  verify_my_text.py --backend claude-code            (canonicalizes the model)
  --model claude-code/haiku                          (any client, incl. --second-opinion)

Judging quality: validated for the combined-judgment task in
docs/HAIKU_VS_GEMINI_JUDGE.md (0 false positives in every condition; parity with
flash-lite needs rich evidence + the tuned rubric, which verify_my_text installs
via matcher.PROMPT_OVERRIDES). Decomposition/extraction through this backend is
UNVALIDATED — fine for dev runs, not for baselines.

The CLI's agentic surface is disabled as far as headless flags allow: one turn,
no MCP servers, a neutral cwd so no project CLAUDE.md is loaded into judgments.
Temperature / max_output_tokens are not controllable through the CLI; they are
accepted for interface parity and ignored.
"""

import logging
import random
import shutil
import subprocess
import tempfile
import time
from typing import Optional

from .llm_client import LLMClient

logger = logging.getLogger(__name__)

DEFAULT_CLI_MODEL = "haiku"     # cheapest; the judge study's subject
_TIMEOUT_S = 240                # per call; the CLI adds ~seconds of startup overhead
_MAX_RETRIES = 3                # attempts for a GENERIC failure (short 2^n backoff)

# The local CLI shares one Claude subscription across the whole --concurrency
# fan-out, so a high fan-out trips a rate/concurrency ceiling: the burst comes
# back as rc!=0 with EMPTY stdout+stderr (or a transient rate-limit message).
# That is retryable, but the generic 1s/2s backoff is far too short — the whole
# burst wakes together and hammers again, so the retries fail and claims get
# falsely marked unsupported (a silently polluted run). Give the throttle case
# its own longer, jittered, more-patient backoff. (Walkthrough #1 / P2.1.)
_THROTTLE_MAX_RETRIES = 6       # a rate-ceiling burst gets more patience than a generic fail
_THROTTLE_BASE_S = 4            # 4, 8, 16, 32, 60 (capped) — plus jitter, spread the herd
_THROTTLE_CAP_S = 60
_THROTTLE_HINTS = ("rate limit", "rate_limit", "429", "too many requests",
                   "overloaded", "try again")

# The subscription-shared CLI can't take the same fan-out as a paid API tier.
# verify_my_text.py clamps --concurrency to this for the claude-code backend.
RECOMMENDED_MAX_CONCURRENCY = 6


def canonical_model(model: Optional[str]) -> str:
    """'claude-code', 'claude-code/', 'claude-code/haiku', bare 'haiku' -> 'claude-code/<alias>'."""
    tail = ""
    if model:
        s = str(model).strip()
        tail = s.split("/", 1)[1] if s.startswith("claude-code") and "/" in s else \
            ("" if s == "claude-code" else s.split("/")[-1])
    return f"claude-code/{tail or DEFAULT_CLI_MODEL}"


class ClaudeCodeClient(LLMClient):
    """LLMClient-compatible (.call/.call_json) wrapper around `claude -p`."""

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None,
                 api_base: Optional[str] = None):
        # Deliberately NOT calling super().__init__ — no litellm, no key resolution.
        self.model = canonical_model(model)
        self.cli_model = self.model.split("/", 1)[1]
        self.provider = "claude-code"
        self.api_key = None                 # auth = the CLI's own login
        self.api_base = None
        self.cli = shutil.which("claude")
        if not self.cli:
            raise RuntimeError(
                "claude-code backend needs the `claude` CLI on PATH "
                "(install Claude Code, run `claude` once to log in), "
                "or use --backend api / a provider --model instead.")
        # Neutral cwd: run judgments from an empty temp dir so the CLI never loads
        # a repo's CLAUDE.md/context into every call (tokens + interference).
        self._cwd = tempfile.mkdtemp(prefix="pt-claude-code-")
        logger.info(f"LLM backend: {self.model} (local claude CLI, $0 API spend)")

    # The base class's call() wraps this (it counts failed calls there).
    def _call_impl(self, prompt: str, temperature: float = 0.1,
                   max_output_tokens: int = 8000) -> Optional[str]:
        cmd = [self.cli, "-p", "--model", self.cli_model, "--output-format", "text",
               "--strict-mcp-config", "--max-turns", "1"]
        # Two independent budgets: generic failures burn `gen`, throttle bursts burn
        # `thr`. A throttle-heavy run isn't cut short by the small generic budget, and
        # a genuinely broken call still gives up fast. Timeouts count as generic.
        gen = thr = 0
        while gen < _MAX_RETRIES and thr < _THROTTLE_MAX_RETRIES:
            try:
                out = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                     timeout=_TIMEOUT_S, cwd=self._cwd)
            except subprocess.TimeoutExpired:
                gen += 1
                logger.warning(f"claude CLI timed out after {_TIMEOUT_S}s "
                               f"(generic attempt {gen}/{_MAX_RETRIES})")
                if gen < _MAX_RETRIES:
                    time.sleep(2 ** (gen - 1))
                continue
            except Exception as e:
                logger.error(f"claude CLI could not be run: {e}")
                return None
            text = (out.stdout or "").strip()
            if out.returncode == 0 and text:
                return text
            err = (out.stderr or "").strip().splitlines()
            err_line = err[-1][:200] if err else ""
            # Auth / invalid-model / usage errors won't fix themselves — fail
            # fast instead of sleeping through retries (multiplied across the
            # --concurrency fan-out). Mirrors llm_client's retry policy.
            lowered = f"{err_line} {text[:200]}".lower()
            if any(s in lowered for s in ("log in", "login", "logged in",
                                          "authentication", "api key",
                                          "unknown model", "invalid model",
                                          "usage:", "unknown option")):
                logger.error(f"claude CLI failed with a non-retryable error "
                             f"(rc={out.returncode}): {err_line or text[:200]}")
                return None
            # Throttle signature: rc!=0 with NO output at all (the subscription
            # rate/concurrency ceiling), or a transient rate-limit message on
            # either stream. Long jittered backoff so the burst doesn't re-collide.
            is_throttle = (out.returncode != 0 and not text and not err_line) \
                or any(s in lowered for s in _THROTTLE_HINTS)
            if is_throttle:
                thr += 1
                logger.warning(f"claude CLI rate/concurrency ceiling (rc={out.returncode}, "
                               f"throttle attempt {thr}/{_THROTTLE_MAX_RETRIES}) — "
                               f"backing off; lower --concurrency if this repeats")
                if thr < _THROTTLE_MAX_RETRIES:
                    backoff = min(_THROTTLE_BASE_S * (2 ** (thr - 1)), _THROTTLE_CAP_S)
                    time.sleep(backoff + random.uniform(0, backoff / 2))
                continue
            gen += 1
            logger.warning(f"claude CLI failed (rc={out.returncode}, "
                           f"generic attempt {gen}/{_MAX_RETRIES})"
                           + (f": {err_line}" if err_line else ""))
            if gen < _MAX_RETRIES:
                time.sleep(2 ** (gen - 1))
        logger.error(f"claude CLI failed after {gen} generic + {thr} throttle attempts")
        return None
