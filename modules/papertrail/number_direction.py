"""Numeric + direction cross-check on supported claims (task #2).

Closes the dangerous false-"supported" class the mutation bench found
(docs/MUTATION_BENCH_PLAN.md): an edited figure riding inside otherwise-
confirmable prose (t29, 42% -> 22% MISSED) and a reversed mechanism verb (t8,
downregulating -> upregulating MISSED). Design + the three binding matcher
rules: docs/FINDING_D_NUMERIC_MECHANISM_CHECK_DESIGN.md (2026-07-17).

FLAG, NEVER A FLIP. Both parts are display-only: the verdict field, the
covering set and every gate scorer input stay untouched (coverage_check reads
only `verdict` + `covering`; regression_check reads only `verdict`).

Part 1 — numbers (deterministic, ZERO model calls, default ON)
    Extract only CONFIDENT digit-bearing figures from a supported cited claim
    (percent, decimal ratio, separator-bearing count, number+unit) and look
    each up in the full text of every source the claim cites. Verbal and
    approximate quantities ("roughly three-quarters"), bare small integers and
    bare years are exempt on purpose — they are the false-alarm source.
    The three rules the 2026-07-17 prototype validated at 10/10 and that are
    binding here:
      1. Match a figure only in its SPECIFIC form. A decimal ratio matches the
         whole "1.42" (dot, comma or middot); a percent matches "42 %". Never
         expand a ratio into a bare integer — that is what made the naive
         prototype accept the mutated "RR 1.22" (a stray "22" elsewhere in the
         source).
      2. Allow separator variants for counts: "29,615" also matches "29 615"
         and "29615" — the naive prototype false-alarmed on exactly this.
      3. Corroborate at the figure-CLUSTER level. "42% (RR 1.42)" is two forms
         of one author-given figure; sources usually print only one of them, so
         the cluster passes when EITHER form is present.
    Stored as c["number_check"]; a cluster with no member found anywhere in the
    cited sources renders an amber "figure not in cited source" chip.

Part 2 — direction (one tiny call per TRIGGERED claim, default OFF)
    Numbers cannot reach t8, a flipped verb. A deterministic trigger finds an
    increase/decrease cue ("upregulating") or a cause-and-effect connective
    ("leads to") in a supported cited claim; only those claims get one small
    question asked against the same windows the judge read: does the source
    state this direction, the opposite one, or neither? "reversed" or
    "not_stated" renders an amber "direction not confirmed in source" chip.
    Default OFF (--direction-check turns it on) because its false-alarm rate on
    the free judge model is not measured yet; task #18's lesson is that a
    strictness rule that helps its target row usually breaks a fair paraphrase,
    so this one waits for a gate arm before it becomes a default.

Why the number lookup uses the source's FULL text and not just the retrieved
window: a figure that IS in the source but fell outside the window would
otherwise raise a false alarm, and false-alarm control is the whole game here.
The 10/10 prototype was measured against the full source files for that reason.
The direction question does use the retrieved windows, because the question
being asked is whether what the judge actually read carried the direction.

Part 1b — where the figure was found (task #99, 2026-09-10, DEFAULT OFF)
    Looking anywhere in the paper leaves two holes. (1) The writer says the
    reuse rate was 23%; the paper says 43% for the reuse rate and prints 23%
    for something else entirely — today that passes. (2) "literacy stayed
    below roughly 20%" passes as soon as ANY percentage anywhere in the paper
    is under 20, even one about something unrelated. Both close by asking
    WHERE the figure sits relative to the passage the judge actually quoted.
    With scope on, each figure cluster records one of four places:
      "sentence"  the proof sentences themselves (the judge's evidence
                  sentence and the covering-set proof sentences)
      "window"    the retrieved window around them (a few sentences either
                  side), which is still the passage the claim was judged on
      "paragraph" within PARAGRAPH_CHARS of a quoted proof sentence in the
                  full source (task #113: retrieval quoted a weak sentence,
                  the paper backs the figure a few sentences away) -> no chip;
                  tried only when the quoted proof prints no figure of that
                  kind itself, and only on sentences sharing a topic word with
                  the claim's words around the figure
      "paper"     somewhere else in the cited source only -> the new, weaker
                  chip "figure found, but not in the quoted proof"
      "none"      nowhere in the cited sources -> today's amber chip
    A claim whose stored proof text is empty (nothing was quoted) records
    proof_shown=False and gets no scope chip: with no proof to compare
    against, silence is the honest answer.
    Task #113 (2026-09-24) added three repairs, all behind the same switch:
    a figure inside a capitalised name ("4 Year Liberal Studies Degree") is
    not extracted; a mass or length amount matches the source's amount in
    another unit ("10 kg" vs "23-pound", CONVERSIONS); and the "paragraph"
    place above.
    Scope is OFF by default (--figure-in-proof turns it on) until the author
    rules on the measured false-alarm rate; with it off the payload is
    byte-identical to the 2026-09-03 version.

No prompt file: the direction question lives in this module as
DIRECTION_PROMPT. Day-session rule 2026-09-03 forbids writing under
config/prompts/; moving it there (as pt_direction_check_v1.txt, loaded through
matcher._load_prompt like every other pass) is a one-line change the author can
approve, and the fingerprint recorded in the tag keeps caching honest either
way.
"""
import hashlib
import logging
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from .llm_client import extract_json, parallel_map

logger = logging.getLogger("papertrail.number_direction")

FIELD = "number_check"
DIR_FIELD = "direction_check"

# ---------------------------------------------------------------- part 1: numbers

# Units we treat as making a bare number a real quantity. Longest first so the
# alternation prefers "mg/day" over "mg".
UNITS = [
    "mg/dl", "mg/dL", "mg/day", "mg/d", "g/day", "g/d", "mmol/l", "mmol/L",
    "person-years", "person years", "kcal", "mmol", "µg", "mcg", "mg", "kg",
    "g/l", "ml", "dl", "years", "year", "yr", "months", "month", "weeks",
    "week", "days", "day", "hours", "hour", "fold", "servings", "serving",
    "eggs", "egg", "cm", "mm", "km",
]
_UNIT_ALT = "|".join(re.escape(u) for u in UNITS)

# A percent, in the claim or the source. Accepts "42%", "42 %", "42 percent",
# "42.5%" and the decimal comma some sources use.
_PERCENT_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d+)?)\s*(?:%|per\s?cent\b|percent\b)")
# A decimal ratio: hazard/relative/odds ratios, and any other decimal figure.
# Dot, comma and the Lancet-style middot all count as the decimal point.
# The lookahead lets a sentence-ending full stop or a comma follow the figure
# ("the odds ratio was 0.72." / "0.72, and") — review 2026-09-04: the old
# lookahead refused any following "." or ",", so a ratio at the end of a
# sentence was invisible in the claim and, worse, in the source, which produced
# a false "figure not in source" chip for a figure printed there verbatim.
_DECIMAL_RE = re.compile(r"(?<![\d.,·])(\d{1,4}[.,·]\d{1,3})(?![\d·%])(?![.,]\d)")
# A count written with thousands separators: comma, ASCII space, no-break space
# or narrow no-break space.
_COUNT_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[,   ]\d{3})+)(?!\d)")
# A number carrying a unit.
_UNITQ_RE = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*-?\s*(" + _UNIT_ALT + r")\b",
                       re.IGNORECASE)
