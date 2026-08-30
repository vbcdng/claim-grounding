#!/usr/bin/env python3
"""free_llm.py — one small file that lets ANY session on this machine call the free LLM APIs.

Standalone on purpose: standard library only (no litellm, no venv needed), so it works
from any project directory, not just claim-grounding. All five providers speak the
OpenAI-compatible chat format, so one HTTP helper covers them all.

Providers (task #42 survey, verified 2026-08-10/11):
  google      Gemma + Gemini free tier, two account keys with rotation on rate-limit
  groq        Llama 3.3 70B, ~100K tokens/day (about 25 large calls), no-training policy
  mistral     Mistral Large free tier — WARNING: trains on your text until the console
              toggle is switched off (Admin Console -> Privacy)
  zai         GLM Flash models, free forever, reported 1 concurrent request
  openrouter  1,000 free-model requests/day (the one-time $10 bump is active)

Usage:
  free_llm.py list                      show which providers have a key (no network)
  free_llm.py ask <provider> "prompt"   one call, prints the reply
  free_llm.py ask <provider> -m MODEL "prompt"
  free_llm.py ask <provider> --no-think "prompt"   ask the model NOT to reason internally
  free_llm.py smoke                     one tiny call to every provider with a key
  free_llm.py smoke groq mistral        ...or only the named ones

Thinking mode: several models reason internally before answering, which costs tokens
and time. --no-think sends each provider its own off-switch (verified 2026-08-11):
zai -> thinking:{type:disabled} + enable_thinking:false (docs vs live behavior conflict,
so both are sent); groq qwen -> reasoning_effort:none; groq gpt-oss -> cannot be
disabled, only minimized (low) and hidden (include_reasoning:false); google
gemini-2.5-flash/-lite -> reasoning_effort:none; gemini-2.5-pro -> CANNOT be disabled
(minimum budget 128 tokens); gemma -> thinking_config thinking_level MINIMAL (the
switch the tool's judge has used since 2026-08-02, live-verified on the native API;
its <thought> text is ALSO stripped from replies client-side as a backstop); mistral-large
never thinks and REJECTS reasoning fields, so nothing is sent. Plain llama models
don't think either.

From Python:  from free_llm import ask; print(ask("groq", "Say hi"))

Live-call rule still applies: `ask` and `smoke` send real requests — a session needs
the author's explicit go before running them. `list` is always safe.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Key search order per provider: env var, then each file path in order.
# ~/.config/free-llm/ lets other projects keep their own copies of the keys;
# the claim-grounding config dir is the fallback where the keys live today.
_SHARED_KEY_DIR = Path.home() / ".config" / "free-llm"
_CLAIM_GROUNDING_CONFIG = Path.home() / "Documents/python_projects/claim-grounding/config"


def _key_paths(*filenames):
    out = []
    for name in filenames:
        out.append(_SHARED_KEY_DIR / name)
        out.append(Path(__file__).resolve().parent / "config" / name)
        out.append(_CLAIM_GROUNDING_CONFIG / name)
    # drop duplicates, keep order
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


PROVIDERS = {
    "google": {
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "key_files": _key_paths("google_api_key.txt", "google_api_key2.txt"),
        "default_model": "gemma-4-31b-it",  # same model the tool's judge uses
        "note": "two keys = two accounts; on a rate-limit answer the next key is tried",
    },
    "groq": {
        "endpoint": "https://api.groq.com/openai/v1/chat/completions",
        "env": ["GROQ_API_KEY"],
        "key_files": _key_paths("groq_api_key.txt"),
        "default_model": "llama-3.3-70b-versatile",
        "note": "~100K tokens/day total — fine for small tasks, not full runs",
    },
    "mistral": {
        "endpoint": "https://api.mistral.ai/v1/chat/completions",
        "env": ["MISTRAL_API_KEY"],
        "key_files": _key_paths("mistral_api_key.txt"),
        "default_model": "mistral-large-latest",
        "note": "free tier TRAINS on prompts until the console privacy toggle is off",
    },
    "zai": {
        "endpoint": "https://api.z.ai/api/paas/v4/chat/completions",
        "env": ["ZAI_API_KEY"],
        "key_files": _key_paths("zai_api_key.txt"),
        "default_model": "glm-4.5-flash",
        "note": "free forever; reported limit of 1 request at a time",
    },
    "openrouter": {
        "endpoint": "https://openrouter.ai/api/v1/chat/completions",
        "env": ["OPENROUTER_API_KEY"],
        "key_files": _key_paths("openrouter_api_key.txt"),
        "default_model": "google/gemma-4-31b-it:free",  # same model as our judge — the task #31 hosting-comparison leg
        "note": "only ':free' models are free and the list changes without notice — "
                "on a 404, GET /api/v1/models and pick a current ':free' id",
    },
}


def resolve_keys(provider):
    """Return (list_of_keys, description_of_where_they_came_from)."""
    cfg = PROVIDERS[provider]
    for var in cfg["env"]:
        val = os.environ.get(var, "").strip()
        if val:
            return [val], f"environment variable {var}"
    keys, places = [], []
    for path in cfg["key_files"]:
        try:
            val = path.read_text().strip()
        except OSError:
            continue
        if val and val not in keys:
            keys.append(val)
            places.append(str(path))
    if keys:
        return keys, ", ".join(places)
    return [], "no key found"


# complete <thought>/<think> blocks some models emit before their real answer
_THOUGHT_RE = re.compile(r"<(thought|think)>.*?</\1>\s*", re.DOTALL)


def _no_think_fields(provider, model):
    """Request fields that turn off internal reasoning — per provider+model, verified 2026-08-11."""
    m = model.lower()
    if provider == "zai":
        # docs say thinking.type; live reports say only enable_thinking works — send both
        return {"thinking": {"type": "disabled"}, "enable_thinking": False}
    if provider == "groq":
        if "qwen" in m:
            return {"reasoning_effort": "none"}
        if "gpt-oss" in m:
            return {"reasoning_effort": "low", "include_reasoning": False}
        return {}
    if provider == "google":
        if "gemma" in m:
            # thinkingLevel MINIMAL is live-verified on Google's native API
            # (llm_client.py 2026-08-02). On the OpenAI-compatible endpoint the
            # google config must sit under a literal top-level "extra_body" key
            # (a bare "google" key = HTTP 400). Thought tags are stripped too.
            return {"extra_body": {"google": {"thinking_config": {"thinking_level": "MINIMAL"}}}}
        if "gemini" in m and "2.5-pro" not in m:
            return {"reasoning_effort": "none"}
        return {}  # 2.5-pro: thinking cannot be disabled (min budget 128)
    return {}  # mistral-large REJECTS reasoning fields (HTTP 422); llama etc. don't think


def chat(provider, messages, model=None, max_tokens=1024, temperature=0.0, timeout=120, no_think=False):
    """One chat call. Returns the full parsed response dict. Raises RuntimeError on failure."""
    if provider not in PROVIDERS:
        raise RuntimeError(f"unknown provider {provider!r}; known: {', '.join(PROVIDERS)}")
    cfg = PROVIDERS[provider]
    keys, _ = resolve_keys(provider)
    if not keys:
        raise RuntimeError(f"no API key found for {provider} (checked env {cfg['env']} and key files)")
    model = model or cfg["default_model"]
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if no_think:
        payload.update(_no_think_fields(provider, model))
    body = json.dumps(payload).encode()

    last_err = None
    for i, key in enumerate(keys):
        req = urllib.request.Request(
            cfg["endpoint"],
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                "User-Agent": "free_llm.py (claim-grounding helper)",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:500]
            except Exception:
                pass
            last_err = RuntimeError(f"{provider} answered HTTP {e.code}: {detail or e.reason}")
            if e.code == 429 and i + 1 < len(keys):
                time.sleep(5)  # same pacing idea as the tool's key rotation
                continue
            raise last_err from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise RuntimeError(f"{provider} request failed before an answer came back: {e}") from None
    raise last_err


def ask(provider, prompt, model=None, system=None, **kw):
    """One question in, the reply text out."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = chat(provider, messages, model=model, **kw)
    try:
        content = resp["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"{provider} sent back an answer in an unexpected shape: {json.dumps(resp)[:500]}")
    # gemma has no server-side thinking switch — remove its finished thought blocks
    return _THOUGHT_RE.sub("", content)


def _cmd_list():
    print("Provider    key found?  default model                              where the key came from")
    print("-" * 110)
    for name, cfg in PROVIDERS.items():
        keys, source = resolve_keys(name)
        found = f"yes ({len(keys)})" if keys else "NO"
        print(f"{name:<11} {found:<11} {cfg['default_model']:<42} {source}")
        print(f"{'':<11} note: {cfg['note']}")
    print()
    print("`list` makes no network requests. `ask` and `smoke` send real requests —")
    print("get the author's explicit go first (standing rule).")


def _cmd_smoke(only=None):
    targets = [p for p in PROVIDERS if (not only or p in only) and resolve_keys(p)[0]]
    skipped = [p for p in PROVIDERS if (not only or p in only) and not resolve_keys(p)[0]]
    if not targets:
        print("No provider has a key available — nothing to test.")
        return 1
    print(f"Sending one tiny test question to: {', '.join(targets)}")
    if skipped:
        print(f"Skipped (no key): {', '.join(skipped)}")
    failures = 0
    for name in targets:
        model = PROVIDERS[name]["default_model"]
        start = time.time()
        try:
            # generous max_tokens: some models (zai's GLM, gemma) spend tokens on
            # internal reasoning first and return empty text if capped too low
            reply = ask(name, "Reply with exactly the word OK and nothing else.", max_tokens=600)
            secs = time.time() - start
            print(f"  {name:<11} OK    {secs:5.1f}s  {model}  reply: {reply.strip()[:40]!r}")
        except RuntimeError as e:
            secs = time.time() - start
            failures += 1
            print(f"  {name:<11} FAIL  {secs:5.1f}s  {model}  {e}")
    print(f"\n{len(targets) - failures} of {len(targets)} providers answered.")
    return 0 if failures == 0 else 1


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    cmd = argv[1]
    if cmd == "list":
        _cmd_list()
        return 0
    if cmd == "smoke":
        return _cmd_smoke(only=set(argv[2:]) or None)
    if cmd == "ask":
        rest = argv[2:]
        model = None
        no_think = False
        if "--no-think" in rest:
            no_think = True
            rest.remove("--no-think")
        if "-m" in rest:
            i = rest.index("-m")
            model = rest[i + 1]
            rest = rest[:i] + rest[i + 2:]
        if len(rest) < 2:
            print("usage: free_llm.py ask <provider> [-m MODEL] [--no-think] \"prompt\"", file=sys.stderr)
            return 2
        print(ask(rest[0], " ".join(rest[1:]), model=model, no_think=no_think))
        return 0
    print(f"Unknown command {cmd!r}. Run with --help to see what this script can do.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
