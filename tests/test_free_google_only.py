"""FREE_GOOGLE_ONLY money-lock mode (modules/papertrail/llm_client.py).

When the environment variable FREE_GOOGLE_ONLY is set (non-empty, not "0"):
  - _gemini_key_files() globs only config/google_api_key*_free.txt (the
    no-billing key files), instead of every config/google_api_key*.txt.
  - LLMClient.__init__ refuses any provider other than "gemini", and refuses
    an explicit --api-base (it could route to a paid service).
  - _resolve_api_keys refuses an explicit --api-key (no way to verify it is
    one of the no-billing keys) and refuses to fall back to an environment
    key when no free key file is found.
  - The $0 claude-code backend is unaffected: ClaudeCodeClient skips
    LLMClient.__init__ entirely, so none of the above checks run for it.

Fully offline: constructing an LLMClient never calls an API. Each test gets
its own tmp_path config directory (via PROJECT_ROOT monkeypatched) and starts
with FREE_GOOGLE_ONLY/GEMINI_API_KEY unset, so no state leaks between tests.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import llm_client as lc


@pytest.fixture(autouse=True)
def clean_env(tmp_path, monkeypatch):
    """Point PROJECT_ROOT at an empty tmp_path/config dir and strip the env
    vars this module reads, so every test starts from the same blank slate."""
    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.setattr(lc, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("FREE_GOOGLE_ONLY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return cfg


def _write_keys(cfg_dir, **files):
    """Write {filename: key value} pairs into cfg_dir."""
    for name, value in files.items():
        (cfg_dir / name).write_text(value)


def test_normal_mode_loads_all_distinct_keys_including_paid(clean_env):
    """Unchanged behavior: with the flag unset, every config/google_api_key*.txt
    file (paid and *_free.txt alike) contributes a distinct key."""
    _write_keys(
        clean_env,
        **{
            "google_api_key.txt": "PAIDKEY",
            "google_api_key2.txt": "FREEKEY2",
            "google_api_key2_free.txt": "FREEKEY2",  # same value, deduped
            "google_api_key3_free.txt": "FREEKEY3",
        },
    )
    c = lc.LLMClient("gemini/gemma-4-27b-it")
    assert set(c._api_keys) == {"PAIDKEY", "FREEKEY2", "FREEKEY3"}


def test_free_mode_loads_only_no_billing_keys(clean_env, monkeypatch):
    """FREE_GOOGLE_ONLY=1 restricts key loading to *_free.txt files; the paid
    key in google_api_key.txt is never picked up."""
    _write_keys(
        clean_env,
        **{
            "google_api_key.txt": "PAIDKEY",
            "google_api_key2.txt": "FREEKEY2",
            "google_api_key2_free.txt": "FREEKEY2",
            "google_api_key3_free.txt": "FREEKEY3",
        },
    )
    monkeypatch.setenv("FREE_GOOGLE_ONLY", "1")
    c = lc.LLMClient("gemini/gemma-4-27b-it")
    assert set(c._api_keys) == {"FREEKEY2", "FREEKEY3"}
    assert "PAIDKEY" not in c._api_keys


def test_free_mode_refuses_explicit_api_key(clean_env, monkeypatch):
    """An explicit --api-key can't be verified as a no-billing key, so it is
    refused outright in free-only mode."""
    _write_keys(clean_env, **{"google_api_key2_free.txt": "FREEKEY2"})
    monkeypatch.setenv("FREE_GOOGLE_ONLY", "1")
    with pytest.raises(RuntimeError):
        lc.LLMClient("gemini/gemma-4-27b-it", api_key="whatever")


@pytest.mark.parametrize(
    "model",
    [
        "deepseek/deepseek-v4-flash",
        "mistral/mistral-large-latest",
        "openrouter/qwen/qwen3",
        "openai/gpt-4o",
        "groq/llama-3.3-70b",
    ],
)
def test_free_mode_refuses_paid_providers(clean_env, monkeypatch, model):
    """Any non-Google provider is refused at construction time in free-only
    mode, regardless of whether it happens to be a cheap or free tier itself."""
    _write_keys(clean_env, **{"google_api_key2_free.txt": "FREEKEY2"})
    monkeypatch.setenv("FREE_GOOGLE_ONLY", "1")
    with pytest.raises(RuntimeError):
        lc.LLMClient(model)


def test_free_mode_refuses_api_base(clean_env, monkeypatch):
    """A custom --api-base could route calls to a paid service, so it is
    refused in free-only mode even for the gemini provider."""
    _write_keys(clean_env, **{"google_api_key2_free.txt": "FREEKEY2"})
    monkeypatch.setenv("FREE_GOOGLE_ONLY", "1")
    with pytest.raises(RuntimeError):
        lc.LLMClient("gemini/gemma-4-27b-it", api_base="http://x")


def test_free_mode_with_no_free_key_files_is_hard_error_no_env_fallback(
    clean_env, monkeypatch
):
    """With no *_free.txt files present, free-only mode raises rather than
    falling back to a GEMINI_API_KEY environment variable, since that key
    could be a paid one."""
    _write_keys(clean_env, **{"google_api_key.txt": "PAIDKEY"})
    monkeypatch.setenv("GEMINI_API_KEY", "ENVPAIDKEY")
    monkeypatch.setenv("FREE_GOOGLE_ONLY", "1")
    with pytest.raises(RuntimeError):
        lc.LLMClient("gemini/gemma-4-27b-it")


def test_free_mode_claude_code_backend_still_constructs(clean_env, monkeypatch):
    """The $0 claude-code backend bypasses LLMClient.__init__ entirely, so it
    is unaffected by the free-only checks (no key files needed at all)."""
    monkeypatch.setenv("FREE_GOOGLE_ONLY", "1")
    c = lc.LLMClient("claude-code")
    assert type(c).__name__ == "ClaudeCodeClient"


def test_free_mode_off_restores_normal_loading(clean_env):
    """With the flag unset (the default), loading falls back to normal
    behavior: every google_api_key*.txt file contributes its key, paid
    included."""
    _write_keys(
        clean_env,
        **{
            "google_api_key.txt": "PAIDKEY",
            "google_api_key2.txt": "FREEKEY2",
        },
    )
    c = lc.LLMClient("gemini/gemma-4-27b-it")
    assert set(c._api_keys) == {"PAIDKEY", "FREEKEY2"}