# A number written with a scale word: "1.7 million participants". The writer's
# "1.7 million" and the source's "1 720 108" are the same figure, so the figure
# is worth its full value (1,700,000), matched with the rounding the writer's
# own last printed place allows. Reading it as the bare decimal 1.7 — what the
# check did before task #99 — let any stray "1.7" in the paper corroborate it.
SCALE_WORDS = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
_SCALE_RE = re.compile(r"(?<![\d.,])(\d{1,4}(?:[.,]\d{1,3})?)[\s\-–—]*("
                       + "|".join(SCALE_WORDS) + r")\b", re.IGNORECASE)
# Citation markers must not be mined for figures.
_MARKER_RE = re.compile(r"\[\[[^\]]*\]\]")
# "95% CI" is the NAME of a statistic, not a quantity the source has to print:
# every paper that reports one writes the words, and a claim that copies an
# interval correctly still gets no credit for the "95%" itself. Task #99 found
# this to be the single largest false-alarm class of the proof-scope check —
# 24 of the first 55 flags — so a percentage that introduces a confidence or
# credible interval is exempt, in the claim and everywhere else.
_CI_AFTER_RE = re.compile(r"^\W{0,3}(?:CI\b|C\.\s?I\.|confidence\s+(?:interval|limit)"
                          r"|credible\s+interval)", re.IGNORECASE)
# Wording that says the figure is deliberately rounded. "Examining nearly 1,800
# policy questions" is a fair report of a source that counted 1,779, so an
# approximate figure gets a wider rounding allowance than an exact one.
APPROX_WORDS = [
    "about", "approximately", "approx", "around", "roughly", "nearly",
    "almost", "close to", "circa", "some", "just over", "just under", "~",
]
_APPROX_RE = re.compile("(?:" + "|".join(re.escape(w) for w in APPROX_WORDS)
                        + r")\W*$", re.IGNORECASE)
# Wording that makes the figure a limit rather than a measurement. "Literacy
# stayed below roughly 20%" is a fair report of a source that measured 16%: any
# source figure on the correct side of the limit backs the claim, so the check
# stays silent unless EVERY figure in the source sits on the wrong side.
BOUND_BELOW_WORDS = ["below", "under", "less than", "fewer than", "no more than",
                     "at most", "up to", "beneath", "shy of"]
BOUND_ABOVE_WORDS = ["above", "over", "more than", "at least", "greater than",
                     "in excess of", "upwards of", "exceeding", "exceeds",
                     "exceeded", "north of"]
_BELOW_RE = re.compile("(?:" + "|".join(re.escape(w) for w in BOUND_BELOW_WORDS)
                       + r")\b[^.]{0,25}$", re.IGNORECASE)
_ABOVE_RE = re.compile("(?:" + "|".join(re.escape(w) for w in BOUND_ABOVE_WORDS)
                       + r")\b[^.]{0,25}$", re.IGNORECASE)

# Two forms of one author-given figure count as the same figure when they agree
# to within this much: "42% higher" and "RR 1.42" differ by 42/100 exactly, so
# a small slack is all that is needed.
CLUSTER_SLACK = 0.006

# When the exact written form is not in the source, also accept a same-kind
# figure whose VALUE is close enough to be the same figure rounded. The
# allowance is the SMALLER of one unit in the claim figure's last decimal place
# and two per cent of its value: "42%" accepts a source's "41.8%", but "6%"
# does not accept a source's "6.36%" (measured on the eggs run — that was a
# coincidental match to an unrelated risk difference).
VALUE_TOLERANCE = True
TOLERANCE_RELATIVE = 0.02
# An approximate figure ("nearly 1,800") gets a five-per-cent allowance instead.
APPROX_RELATIVE = 0.05


def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def _to_float(raw: str) -> Optional[float]:
    """'1,42' / '1·42' / '29 615' -> float. None when it is not a number."""
    t = raw.strip().replace(" ", "").replace(" ", "").replace(" ", "")
    t = t.replace("·", ".")
    if t.count(",") == 1 and "." not in t and len(t.split(",")[1]) != 3:
        t = t.replace(",", ".")          # decimal comma
    else:
        t = t.replace(",", "")           # thousands separator
    try:
        return float(t)
    except ValueError:
        return None


def _places(raw: str) -> int:
    """How many digits the figure prints after its decimal point."""
    t = raw.replace("·", ".")
    if t.count(",") == 1 and "." not in t and len(t.split(",")[1]) != 3:
        t = t.replace(",", ".")
    return len(t.split(".")[-1]) if "." in t else 0


def _tolerance(raw: str, value: float, approximate: bool = False) -> float:
    """How far a source figure may sit from the claim's figure and still be the
    same number. Exact figure: the smaller of one unit in the last printed
    decimal place and two per cent of the value. Approximate figure: five per
    cent of the value, never less than the last printed place."""
    last_place = 10.0 ** -_places(raw) if _places(raw) else 1.0
    v = abs(value)
    if approximate:
        return max(APPROX_RELATIVE * v, last_place)
    if v == 0:
        return last_place
    return min(last_place, TOLERANCE_RELATIVE * v)


def _fmt(value: float, places: int) -> List[str]:
    """The written forms of a number: fixed decimals, and the same with
    trailing zeros dropped ('1.70' also prints as '1.7')."""
    a = f"{value:.{places}f}"
    out = [a]
    if "." in a:
        b = a.rstrip("0").rstrip(".")
        if b and b != a:
            out.append(b)
    return out


def cross_forms(fig: Dict[str, Any]) -> List[Tuple[float, str, int]]:
    """The OTHER written form of the same author-given figure, as
    (value, kind, decimal places).

    A writer routinely turns a source's ratio into a percentage: the source
    prints "1.69", the writer says "a 69% higher risk". Both are the same
    figure, so finding either one in the source corroborates the claim. This
    conversion is restricted to the "X per cent higher / lower" idiom — one
    plus or one minus the fraction. The bare-proportion reading (69% as 0.69)
    is deliberately NOT included: measured against the mutation bench it made
    the corrupted "154% higher" match the source's real "1.54" and so hid a
    planted error, which is exactly the failure this whole check exists to
    prevent."""
    v, num, kind = fig["value"], fig["number"], fig["kind"]
    dec = _places(num)
    out: List[Tuple[float, str, int]] = []
    if kind == "percent":
        for cand in (1.0 + v / 100.0, 1.0 - v / 100.0):
            if cand > 0:
                out.append((round(cand, dec + 4), "decimal", dec + 2))
    elif kind == "decimal":
        for cand in ((v - 1.0) * 100.0, (1.0 - v) * 100.0):
            if cand > 0:
                out.append((round(cand, dec + 2), "percent", max(0, dec - 2)))
    return out


