#!/usr/bin/env python3
"""Prototype the Finding-D Part-1 numeric check against real eggs sources.
Validates: real figures present (no false alarm) vs mutated figures absent (would flag)."""
import re, pathlib
SRC = pathlib.Path(__file__).resolve().parents[2] / "data/eggs/sources"

def norm_tokens(text):
    # collect numeric tokens from source: raw numbers, and percent<->ratio friendly
    return text

def present(fig, text):
    """Is fig (a string like '1.42','42','29,615','0.99') numerically present in text?"""
    t = text.replace(",", "")
    variants = {fig, fig.replace(",", "")}
    # percent <-> ratio: 42% ~ 1.42 ; 54% ~ 1.54 (RR form)
    m = re.fullmatch(r"(\d+)%", fig)
    if m:
        p = int(m.group(1))
        variants |= {f"{p}", f"{p}.0", f"1.{p:02d}", f"1.{p}"}
    m = re.fullmatch(r"1\.(\d{2})", fig)  # RR 1.42 -> 42%
    if m:
        variants |= {f"{int(m.group(1))}%", m.group(1)}
    for v in variants:
        if re.search(r"(?<!\d)" + re.escape(v) + r"(?!\d)", t):
            return True, v
    return False, None

# (label, source, figure, expectation)
TESTS = [
    ("t17 real RR 0.99", "rong2013.txt", "0.99", "PRESENT"),
    ("t17 real RR 0.91", "rong2013.txt", "0.91", "PRESENT"),
    ("t22 real n=29,615", "zhong2019.txt", "29,615", "PRESENT"),
    ("t22 real HR 1.17", "zhong2019.txt", "1.17", "PRESENT"),
    ("t22 real 17.5y", "zhong2019.txt", "17.5", "PRESENT"),
    ("t29 real RR 1.42", "shin2013.txt", "1.42", "PRESENT"),
    ("t30 real RR 1.54", "rong2013.txt", "1.54", "PRESENT"),
    ("t29 MUT RR 1.22", "shin2013.txt", "1.22", "ABSENT(would-flag)"),
    ("t30 MUT RR 2.54", "rong2013.txt", "2.54", "ABSENT(would-flag)"),
    ("t22 MUT HR 1.70", "zhong2019.txt", "1.70", "ABSENT(would-flag)"),
]
print(f"{'test':22} {'fig':8} {'expect':20} {'result':10} match")
ok = 0
for label, src, fig, expect in TESTS:
    p = SRC / src
    if not p.exists():
        print(f"{label:22} {fig:8} {expect:20} NO-SOURCE"); continue
    found, via = present(fig, p.read_text(errors="ignore"))
    res = "PRESENT" if found else "ABSENT"
    good = (found and expect == "PRESENT") or (not found and expect.startswith("ABSENT"))
    ok += good
    print(f"{label:22} {fig:8} {expect:20} {res:10} {'OK' if good else 'XX'}  {('via '+via) if via else ''}")
print(f"\n{ok}/{len(TESTS)} behaved as the design predicts")
