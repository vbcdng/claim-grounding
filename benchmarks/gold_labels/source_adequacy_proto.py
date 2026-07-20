#!/usr/bin/env python3
"""Prototype: deterministic source-adequacy precheck.
Flags 'present but a stub' sources (paywall preview, Wayback/nav boilerplate,
reference-list-only) so their claims become not_rulable instead of unsupported.
Test: known stubs must flag; known full papers must NOT."""
import re, sys, os

STRONG_MARKERS = [
    r"preview of subscription content",
    r"log in via an institution",
    r"subscribe and save",
    r"instant access to the full article pdf",
    r"access this article",
    r"collected by\s*\n?\s*organization: internet archive",
    r"this is a preview",
    r"sign in to (?:access|read|continue)",
    r"purchase (?:this )?(?:article|access)",
    r"to check access",
    r"buy print or ebook",
    r"published online by cambridge university press",
    r"has data issue:\s*(?:true|false)",   # Cambridge Core page scaffold
    r"render date:",
]
NAV_MARKERS = [
    r"join our mailing list",
    r"\bcaptures\b.*\babout this capture\b",
]

def content_chars(text):
    # strip our extractor's (meta data) lines and blank lines
    lines=[l for l in text.splitlines() if not l.strip().lower().startswith("(meta data)") and l.strip()]
    return sum(len(l) for l in lines), lines

def ref_ratio(lines):
    # fraction of lines that look like a bibliography entry: "Name, X. (YYYY)."
    if not lines: return 0.0
    refy=sum(1 for l in lines if re.search(r"\(\d{4}[a-z]?\)\.", l) or re.match(r"^[A-Z][a-z]+,\s+[A-Z]\.", l))
    return refy/len(lines)

def country_dropdown(text):
    # a run of concatenated country names (form dropdown scraped as one blob)
    return bool(re.search(r"(Cayman Islands|Central African Republic|Comoros).{0,40}(Chad|Chile|China|Congo)", text))

def assess(path):
    t=open(path, errors="ignore").read()
    low=t.lower()
    cc, lines = content_chars(t)
    reasons=[]
    for m in STRONG_MARKERS:
        if re.search(m, low): reasons.append(f"paywall/preview marker: '{m[:32]}'"); break
    for m in NAV_MARKERS:
        if re.search(m, low, re.S): reasons.append("nav/wayback boilerplate")
    if country_dropdown(t): reasons.append("scraped form dropdown (country list)")
    rr=ref_ratio(lines)
    if cc < 1500 and rr > 0.4: reasons.append(f"reference-list-only (refs {rr:.0%}, {cc} content chars)")
    if cc < 500: reasons.append(f"almost no body ({cc} content chars)")
    verdict = "STUB" if reasons else "OK"
    return verdict, cc, reasons

TESTS = [
    ("data/polisci_verification/sources/schneider1982.txt", "STUB"),
    ("data/polisci_verification/sources/simon1978.txt", "STUB"),
    ("data/newsys_wice_train1/sources/harpenden.txt", "STUB"),
    ("data/newsys_wice_train1/sources/hammurabihumanrightsorga.txt", "STUB"),
    # known-good full papers (must NOT flag)
    ("data/eggs/sources/rong2013.txt", "OK"),
    ("data/eggs/sources/shin2013.txt", "OK"),
    ("data/eggs/sources/zhong2019.txt", "OK"),
    ("data/eggs/sources/barnard2019.txt", "OK"),
]
print(f"{'file':52} {'expect':6} {'verdict':6} {'chars':7} reasons")
ok=0
for path, expect in TESTS:
    if not os.path.exists(path):
        print(f"{os.path.basename(path):52} {expect:6} MISSING"); continue
    v, cc, reasons = assess(path)
    good = v==expect
    ok+=good
    print(f"{os.path.basename(path):52} {expect:6} {v:6} {cc:7} {'OK' if good else 'XX'}  {'; '.join(reasons)}")
print(f"\n{ok}/{len(TESTS)} classified as expected")
