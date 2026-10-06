"""Offline tests for the adjustable per-call CLI timeout (card #88, 2026-09-12).

No `claude` binary and no API call: cli_timeout_s() only reads the environment.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail.claude_code_backend import cli_timeout_s  # noqa: E402

ENV = "PAPERTRAIL_CLAUDE_CLI_TIMEOUT_S"


def test_unset_gives_the_default(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    assert cli_timeout_s() == 240


def test_number_is_used(monkeypatch):
    monkeypatch.setenv(ENV, "900")
    assert cli_timeout_s() == 900


def test_non_number_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv(ENV, "abc")
    assert cli_timeout_s() == 240