# Task #113 fix 1 (figure-in-proof only): a figure followed straight away by two
# capitalised words is part of a proper name, not a measurement — "4 Year
# Liberal Studies Degree", "3 and 4 year History Degrees". Two words, not one,
# so "12 months The" can only arise with no full stop between, which prose does
# not produce. Percentages are never skipped: "42% Higher Risk" in a title-case
# heading is still a figure.
_NAME_AFTER_RE = re.compile(r"^[ \t\-]+[A-Z][a-z]+[ \t]+[A-Z][a-z]+")
_NAME_KINDS = ("unit", "count", "decimal")


def extract_figures(claim_text: str, skip_names: bool = False) -> List[Dict[str, Any]]:
    """Confident digit-bearing figures in a claim, each as
    {raw, kind, value, unit}. Overlapping matches are resolved by kind
    priority: a percent beats a decimal, a unit quantity beats a bare count.
    Verbal quantities, bare integers and bare years yield nothing.
    skip_names (task #113, on with --figure-in-proof) also drops a figure that
    sits inside a capitalised name."""
    text = _MARKER_RE.sub(" ", _nfkc(claim_text or ""))
    found: List[Dict[str, Any]] = []
    taken: List[Tuple[int, int]] = []

    def overlaps(a: int, b: int) -> bool:
        return any(not (b <= s or a >= e) for s, e in taken)

    # Order matters: the most specific reading of a span wins.
    for kind, rx in (("percent", _PERCENT_RE), ("scale", _SCALE_RE),
                     ("unit", _UNITQ_RE), ("count", _COUNT_RE),
                     ("decimal", _DECIMAL_RE)):
        for m in rx.finditer(text):
            if overlaps(m.start(), m.end()):
                continue
            num = m.group(1)
            val = _to_float(num)
            if val is None:
                continue
            if kind == "percent" and _CI_AFTER_RE.match(text[m.end():m.end() + 30]):
                continue                      # "95% CI" names a statistic
            if (skip_names and kind in _NAME_KINDS
                    and _NAME_AFTER_RE.match(text[m.end():m.end() + 60])):
                taken.append((m.start(), m.end()))
                continue                      # "4 Year Liberal Studies Degree"
            taken.append((m.start(), m.end()))
            before = text[max(0, m.start() - 34):m.start()]
            bound = ("below" if _BELOW_RE.search(before)
                     else "above" if _ABOVE_RE.search(before) else None)
            approximate = bool(_APPROX_RE.search(before))
            fig = {"raw": m.group(0).strip(), "number": num, "kind": kind,
                   "value": val, "bound": bound, "approximate": approximate,
                   "unit": (m.group(2).lower() if kind == "unit" else None)}
            if kind == "scale":
                # A scale word turns the figure into a count of its full value,
                # rounded as loosely as the writer's own last printed place.
                scale = SCALE_WORDS[m.group(2).lower()]
                fig["value"] = val * scale
                fig["number"] = f"{fig['value']:.0f}"
                fig["kind"] = "count"
                fig["scale_form"] = m.group(0).strip()
                last_place = (10.0 ** -_places(num)) * scale
                fig["tolerance"] = (max(APPROX_RELATIVE * fig["value"], last_place)
                                    if approximate
                                    else min(last_place,
                                             TOLERANCE_RELATIVE * fig["value"]))
            found.append(fig)
    found.sort(key=lambda f: f["raw"])
    return found


# Text pulled out of a PDF often breaks a figure apart: "100,000" arrives as
# "100 ,000" and "0.80" as "0 .80". Task #99 found two flags caused by exactly
# this, where the figure was sitting inside the sentence quoted as proof. Both
# repairs are narrow on purpose: the comma form needs a full three-digit group
# straight after the comma, and the dot form needs short digit runs on both
# sides, so a sentence boundary after a year ("in 2021 . 30 people") is left
# alone.
_SPACED_COMMA_RE = re.compile(r"(\d)\s+,(\d{3})(?!\d)")
_SPACED_DOT_RE = re.compile(r"(?<!\d)(\d{1,3})\s*\.\s+(\d{1,3})(?!\d)")
_SPACED_DOT2_RE = re.compile(r"(?<!\d)(\d{1,3})\s+\.\s*(\d{1,3})(?!\d)")
# Task #146: some journals print the decimal point as a raised middle dot, and
# extraction leaves a space before it ("1 ·37" for 1.37). Short digit runs on
# both sides, like the dot repairs above; a chemical formula ("CaSO4·2H2O") has
# a letter before the dot and is left alone.
_SPACED_MIDDOT_RE = re.compile(r"(?<![\d\w])(\d{1,3})\s*[·⋅∙]\s*(\d{1,3})(?!\d)")


def normalize_source(text: str) -> str:
    """Repair figures that PDF extraction pulled apart. Source text only — the
    claim is the author's own typing and is left exactly as written."""
    t = _nfkc(text or "")
    t = _SPACED_COMMA_RE.sub(r"\1,\2", t)
    t = _SPACED_DOT_RE.sub(r"\1.\2", t)
    t = _SPACED_DOT2_RE.sub(r"\1.\2", t)
    t = _SPACED_MIDDOT_RE.sub(r"\1.\2", t)
    return t


# Numbers a source spells out: "literacy stayed below twenty percent". The
# printing-press source does exactly this, and the claim writes "20%", so a
# digits-only comparison called a correct figure missing.
NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90, "hundred": 100,
}
_WORD_ALT = "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))
_WORD_PERCENT_RE = re.compile(r"\b(" + _WORD_ALT + r")(?:[\s-]+(" + _WORD_ALT
                              + r"))?\s*(?:%|per\s?cent\b|percent\b)", re.IGNORECASE)


def _word_percent_values(text: str) -> List[float]:
    out = []
    for m in _WORD_PERCENT_RE.finditer(text):
        a = NUMBER_WORDS[m.group(1).lower()]
        b = NUMBER_WORDS[m.group(2).lower()] if m.group(2) else None
        if b is None:
            out.append(float(a))
        elif b == 100:                      # "one hundred percent"
            out.append(float(a * 100))
        else:                               # "twenty-five percent"
            out.append(float(a + b))
    return out


def _unit_values(text: str) -> List[Tuple[float, str]]:
    out = []
    for m in _UNITQ_RE.finditer(text):
        v = _to_float(m.group(1))
        if v is not None:
            out.append((v, m.group(2).lower()))
    return out


