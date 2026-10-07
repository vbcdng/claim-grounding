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
    key in google_api_key.txt is never picked up. (A non-Gemma model: since
    2026-09-29 Gemma rotates over every key, see the Gemma tests below.)"""
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
    c = lc.LLMClient("gemini/gemini-3.8-flash")
    assert set(c._api_keys) == {"FREEKEY2", "FREEKEY3"}
    assert "PAIDKEY" not in c._api_keys


_BOTH_ACCOUNTS = {
    "google_api_key.txt": "ACCOUNT1KEY",
    "google_api_key2.txt": "ACCOUNT2KEY",
    "google_api_key2_free.txt": "ACCOUNT2KEY",  # copy of key2: must be deduped
    "google_api_key3.txt": "BILLEDKEY3",
}


@pytest.mark.parametrize("model", ["gemini/gemma-4-31b-it", "gemma-4-31b-it",
                                   "gemini/gemma-4-26b-a4b-it"])
def test_free_mode_gemma_rotates_over_all_distinct_keys(clean_env, monkeypatch,
                                                        model):
    """Card 143: Gemma on Google direct is free on every key (no paid tier), so
    under FREE_GOOGLE_ONLY a Gemma model loads EVERY google_api_key*.txt, with
    duplicate key values dropped (key2 and key2_free hold the same key)."""
    _write_keys(clean_env, **_BOTH_ACCOUNTS)
    monkeypatch.setenv("FREE_GOOGLE_ONLY", "1")
    c = lc.LLMClient(model)
    assert c._api_keys == ["ACCOUNT1KEY", "ACCOUNT2KEY", "BILLEDKEY3"]


@pytest.mark.parametrize("model", ["gemini/gemini-3.8-flash",
                                   "gemini/gemini-2.5-flash-lite",
                                   "gemini/gemini-3.1-pro",
                                   # 'gemma' not directly after the provider
                                   "gemini/tuned-gemma-4-31b-it"])
def test_free_mode_non_gemma_google_model_still_only_free_keys(clean_env,
                                                               monkeypatch,
                                                               model):
    """Every non-Gemma Google model keeps today's lock: only *_free.txt."""
    _write_keys(clean_env, **_BOTH_ACCOUNTS)
    monkeypatch.setenv("FREE_GOOGLE_ONLY", "1")
    c = lc.LLMClient(model)
    assert c._api_keys == ["ACCOUNT2KEY"]


def test_free_mode_gemma_log_names_files_never_keys(clean_env, monkeypatch,
                                                    caplog):
    """The one info line names the key FILES used, never a key value."""
    _write_keys(clean_env, **_BOTH_ACCOUNTS)
    monkeypatch.setenv("FREE_GOOGLE_ONLY", "1")
    with caplog.at_level("DEBUG", logger=lc.logger.name):
        lc.LLMClient("gemini/gemma-4-31b-it")
        lc.LLMClient("gemini/gemini-3.8-flash")
    text = "\n".join(r.getMessage() for r in caplog.records)
    for key in set(_BOTH_ACCOUNTS.values()):
        assert key not in text
    assert ("rotating over all Google key files: google_api_key.txt, "
            "google_api_key2.txt, google_api_key3.txt") in text
    assert "restricted to no-billing files: google_api_key2_free.txt" in text


def test_free_mode_gemma_keeps_every_refusal(clean_env, monkeypatch):
    """Gemma's wider key list changes nothing else: explicit --api-key and
    --api-base stay refused, and with no key file at all there is still no
    environment-key fallback."""
    _write_keys(clean_env, **_BOTH_ACCOUNTS)
    monkeypatch.setenv("FREE_GOOGLE_ONLY", "1")
    with pytest.raises(RuntimeError):
        lc.LLMClient("gemini/gemma-4-31b-it", api_key="whatever")
    with pytest.raises(RuntimeError):
        lc.LLMClient("gemini/gemma-4-31b-it", api_base="http://x")
    for f in clean_env.iterdir():
        f.unlink()
    monkeypatch.setenv("GEMINI_API_KEY", "ENVPAIDKEY")
    with pytest.raises(RuntimeError):
        lc.LLMClient("gemini/gemma-4-31b-it")


def test_key_files_without_model_stay_strict_under_lock(clean_env, monkeypatch):
    """_gemini_key_files() with no model (benchmarks/conversion_key uses it for
    Gemini Flash) keeps returning only the *_free.txt files under the lock."""
    _write_keys(clean_env, **_BOTH_ACCOUNTS)
    monkeypatch.setenv("FREE_GOOGLE_ONLY", "1")
    names = [os.path.basename(p) for p in lc._gemini_key_files()]
    assert names == ["google_api_key2_free.txt"]
    names = [os.path.basename(p) for p in lc._gemini_key_files("gemini/gemma-4-31b-it")]
    assert names == ["google_api_key.txt", "google_api_key2.txt",
                     "google_api_key2_free.txt", "google_api_key3.txt"]


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
    could be a paid one. (A non-Gemma model: since 2026-09-29 a Gemma model
    may use the billed google_api_key.txt, because Gemma has no paid tier;
    Gemma's own no-file case is in test_free_mode_gemma_keeps_every_refusal.)"""
    _write_keys(clean_env, **{"google_api_key.txt": "PAIDKEY"})
    monkeypatch.setenv("GEMINI_API_KEY", "ENVPAIDKEY")
    monkeypatch.setenv("FREE_GOOGLE_ONLY", "1")
    with pytest.raises(RuntimeError):
        lc.LLMClient("gemini/gemini-3.8-flash")


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
