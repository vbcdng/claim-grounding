"""Card 166: the public copy ships without the HHEM check and without the new
checking path (modules/papertrail/aida/). Both must then cost one plain
sentence, never an import error. The full-tree proof is card 166's simulated
public tree (benchmarks/card166/card166_simtree.py); these tests pin the pieces.
"""
import argparse
import importlib.util
import logging
import os
import re

import pytest

import verify_my_text as vmt
from modules.papertrail import viewer, viewer_v2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _args(**kw):
    base = {s: (None if s == "aida_converter_prompt" else False) for s in vmt.AIDA_SWITCHES}
    base.update(kw)
    return argparse.Namespace(**base)


def test_optional_module_absent_and_present():
    assert vmt._optional_module("modules.papertrail.no_such_module_card166") is None
    assert vmt._optional_module("modules.papertrail.viewer") is viewer


def test_private_copy_still_has_both_parts():
    # The private copy keeps both; the switches behave exactly as before.
    if vmt.hhem_check is None or not vmt.aida_available():
        pytest.skip("public copy: HHEM and the new checking path are left out")
    a = _args(aida_grounder=True, aida_field_tools=True)
    assert vmt.drop_absent_aida(a) is False
    assert a.aida_grounder and a.aida_field_tools


def test_aida_switch_without_the_path_prints_one_sentence(monkeypatch, caplog):
    monkeypatch.setattr(vmt, "aida_available", lambda: False)
    a = _args(aida_grounder=True, aida_converter_prompt="x.txt", aida_reconvert=True)
    with caplog.at_level(logging.INFO, logger="verify_my_text"):
        assert vmt.drop_absent_aida(a) is True
    notes = [r.getMessage() for r in caplog.records]
    assert notes == [vmt.AIDA_ABSENT_NOTE]
    assert a.aida_grounder is False and a.aida_converter_prompt is None
    assert a.aida_reconvert is False


def test_no_aida_switch_no_note(monkeypatch, caplog):
    monkeypatch.setattr(vmt, "aida_available", lambda: False)
    with caplog.at_level(logging.INFO, logger="verify_my_text"):
        assert vmt.drop_absent_aida(_args()) is False
    assert not caplog.records


def test_every_aida_switch_is_listed():
    # A new --aida-* switch added later must join AIDA_SWITCHES, or the public
    # copy would let it through to an import of the private package.
    src = open(os.path.join(ROOT, "verify_my_text.py"), encoding="utf-8").read()
    dests = {m.replace("-", "_") for m in re.findall(r'add_argument\("--(aida-[a-z-]+)"', src)}
    assert dests == set(vmt.AIDA_SWITCHES)


def _no_hhem(monkeypatch):
    real = importlib.util.find_spec

    def fake(name, *a, **k):
        if name == "modules.papertrail.hhem_check":
            return None
        return real(name, *a, **k)
    monkeypatch.setattr(importlib.util, "find_spec", fake)


def _legend_rows(module):
    src = open(module.__file__, encoding="utf-8").read()
    return viewer._HHEM_LEGEND_RE.findall(src)


def test_each_viewer_has_exactly_one_hhem_legend_row():
    # Guards the pattern: if a legend rewrite stops matching, the public copy
    # would silently keep explaining a check it does not have.
    assert len(_legend_rows(viewer)) == 1
    assert len(_legend_rows(viewer_v2)) == 1


def test_legend_row_replaced_when_hhem_absent(monkeypatch):
    page = "<p>a</p>" + _legend_rows(viewer)[0] + "<p>b</p>"
    page2 = _legend_rows(viewer_v2)[0]
    analysis = {"text_claims": [{"id": "c1", "verdict": "unsupported"}]}
    if vmt.hhem_check is not None:
        assert viewer.hhem_legend(page, analysis) == page      # private copy: unchanged
    _no_hhem(monkeypatch)
    out = viewer.hhem_legend(page, analysis)
    assert out == "<p>a</p>" + viewer.HHEM_ABSENT_LEGEND + "<p>b</p>"
    assert viewer.hhem_legend(page2, analysis) == viewer.HHEM_ABSENT_LEGEND


def test_legend_row_kept_when_a_claim_carries_an_hhem_answer(monkeypatch):
    _no_hhem(monkeypatch)
    page = _legend_rows(viewer)[0]
    analysis = {"text_claims": [{"id": "c1", "hhem_check": {"score": 0.3}}]}
    assert viewer.hhem_legend(page, analysis) == page