# Task #113 fix 2 (figure-in-proof only): the same amount in another unit. The
# writer's "10 kg (23 lb)" rests on the source's "23-pound projectile"; no
# comparison of digits can see that. Each unit maps to (dimension, size in the
# dimension's base unit: kilograms for mass, metres for length). Only mass and
# length: those are the conversions writers make. The bare "in" and "m" are left
# out on purpose ("18 in 2019", "2 m participants"); the source's "18-inch" and
# "140-mile" are what occur.
CONVERSIONS = {
    "kg": ("mass", 1.0), "kilogram": ("mass", 1.0), "kilograms": ("mass", 1.0),
    "g": ("mass", 1e-3), "gram": ("mass", 1e-3), "grams": ("mass", 1e-3),
    "mg": ("mass", 1e-6), "milligram": ("mass", 1e-6), "milligrams": ("mass", 1e-6),
    "lb": ("mass", 0.45359237), "lbs": ("mass", 0.45359237),
    "pound": ("mass", 0.45359237), "pounds": ("mass", 0.45359237),
    "oz": ("mass", 0.028349523), "ounce": ("mass", 0.028349523),
    "ounces": ("mass", 0.028349523),
    "mm": ("length", 1e-3), "millimetre": ("length", 1e-3),
    "millimetres": ("length", 1e-3), "millimeter": ("length", 1e-3),
    "millimeters": ("length", 1e-3),
    "cm": ("length", 1e-2), "centimetre": ("length", 1e-2),
    "centimetres": ("length", 1e-2), "centimeter": ("length", 1e-2),
    "centimeters": ("length", 1e-2),
    "metre": ("length", 1.0), "metres": ("length", 1.0),
    "meter": ("length", 1.0), "meters": ("length", 1.0),
    "km": ("length", 1e3), "kilometre": ("length", 1e3),
    "kilometres": ("length", 1e3), "kilometer": ("length", 1e3),
    "kilometers": ("length", 1e3),
    "inch": ("length", 0.0254), "inches": ("length", 0.0254),
    "ft": ("length", 0.3048), "foot": ("length", 0.3048), "feet": ("length", 0.3048),
    "yard": ("length", 0.9144), "yards": ("length", 0.9144),
    "mile": ("length", 1609.344), "miles": ("length", 1609.344),
}
_CONV_ALT = "|".join(re.escape(u) for u in sorted(CONVERSIONS, key=len, reverse=True))
_CONV_RE = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*-?\s*(" + _CONV_ALT + r")\b",
                      re.IGNORECASE)
# Converting and rounding at once loses precision: "23 pounds" is 10.4 kg and
# the writer printed "10 kg". Five per cent, the allowance an approximate
# figure already gets, never less than half the claim's last printed place.
CONVERSION_RELATIVE = 0.05


def converted_present(fig: Dict[str, Any], text: str) -> bool:
    """True when the source prints the claim's unit amount in another unit of
    the same kind (mass or length), within the conversion allowance."""
    dim = CONVERSIONS.get((fig.get("unit") or "").lower())
    if not dim:
        return False
    target = fig["value"] * dim[1]              # the claim's amount, base unit
    places = _places(fig["number"])
    half_place = 0.5 * (10.0 ** -places) * dim[1]
    allow = max(CONVERSION_RELATIVE * abs(target), half_place)
    for m in _CONV_RE.finditer(text):
        other = CONVERSIONS.get(m.group(2).lower())
        v = _to_float(m.group(1))
        if not other or other[0] != dim[0] or v is None or other[1] == dim[1]:
            continue
        if abs(v * other[1] - target) <= allow:
            return True
    return False


def _scale_values(text: str) -> List[float]:
    """Counts the source writes with a scale word: "5.54 million person years"
    is 5,540,000, and is the same figure as a claim's "over 5.5 million"."""
    out = []
    for m in _SCALE_RE.finditer(text):
        v = _to_float(m.group(1))
        if v is not None:
            out.append(v * SCALE_WORDS[m.group(2).lower()])
    return out


def _count_values(text: str) -> List[float]:
    out = []
    for m in _COUNT_RE.finditer(text):
        v = _to_float(m.group(1))
        if v is not None:
            out.append(v)
    out.extend(_scale_values(text))
    return out


def _percent_values(text: str) -> List[float]:
    out = []
    for m in _PERCENT_RE.finditer(text):
        v = _to_float(m.group(1))
        if v is not None:
            out.append(v)
    out.extend(_word_percent_values(text))    # "below twenty percent"
    return out


def _decimal_values(text: str) -> List[float]:
    out = []
    for m in _DECIMAL_RE.finditer(text):
        v = _to_float(m.group(1))
        if v is not None:
            out.append(v)
    return out


def _percent_written(value: float, places: int, text: str) -> bool:
    for form in _fmt(value, places):
        body = re.escape(form).replace(r"\.", r"[.,·]")
        if re.search(r"(?<![\d.,])" + body + r"\s*(?:%|per\s?cent|percent)", text):
            return True
    return False


def _decimal_written(value: float, places: int, text: str) -> bool:
    for form in _fmt(value, places):
        body = re.escape(form).replace(r"\.", r"[.,·]")
        if re.search(r"(?<![\d.,·])" + body + r"(?![\d·])(?![.,]\d)", text):
            return True
    return False


