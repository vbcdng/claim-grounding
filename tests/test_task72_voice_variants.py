"""Task #72: the two judge-prompt variants must be the SHIPPED prompts plus exactly
one inserted voice passage, so the gate arm measures one variable and a later
promotion copies nothing else. Also guards the placeholders and the date-rule
anchor the matcher relies on."""
import os

import pytest

from modules.papertrail import matcher

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAIRS = [
    ("pt_combined_judgment_prompt.txt", "pt_task72_combined_judgment.txt",
     "leaves the other unsupported."),
    ("pt_support_judgment_prompt.txt", "pt_task72_support_judgment.txt",
     "does not support the claim."),
]
START = "The writer's voice can also sit INSIDE a single citing sentence"


def _read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("shipped,variant,end", PAIRS)
def test_variant_is_shipped_plus_one_passage(shipped, variant, end):
    base = _read("config", "prompts", shipped)
    var = _read("benchmarks", "prompt_variants", variant)
    i = var.index(START)
    j = var.index(end, i) + len(end) + 1       # the passage + its trailing space
    assert var[:i] + var[j:] == base
    passage = var[i:j]
    assert "interest or attention" in passage
    assert "ABOUT THE SUBJECT ITSELF" in passage
    # round 2 (paid gate 72r1, t37): the voice exception must not loosen the
    # fact it wraps — every component of the wrapped assertion stays checked
    assert "never loosens the fact it wraps" in passage


@pytest.mark.parametrize("shipped,variant,end", PAIRS)
def test_variant_keeps_placeholders_and_anchor(shipped, variant, end):
    var = _read("benchmarks", "prompt_variants", variant)
    assert "{CLAIM}" in var and "{PASSAGE}" in var
    assert matcher._DATE_RULE_ANCHOR in var