def figure_present(fig: Dict[str, Any], source_text: str,
                   percents: Optional[List[float]] = None,
                   decimals: Optional[List[float]] = None,
                   counts: Optional[List[float]] = None,
                   units: Optional[List[Tuple[float, str]]] = None,
                   convert: bool = False
                   ) -> Tuple[bool, str]:
    """(found, how). Rule 1: the figure's own specific form only — a ratio is
    never expanded into a bare integer. Rule 2: a count may be written with any
    thousands separator. Plus the one conversion a writer legitimately makes:
    the source's ratio written as a percentage (see cross_forms).
    convert (task #113, on with --figure-in-proof) also accepts a unit amount
    the source prints in another unit of mass or length."""
    text = _nfkc(source_text or "")
    num = fig["number"]
    kind = fig["kind"]
    approx = bool(fig.get("approximate"))
    plain = num.replace("·", ".").replace(",", ".")
    int_digits = re.sub(r"[^0-9]", "", num)
    places = _places(num)
    tol = fig.get("tolerance") or _tolerance(num, fig["value"], approx)
    pcts = percents if percents is not None else _percent_values(text)
    decs = decimals if decimals is not None else _decimal_values(text)
    cnts = counts if counts is not None else _count_values(text)
    unts = units if units is not None else _unit_values(text)

    # For the limit rule at the bottom: the source's figures of the claim's own
    # kind. Amounts carrying a unit have none, so the rule stays out of the way.
    same_kind = (pcts if kind == "percent" else
                 decs if kind == "decimal" else
                 cnts if kind == "count" else [])

    if kind == "percent":
        if _percent_written(fig["value"], places, text):
            return True, "percent form"
        if VALUE_TOLERANCE and any(abs(v - fig["value"]) <= tol for v in pcts):
            return True, "percent within rounding"
    elif kind == "decimal":
        if _decimal_written(fig["value"], places, text):
            return True, "decimal form"
        if VALUE_TOLERANCE and any(abs(v - fig["value"]) <= tol for v in decs):
            return True, "decimal within rounding"
    elif kind == "count":
        if fig.get("scale_form"):
            # "1.7 million" written out in the source, however it is spaced or
            # hyphenated there ("a 300-million-mile trip").
            parts = re.split(r"[\s\-–—]+", fig["scale_form"])
            body = re.escape(parts[0]).replace(r"\.", r"[.,·]")
            if re.search(r"(?<![\d.,])" + body + r"[\s\-–—]*" + re.escape(parts[-1]),
                         text, re.IGNORECASE):
                return True, "the same figure written with its scale word"
        head, tail = int_digits[:-3], int_digits[-3:]
        if re.search(r"(?<!\d)" + re.escape(head) + r"[,   ]?"
                     + re.escape(tail) + r"(?!\d)", text):
            return True, "count with any separator"
        if VALUE_TOLERANCE and any(abs(v - fig["value"]) <= tol for v in cnts):
            return True, ("count within the claim's own rounding" if approx
                          else "count within rounding")
    else:                                     # a number carrying a unit
        unit = fig.get("unit") or ""
        body = re.escape(plain).replace(r"\.", r"[.,·]")
        near = (r"(?<![\d.,])" + body + r"\s*-?\s*" + re.escape(unit))
        if unit and re.search(near, text, re.IGNORECASE):
            return True, "number with its unit"
        if re.search(r"(?<![\d.,])" + body + r"(?![\d.,])", text):
            return True, "number present"
        # The same amount printed more precisely: the claim's "4.5 mg/dL" is
        # the source's "4.52 mg/dL". Same unit only, and the same rounding
        # allowance every other kind of figure already gets.
        if VALUE_TOLERANCE and unit and any(
                u == unit and abs(v - fig["value"]) <= tol for v, u in unts):
            return True, "amount within rounding"
        if convert and converted_present(fig, text):
            return True, "the same amount in another unit"

    # The same figure written the other way round: the source's "1.69" is the
    # claim's "69% higher". Measured on the eggs and paper1 runs, this one
    # conversion accounted for three of the four flags the first version
    # raised, and all three were fair reporting, not errors.
    for value, other_kind, other_places in cross_forms(fig):
        form = f"{value:.{other_places}f}"
        cross_tol = _tolerance(form, value, approx)
        if other_kind == "decimal":
            if _decimal_written(value, other_places, text):
                return True, "the source's ratio form of the same figure"
            if VALUE_TOLERANCE and any(abs(v - value) <= cross_tol for v in decs):
                return True, "the source's ratio form, within rounding"
        else:
            if _percent_written(value, other_places, text):
                return True, "the source's percentage form of the same figure"
            if VALUE_TOLERANCE and any(abs(v - value) <= cross_tol for v in pcts):
                return True, "the source's percentage form, within rounding"

    # A limit rather than a measurement ("literacy stayed below roughly 20%"):
    # any source figure of the same kind on the correct side of the limit backs
    # the claim. Tried LAST, after the figure's own form: "more than 12 million
    # observations" is best answered by the source printing "12 million", and
    # letting the limit rule answer first made that a false alarm whenever the
    # source's other counts happened to be smaller (task #99).
    if fig.get("bound") and same_kind:
        ok = (any(v <= fig["value"] for v in same_kind)
              if fig["bound"] == "below"
              else any(v >= fig["value"] for v in same_kind))
        if ok:
            return True, "a source figure on the correct side of this limit"
        return False, "every figure in the source is on the other side of this limit"
    return False, ""


def build_clusters(figures: List[Dict[str, Any]]) -> List[List[int]]:
    """Group figures that are two forms of ONE author-given number (rule 3).
    '42% higher (RR 1.42)' -> one cluster; a lower risk written '11% lower
    (HR 0.89)' -> one cluster; '0.42' next to '42%' -> one cluster."""
    n = len(figures)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        a, b = find(i), find(j)
        if a != b:
            parent[b] = a

    for i in range(n):
        for j in range(i + 1, n):
            a, b = figures[i], figures[j]
            pair = {a["kind"], b["kind"]}
            if pair != {"percent", "decimal"}:
                continue
            p = a if a["kind"] == "percent" else b
            d = a if a["kind"] == "decimal" else b
            frac = p["value"] / 100.0
            if (abs(d["value"] - (1.0 + frac)) <= CLUSTER_SLACK
                    or abs(d["value"] - (1.0 - frac)) <= CLUSTER_SLACK
                    or abs(d["value"] - frac) <= CLUSTER_SLACK):
                union(i, j)

    groups: Dict[int, List[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return [sorted(g) for g in groups.values()]


def source_texts_from(sources: Dict[str, Any]) -> Dict[str, str]:
    """{paper_id: full text} from the in-memory source dicts the pipeline
    already holds (each carries the source's sentence index)."""
    out = {}
    for pid, sd in (sources or {}).items():
        if not isinstance(sd, dict):
            continue
        text = sd.get("full_text") or " ".join(
            s.get("text", "") for s in (sd.get("sentences") or []))
        out[pid] = text
    return out


# ------------------------------------------------------- part 1b: where it sits

# Places a figure can be found, nearest the quoted proof first.
SCOPE_ORDER = ("sentence", "window", "paragraph", "paper", "none")
# Proof text whose entry recorded no source: it came from one of the cited
# sources, so it is added to each of them rather than thrown away.
UNATTRIBUTED = ""


def proof_texts_by_source(claim: Dict[str, Any]) -> Dict[str, Dict[str, List[str]]]:
    """The passages this claim was actually judged on, grouped by source.

    A worked example: the judge quotes one sentence of the Redshaw paper as the
    proof and also kept the four sentences either side of it as the window. The
    result for that source is {"sentence": [the quoted sentence],
    "window": [the quoted sentence, the surrounding passage]}. The sentence
    tier is what a reader sees printed on the card; the window tier is the
    wider passage the judgement was made against.
    """
    out: Dict[str, Dict[str, List[str]]] = {}

    def add(pid: Optional[str], tier: str, value: Optional[str]) -> None:
        if not value:
            return
        slot = out.setdefault(pid or UNATTRIBUTED,
                              {"sentence": [], "window": []})
        if value not in slot[tier]:
            slot[tier].append(value)

    evs = list(claim.get("evidences") or [])
    if isinstance(claim.get("evidence"), dict):
        evs.append(claim["evidence"])
    for e in evs:
        if not isinstance(e, dict):
            continue
        pid = e.get("paper_id")
        add(pid, "sentence", e.get("sentence"))
        add(pid, "window", e.get("window"))
    for e in ((claim.get("covering") or {}).get("covered") or []):
        if isinstance(e, dict):
            add(e.get("paper_id"), "sentence", e.get("sentence"))
    for e in ((claim.get("component_check") or {}).get("evidence") or []):
        if isinstance(e, dict):
            add(e.get("paper_id"), "sentence", e.get("sentence"))
            add(e.get("paper_id"), "window", e.get("window"))
    return out


def scope_text(proofs: Dict[str, Dict[str, List[str]]], paper_id: str,
               tier: str) -> str:
    """One block of proof text for one source: the sentence tier alone, or the
    sentence tier plus the retrieved windows."""
    parts: List[str] = []
    for key in (paper_id, UNATTRIBUTED):
        slot = proofs.get(key)
        if not slot:
            continue
        parts.extend(slot["sentence"])
        if tier == "window":
            parts.extend(slot["window"])
    return normalize_source("\n\n".join(dict.fromkeys(p for p in parts if p)))


# Task #113 fix 3: how far either side of a quoted proof sentence the check
# reads before it says "not in the quoted proof". The source index keeps no
# paragraph breaks (PDF text arrives as one run of sentences), so a paragraph
# is approximated by a fixed stretch of characters — 600 is roughly four to six
# sentences of a research paper, about one paragraph.
PARAGRAPH_CHARS = 600
_WS_RE = re.compile(r"\s+")


def _squash(s: str) -> str:
    return _WS_RE.sub(" ", s or "").strip()


# A figure a few sentences from the proof can still be about something else:
# paper1_ds_reasoner t17 says "around 70% of foundational AI models" and the
# paragraph near its proof says "around 70% of the gap in per capita GDP". So a
# paragraph sentence counts only when it shares a topic word with the words
# around the figure in the claim. A topic word is 4+ letters and not a
# function word, compared on its first 5 letters ("diabetes" = "diabetic"), or
# an all-capitals abbreviation of 2+ letters ("CVD").
TOPIC_CONTEXT_CHARS = 70
_TOPIC_STOP = set("""
about above after again against almost along also although among amongst and
another around because been before being below between both came come could
each either else even ever every from further have having here however into
just least less like made make many more most much must near nearly neither
never next none only other over per rather really same several should since
some such than that their them then there these they this those though through
thus under until upon very was were what when where whether which while whom
whose will with within without would your roughly approximately higher lower
percent times increase increased decrease decreased reported found showed shown
study studies paper data total overall analysis results
""".split())
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-]*")


def topic_stems(text: str) -> set:
    out = set()
    for w in _WORD_RE.findall(text or ""):
        if len(w) >= 2 and w.isupper():
            out.add(w)
        elif len(w) >= 4 and w.lower() not in _TOPIC_STOP:
            out.add(w.lower()[:5])
    return out


def figure_topic(claim_text: str, fig: Dict[str, Any]) -> set:
    """Topic words within TOPIC_CONTEXT_CHARS of the figure in the claim."""
    text = _MARKER_RE.sub(" ", _nfkc(claim_text or ""))
    i = text.find(fig.get("raw") or "")
    if i < 0:
        return set()
    return topic_stems(text[max(0, i - TOPIC_CONTEXT_CHARS):
                            i + len(fig["raw"]) + TOPIC_CONTEXT_CHARS])


_SENT_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+(?=[A-Z(\[])")


def states_same_kind(fig: Dict[str, Any], text: str) -> bool:
    """Does this proof text print any figure of the claim figure's kind?
    A percentage and a decimal ratio count as one kind (a writer turns one into
    the other). The paragraph step runs only when the answer is no: a quoted
    proof that prints its own, different figure for the fact is evidence the
    claim's figure is wrong (the 23%-versus-43% hole of card 99), while a proof
    sentence with no figure at all is merely weak."""
    kind = fig["kind"]
    if kind in ("percent", "decimal"):
        return bool(_percent_values(text) or _decimal_values(text))
    if kind == "count":
        return bool(_count_values(text))
    unit = (fig.get("unit") or "").lower()
    if any(u == unit for _, u in _unit_values(text)):
        return True
    dim = CONVERSIONS.get(unit)
    return bool(dim and any(CONVERSIONS.get(m.group(2).lower(), ("",))[0] == dim[0]
                            for m in _CONV_RE.finditer(text)))


def paragraph_text(proofs: Dict[str, Dict[str, List[str]]], paper_id: str,
                   squashed_source: str, topic: Optional[set] = None) -> str:
    """The stretch of the cited source around each quoted proof sentence of
    this source: PARAGRAPH_CHARS either side of where the sentence sits in the
    full text. With a topic set, only the stretch's sentences sharing a topic
    word with it are kept (see TOPIC_CONTEXT_CHARS); an empty topic keeps
    nothing, so a figure with no readable context never passes this way. A worked example: the judge quoted "participants were followed
    for a median of 17.5 years"; the sentence before it in the paper, which the
    quote left out, says "among 29 615 adults pooled from 6 cohorts" — this
    text includes both, so a claim's "29,615 people" is not reported as absent
    from the proof.
    A proof sentence that cannot be found in the source adds nothing."""
    if not squashed_source:
        return ""
    parts: List[str] = []
    for key in (paper_id, UNATTRIBUTED):
        slot = proofs.get(key)
        if not slot:
            continue
        for sent in slot["sentence"]:
            s = _squash(normalize_source(sent))
            if len(s) < 20:
                continue
            pos, length = -1, 0
            for probe in (s[:80], s[:40], s[-40:]):
                pos = squashed_source.find(probe)
                if pos >= 0:
                    length = len(s) if probe != s[-40:] else len(probe)
                    break
            if pos < 0:
                continue
            span = squashed_source[max(0, pos - PARAGRAPH_CHARS):
                                   pos + length + PARAGRAPH_CHARS]
            if topic is None:
                parts.append(span)
                continue
            parts.extend(s for s in _SENT_SPLIT_RE.split(span)
                         if topic & topic_stems(s))
    return "\n\n".join(dict.fromkeys(parts))


def _best_scope(a: str, b: str) -> str:
    """The nearer of two places (a proof sentence beats the window, the window
    beats the rest of the paper, the paper beats nowhere)."""
    return a if SCOPE_ORDER.index(a) <= SCOPE_ORDER.index(b) else b


def check_claim_numbers(claim: Dict[str, Any],
                        source_texts: Dict[str, str],
                        in_proof: bool = False) -> Optional[Dict[str, Any]]:
    """The numeric payload for ONE claim, or None when there is nothing
    confident to check (no figures, or no cited source text on hand).

    With in_proof off this is the 2026-09-03 behaviour unchanged: a figure
    found anywhere in a cited source passes. With it on, each cluster also
    records WHERE it was found (task #99) — in the quoted proof sentences, in
    the retrieved window, only elsewhere in the paper, or nowhere.
    """
    figures = extract_figures(claim.get("text", ""), skip_names=in_proof)
    if not figures:
        return None
    keys = list(claim.get("paper_ids") or []) + list(claim.get("markers") or [])
    texts = [(k, normalize_source(source_texts[k]))
             for k in dict.fromkeys(keys) if source_texts.get(k)]
    if not texts:
        return None

    proofs = proof_texts_by_source(claim) if in_proof else {}
    proof_shown = any(scope_text(proofs, k, "window") for k, _ in texts)

    cache = {k: (_percent_values(t), _decimal_values(t), _count_values(t),
                 _unit_values(t))
             for k, t in texts}
    squashed: Dict[str, str] = {}
    for f in figures:
        f["found_in"], f["how"], f["why_not"] = None, "", ""
        if in_proof:
            f["scope"], f["scope_how"] = "none", ""
        for k, t in texts:
            if in_proof and f["scope"] not in ("sentence", "window"):
                for tier in ("sentence", "window"):
                    ptext = scope_text(proofs, k, tier)
                    if not ptext:
                        continue
                    ok_p, how_p = figure_present(f, ptext, convert=True)
                    if ok_p:
                        f["scope"], f["scope_how"] = tier, how_p
                        break
            if f["found_in"] is None:
                pcts, decs, cnts, unts = cache[k]
                ok, how = figure_present(f, t, pcts, decs, cnts, unts,
                                         convert=in_proof)
                if ok:
                    f["found_in"], f["how"] = k, how
                elif how and not f["why_not"]:
                    f["why_not"] = how
            if f["found_in"] and (not in_proof or f["scope"] != "none"):
                break
        if in_proof and f["scope"] == "none" and f["found_in"]:
            # Task #113 fix 3, tried only after every source's quoted proof
            # and window: the paragraph around a quoted proof sentence.
            topic = figure_topic(claim.get("text", ""), f)
            for k, t in texts:
                if states_same_kind(f, scope_text(proofs, k, "sentence")):
                    continue                  # the proof prints its own figure
                if k not in squashed:
                    squashed[k] = _squash(t)
                ptext = paragraph_text(proofs, k, squashed[k], topic=topic)
                ok_p, how_p = (figure_present(f, ptext, convert=True)
                               if ptext else (False, ""))
                if ok_p:
                    f["scope"], f["scope_how"] = "paragraph", how_p
                    break
        if in_proof and f["scope"] == "none" and f["found_in"]:
            f["scope"] = "paper"

    clusters = []
    for idx in build_clusters(figures):
        members = [figures[i] for i in idx]
        hit = next((m for m in members if m["found_in"]), None)
        row = {
            "forms": [m["raw"] for m in members],
            "found": hit is not None,
            "found_in": hit["found_in"] if hit else None,
            "how": hit["how"] if hit else "",
            "why_not": ("" if hit else next((m["why_not"] for m in members
                                             if m.get("why_not")), "")),
            "bound": next((m.get("bound") for m in members if m.get("bound")), None),
            "approximate": any(m.get("approximate") for m in members),
        }
        if in_proof:
            where = "none"
            for m in members:
                where = _best_scope(where, m["scope"])
            row["where"] = where
            row["where_how"] = next((m["scope_how"] for m in members
                                     if m["scope"] == where and m["scope_how"]), "")
        clusters.append(row)
    missing = [c for c in clusters if not c["found"]]
    payload = {
        "checked": len(figures),
        "sources": [k for k, _ in texts],
        "clusters": clusters,
        "missing": [" / ".join(c["forms"]) for c in missing],
    }
    if in_proof:
        payload["proof_shown"] = proof_shown
        # Only worth telling the reader about when there IS a quoted proof to
        # compare against: with nothing quoted, "not in the proof" would say
        # more about the card than about the figure.
        payload["elsewhere"] = ([" / ".join(c["forms"]) for c in clusters
                                 if c.get("where") == "paper"]
                                if proof_shown else [])
    return payload


def eligible(claim: Dict[str, Any]) -> bool:
    """Only the dangerous direction: a claim the tool called supported, that
    cites something, and that the author has not already ruled on."""
    if claim.get("verdict") != "supported":
        return False
    if not (claim.get("markers") or claim.get("paper_ids")):
        return False
    if claim.get("owner_flag") or claim.get("source_file_missing"):
        return False
    return True


def check_numbers(claims: List[Dict[str, Any]],
                  sources: Dict[str, Any],
                  in_proof: bool = False) -> Dict[str, Any]:
    """Part 1 over every eligible claim. No model calls. Tags claims in place
    with c["number_check"]; returns a summary for analysis.json metadata.

    in_proof (task #99, --figure-in-proof) additionally records where each
    figure sits relative to the quoted proof and reports the claims whose
    figure is only elsewhere in the cited paper."""
    source_texts = source_texts_from(sources)
    checked, flagged_ids, elsewhere_ids = 0, [], []
    for c in claims:
        c.pop(FIELD, None)                    # deterministic: always recomputed
        if not eligible(c):
            continue
        payload = check_claim_numbers(c, source_texts, in_proof=in_proof)
        if payload is None:
            continue
        checked += 1
        c[FIELD] = payload
        if payload["missing"]:
            flagged_ids.append(c["id"])
        elif payload.get("elsewhere"):
            elsewhere_ids.append(c["id"])
    if flagged_ids:
        logger.info("Numeric cross-check: %d claim(s) state a figure that is in no "
                    "cited source (%s)", len(flagged_ids), ", ".join(flagged_ids))
    if elsewhere_ids:
        logger.info("Numeric cross-check: %d claim(s) state a figure that is in the "
                    "cited paper but not in the quoted proof (%s)",
                    len(elsewhere_ids), ", ".join(elsewhere_ids))
    out = {"checked": checked, "flagged": len(flagged_ids), "flagged_ids": flagged_ids}
    if in_proof:
        out["elsewhere"] = len(elsewhere_ids)
        out["elsewhere_ids"] = elsewhere_ids
    return out


# -------------------------------------------------------------- part 2: direction

# Paired increase/decrease vocabulary. A claim using one of these asserts a
# direction that a source can contradict word for word.
# Named mechanisms first. These words carry the claim's substance, so they must
# be preferred over an incidental "rises" elsewhere in the same sentence: the
# 2026-09-03 probe of the real t8 case asked about "rises" when the word that
# mattered was "downregulating".
MECHANISM_INCREASE = ["upregulat", "up-regulat", "promot", "stimulat", "enhanc",
                      "accelerat", "elevat", "boost", "worsen"]
MECHANISM_DECREASE = ["downregulat", "down-regulat", "suppress", "inhibit",
                      "attenuat", "diminish", "protect", "improv"]
INCREASE_CUES = MECHANISM_INCREASE + [
    "increas", "raise", "raises", "raising", "rise", "rises", "rising",
    "higher", "greater",
]
DECREASE_CUES = MECHANISM_DECREASE + [
    "decreas", "reduc", "lower", "lowers", "lowering", "fall", "falls",
    "declin", "slow",
]
# Cause-and-effect connectives: the claim says one thing brings about another,
# so the source can have the two the other way around.
CAUSAL_CUES = [
    "causes", "caused by", "causing", "leads to", "led to", "leading to",
    "results in", "resulting in", "drives", "driven by", "due to",
    "because of", "triggers", "triggered by", "brings about", "gives rise to",
    "responsible for", "mediates", "mediated by",
]

DIRECTION_PROMPT = """You check ONE thing: does the source text state the same \
direction as the claim, the opposite direction, or neither?

CLAIM:
{CLAIM}

THE DIRECTION WORDING IN THE CLAIM: "{CUE}"

SOURCE TEXT (the passages this claim was judged against):
{SOURCE}

Answer with JSON and nothing else:
{{"answer": "confirmed" | "reversed" | "not_stated",
  "quote": "<the exact sentence from the SOURCE TEXT that settles it, or empty>",
  "reason": "<one short sentence>"}}

Rules:
- "confirmed" only if the source text states the SAME direction for the SAME \
subject as the claim (a different wording with the same meaning is fine).
- "reversed" if the source states the OPPOSITE direction for that subject, or \
puts the cause and the effect the other way round.
- "not_stated" if the source text does not settle the direction at all. \
Silence is "not_stated" — never guess "confirmed" from what is plausible.
- The quote must be copied character for character from the SOURCE TEXT above."""

_ANSWERS = ("confirmed", "reversed", "not_stated")
MAX_SOURCE_CHARS = 6000


def _prompt_sha(prompt: str) -> str:
    return hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:8]


def find_direction_cue(claim_text: str) -> Optional[Dict[str, str]]:
    """The direction wording a source could contradict, or None. Prefers an
    explicit cause-and-effect connective, then an increase/decrease word."""
    low = _nfkc(claim_text or "").lower()
    for cue in CAUSAL_CUES:
        if cue in low:
            return {"cue": cue, "kind": "cause"}
    for cues, kind in ((MECHANISM_INCREASE, "increase"),
                       (MECHANISM_DECREASE, "decrease"),
                       (INCREASE_CUES, "increase"),
                       (DECREASE_CUES, "decrease")):
        for cue in cues:
            if cue in low:
                return {"cue": cue, "kind": kind}
    return None


def proof_text_for(claim: Dict[str, Any]) -> str:
    """What the judge actually read for this claim: every evidence sentence and
    window, plus the covering-set proofs, in one block."""
    parts: List[str] = []
    evs = list(claim.get("evidences") or [])
    if claim.get("evidence"):
        evs.append(claim["evidence"])
    for e in evs:
        if not isinstance(e, dict):
            continue
        for key in ("window", "sentence"):
            v = e.get(key)
            if v and v not in parts:
                parts.append(v)
    for e in ((claim.get("covering") or {}).get("covered") or []):
        v = (e or {}).get("sentence")
        if v and v not in parts:
            parts.append(v)
    for e in ((claim.get("component_check") or {}).get("evidence") or []):
        v = (e or {}).get("sentence") if isinstance(e, dict) else e
        if v and v not in parts:
            parts.append(v)
    return "\n\n".join(parts)[:MAX_SOURCE_CHARS]


def _parse(raw: str) -> Tuple[Optional[Dict[str, Any]], str]:
    if not raw:
        return None, "no model response"
    obj = extract_json(raw)
    if isinstance(obj, dict) and str(obj.get("answer", "")).lower() in _ANSWERS:
        return {"answer": str(obj["answer"]).lower(),
                "quote": str(obj.get("quote", "") or ""),
                "reason": str(obj.get("reason", "") or "")}, ""
    m = re.search(r'"answer"\s*:\s*"(confirmed|reversed|not_stated)"', raw, re.I)
    if m:
        q = re.search(r'"quote"\s*:\s*"([^"]*)', raw)
        r = re.search(r'"reason"\s*:\s*"([^"]*)', raw)
        return {"answer": m.group(1).lower(),
                "quote": (q.group(1) if q else ""),
                "reason": (r.group(1).strip() if r else "")}, ""
    return None, "unparseable answer"


def _quote_verified(quote: str, proof: str) -> bool:
    """Is the model's quote really in the text it was given? Recorded, never
    used to change the answer — see the module docstring."""
    if not quote or len(quote.split()) < 4:
        return False
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
    return norm(quote) in norm(proof)


def check_direction(claims: List[Dict[str, Any]], llm, workers: int = 4) -> Dict[str, Any]:
    """Part 2 over every eligible claim carrying a direction cue. One small
    call each. Tags claims in place with c["direction_check"]."""
    psha = _prompt_sha(DIRECTION_PROMPT)
    todo, reused = [], 0
    for c in claims:
        if not eligible(c):
            c.pop(DIR_FIELD, None)
            continue
        cue = find_direction_cue(c.get("text", ""))
        if not cue:
            c.pop(DIR_FIELD, None)
            continue
        prior = c.get(DIR_FIELD) or {}
        if (prior.get("model") == llm.model and prior.get("prompt_sha") == psha
                and prior.get("cue") == cue["cue"]):
            reused += 1
            continue
        todo.append((c, cue))

    unparsed: List[str] = []

    def ask(item) -> None:
        c, cue = item
        proof = proof_text_for(c)
        if not proof.strip():
            c.pop(DIR_FIELD, None)
            return
        prompt = (DIRECTION_PROMPT
                  .replace("{CLAIM}", c.get("text", ""))
                  .replace("{CUE}", cue["cue"])
                  .replace("{SOURCE}", proof))
        raw = llm.call(prompt, temperature=0.0, max_output_tokens=1024,
                       purpose="direction_check", claim_id=c.get("id"))
        payload, err = _parse(raw)
        if payload is None:
            c.pop(DIR_FIELD, None)         # an honest gap beats a guessed answer
            unparsed.append(c.get("id"))
            return
        payload.update({"cue": cue["cue"], "cue_kind": cue["kind"],
                        "quote_verified": _quote_verified(payload["quote"], proof),
                        "model": llm.model, "prompt_sha": psha})
        c[DIR_FIELD] = payload

    parallel_map(lambda it: ask(it), todo, workers=workers)
    if unparsed:
        logger.warning("Direction check unparseable for %s — left untagged "
                       "(retried next run)", ", ".join(x for x in unparsed if x))

    counts = {a: 0 for a in _ANSWERS}
    flagged_ids = []
    for c in claims:
        ans = (c.get(DIR_FIELD) or {}).get("answer")
        if ans in counts:
            counts[ans] += 1
            if ans in ("reversed", "not_stated"):
                flagged_ids.append(c["id"])
    return {"checked": len(todo), "reused": reused, "unparsed": len(unparsed),
            "counts": counts, "flagged_ids": flagged_ids}
