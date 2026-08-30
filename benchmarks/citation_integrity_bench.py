#!/usr/bin/env python3
"""Citation-Integrity benchmark harness — convert real citing sentences from
biomedical papers into verifier projects and score a finished run against the
benchmark's citation-accuracy labels.

Citation-Integrity (Sarol et al. 2024, github.com/ScienceNLP-Lab/Citation-Integrity,
MIT): 3,063 citation instances from PubMed Central papers. Each instance is one
(citing sentence, cited article) pair, hand-labelled for whether the cited
article actually supports the statement. This is the closest public set to what
this tool does — real published prose, real full-text sources, and the citation
itself is the unit under test.

What each row gives us (verified 2026-07-30 against the annotation files):
  - the citing paragraph + the exact citing sentence, with the citation under
    test marked `<|cit|>` / `<|multi_cit|>` and co-cited references marked
    `<|other_cit|>`;
  - the FULL TEXT of the cited article (`<Split>/references/<ref>.txt`,
    markdown-sectioned, ~6k-60k chars) — so retrieval is exercised for real,
    not handed a pre-selected snippet;
  - `evidence_segments`: the annotator's verbatim proof sentences WITH character
    offsets into that full text (offsets check out exactly), which gives a
    sentence-level diagnostic for free;
  - one of 9 fine labels (the paper's 8 plus an undocumented
    INDIRECT_NOT_REVIEW).

Usage:
  # build a 30-claim batch from the dev split (deterministic, seeded)
  python3 benchmarks/citation_integrity_bench.py convert --split dev --batch 1 \
      --size 30 --output-dir data/citation_integrity/batch_dev_1

  # score a finished run against that batch's ground truth
  python3 benchmarks/citation_integrity_bench.py score \
      --analysis data/citation_integrity/batch_dev_1_run/analysis.json \
      --ground-truth data/citation_integrity/batch_dev_1/ci_ground_truth.json

SET STATUS (author ruling 2026-08-01) — read before quoting any number from this
harness. The 100-row batch `data/citation_integrity/batch_dev_pilot100` is a
DIAGNOSIS set: fixes may be and have been derived from it (task #18), so its
scores are not an honest score of any tool version those fixes touch. Never
publish them as accuracy. The Citation-Integrity TEST split is the honest eval
and must stay unread and unconverted until those fixes are finished; the
quotable post-fix number comes from there alone. The rest of the dev split is
unconsumed and may seed further diagnosis batches. (WiCE differs: its held-out
rows are consumed outright.)

No LLM calls, no network (like regression_check.py / wice_bench.py). The raw data
lives under data/citation_integrity/ (gitignored; re-fetch with
`git clone --depth 1 https://github.com/ScienceNLP-Lab/Citation-Integrity
data/citation_integrity/repo` then unzip Data/annotations.zip into
Data/annotations_extracted/).

CLAIM UNIT (`--claim-unit`, default `span`): the annotators marked the exact
span of the citing sentence the citation is responsible for, and that span is
what the label describes — so it is the default claim text. Measured over the
whole dev split (2026-07-30; 316 citation instances, 339 annotated spans —
a row with several spans is classified from its outermost bounds, and the
proportions below hold either way): median 22 words, only 4 under 8 words, so these are
propositions rather than phrases; 40% are a whole sentence start to finish, and
the rest are a clause carved out of a longer sentence, which on a
multi-citation sentence is the only correct unit. About 9% genuinely lean on
text left behind — 8% open right after an attribution or hedge frame ("these
studies suggested that |X|", "we predict that |X|"), which reads as a flat
assertion when the author hedged, and 1% open with a pronoun only the previous
sentence resolves. 29% have three or more words of their own sentence discarded
after them (usually a list continuing under a DIFFERENT citation).

Those shapes are recorded per row, not filtered: `span_words`,
`span_is_full_sentence` and `span_context` (see `_span_context`, whose
`classes` field is the slice key — frame_dropped / pronoun_start /
mid_clause_start / tail_dropped, empty for a self-contained span). `evaluate`
cross-tabulates disagreements by those classes, so "the span was a fragment" is
a claim the label audit can CHECK instead of assume.

`--claim-unit sentence` expands each span to its enclosing sentence — the more
realistic input shape (whole sentences are what authors write) but it can add
content the citation under test was never responsible for, especially in
multi-citation sentences, which manufactures false-flags; the span classes stay
attached to the span there too, marking exactly the rows where the text fed to
the tool says more than the label covers. Choosing between the two for the
recorded eval is a Phase-C decision.

THE SCORED READING (author ruling 2026-08-10, task #32 round 3 — "Option B").
The answer key judges ONE citation, but the tool's row-level pass/flag
(`_tool_bucket`) answers a different question — is the tool content with the
WHOLE sentence — and it lumps "this paper does not support the sentence"
together with "some part of the sentence has no proof shown". Measured on the
round-3 arms (original + sibling-repaired, docs/TASK32_LOOP.md): the
whole-sentence reading scored 80/136 and moved to 74/136 when the missing
sibling papers were restored; the per-source reading — the tool's verdict for
the one paper the key is about — scored 94/136 and stayed put (93/136), which
is how a measure of the right question should behave. So the HEADLINE number
is now the per-source reading (`own_paper_side`, the `own_paper` block);
the whole-sentence reading is still reported below it as a secondary view of
the warning layer, and the answer key is never edited. A false-support on a
major-error row under EITHER reading makes `score` exit 1.

TWO-LABEL SCORING (the 2026-07-29/30 lesson from WiCE + the retreat pilot):
the fine labels are noisy — the paper reports expert kappa 0.18-0.31 on the
8-way label and its own evaluation collapses them. So this scorer never scores
the fine label directly. It reports the pass/flag line under TWO mappings, side
by side, plus the full per-fine-label confusion so any other mapping can be
recomputed by hand:

  strict     pass = ACCURATE; flag = the 8 other labels. The benchmark's own
             line, reported as-is (dual-report rule: the raw number always ships).
  grounding  what THIS tool claims to detect: does the cited article's text
             support the passage? pass = ACCURATE + INDIRECT + INDIRECT_NOT_REVIEW
             (the statement IS in the cited article; the defect is that the
             article is relaying someone else's finding — a provenance problem
             the tool does not claim to catch); flag = CONTRADICT,
             NOT_SUBSTANTIATE, IRRELEVANT, MISQUOTE, OVERSIMPLIFY. ETIQUETTE
             (13.6% of rows — "this general statement should have cited a
             different paper") is EXCLUDED from this tally and reported as its
             own band, because its source text typically neither proves nor
             refutes the passage.
Both mappings are provisional until the Phase-D label audit; the fine-label
confusion table is the durable artifact.

False-support and false-flag counts are always printed SEPARATELY and never
averaged (retreat-pilot Amendment 6 discipline).

CO-CITATION, i.e. WHICH ROWS ASK A FAIR QUESTION (2026-08-01, task #17). The
benchmark gives us the text of ONE cited article, but on most rows the paper
cited the statement to several — 54 of the dev-100 pilot. The converter drops
the co-citation tokens, so on those rows the tool is asked to prove a whole
statement from one of the several papers that back it, and a red card is the
setup's doing rather than the tool's. Every row therefore records
`co_citation` (`_co_citation`): `single` / `shared_spot` (the citation under
test shares its bracket) / `siblings_in_span` (others elsewhere in the span) /
`both`. The scorer reports the SINGLE-CITED SUBSET separately — that is the
honest false-alarm rate — and cross-tabulates every disagreement by class.
Ground truth written before this field existed is recomputed from the stored
`annotated_span`, so the five existing pilot runs rescore without conversion.
The false-alarm rate rose with how much the paper cited (30% single-cited, 50%
shared bracket, 67% siblings in span), so any earlier number quoted over all
100 rows overstates it.

The report also prints the ESCALATION RATE — the share of judged claims that
fell through to `method=llm_fulltext` full-text component checking. It needs no
labels at all and it tracked the false-alarm count almost step for step across
the five dev-100 arms, so it is the cheapest quality signal this harness has.

NOTE on the safety blocklist: wice_bench.py filters health/bio content because
unattended Claude model columns must not poke a refusal classifier. That rule
does NOT apply here — this set is biomedical by construction and these are
supervised tool runs (Gemini/DeepSeek-class judges, no Claude grading column).
Any later Claude-graded label audit does its own 5-row content sample-check
first (Fable safety layer; Opus is the validated backup grader).

The tool-side pass/flag mapping is imported from wice_bench so the two external
benchmarks can never drift apart on what counts as "supported".
"""

import argparse
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from wice_bench import _tool_bucket, _adjudicated_bucket  # noqa: E402
from ci_batch_ids import batch_tag, qualify  # noqa: E402

CI_ROOT = os.path.join(ROOT, "data", "citation_integrity", "repo", "Data",
                       "annotations_extracted", "annotations")
SPLIT_DIR = {"train": "Train", "dev": "Dev", "test": "Test"}

# --- label bands ----------------------------------------------------------
ACCURATE = "ACCURATE"
MAJOR = {"CONTRADICT", "NOT_SUBSTANTIATE", "IRRELEVANT"}
MINOR_CONTENT = {"MISQUOTE", "OVERSIMPLIFY"}
PROVENANCE = {"INDIRECT", "INDIRECT_NOT_REVIEW"}
ETIQUETTE = {"ETIQUETTE"}
ALL_LABELS = {ACCURATE} | MAJOR | MINOR_CONTENT | PROVENANCE | ETIQUETTE


def strict_side(label):
    """The benchmark's own line: only ACCURATE passes."""
    return "pass" if label == ACCURATE else "flag"


def grounding_side(label):
    """This tool's line. Returns None for rows excluded from the tally."""
    if label == ACCURATE or label in PROVENANCE:
        return "pass"
    if label in MAJOR or label in MINOR_CONTENT:
        return "flag"
    return None  # ETIQUETTE: own band, see module docstring


# --- citing-sentence normalisation ---------------------------------------
# The citation under test is `<|cit|>` or `<|multi_cit|>`; co-cited references
# are `<|other_cit|>`. Both appear inside bracket groups mixed with plain
# numbers ("[<|other_cit|>,10]"), so bracket groups are rewritten whole.
_CIT_GROUP = re.compile(r"\[[^\[\]]*<\|(?:multi_)?cit\|>[^\[\]]*\]")
_CIT_BARE = re.compile(r"<\|(?:multi_)?cit\|>")
_OTHER_GROUP = re.compile(r"\(?\[[^\[\]]*<\|other_cit\|>[^\[\]]*\]\)?")
# co-citations also appear bare inside parentheses: "(Figure <|other_cit|>)"
_OTHER_PAREN = re.compile(r"\([^()]*<\|other_cit\|>[^()]*\)")
_OTHER_BARE = re.compile(r"<\|other_cit\|>")
_WS = re.compile(r"\s+")


# Sentence-boundary detection for --claim-unit sentence. Conservative: a period
# only ends a sentence when it is not part of a known abbreviation or an
# initial, because citing prose is full of "et al." / "e.g." / "Fig.".
_ABBREV = re.compile(
    r"(?:\b(?:al|e\.g|i\.e|vs|etc|approx|Fig|Figs|Tab|ref|refs|No|Dr|Prof|"
    r"Mr|Mrs|Ms|St|cf|ca|resp|min|max|sp|spp|var|ed|eds|vol|pp)|\b[A-Z])\.$")
# "no" was deliberately dropped from the list (2026-08-01): unlike every other
# title-style entry here (Dr/Mr/Mrs/Ms/St/Prof), it had BOTH cases, and
# lowercase "no." collides with the ordinary English word ("...the answer was
# no.") — that merged the sentence with whatever came before it. "No." (number)
# stays, capitalized only, matching the rest of the list's convention.


def _sentence_bounds(par, start, end):
    """Widen [start, end) to the enclosing sentence within the paragraph."""
    lo = 0
    for m in re.finditer(r"[.!?]\s+", par[:start]):
        if not _ABBREV.search(par[:m.start() + 1]):
            lo = m.end()
    nl = par.rfind("\n", 0, start)
    if nl + 1 > lo:
        lo = nl + 1
    head = par[:end].rstrip()
    if head.endswith((".", "!", "?")) and not _ABBREV.search(head):
        hi = end          # the span already runs to a sentence end
    else:
        hi = len(par)
        for m in re.finditer(r"[.!?](?:\s|$)", par[end:]):
            if not _ABBREV.search(par[:end + m.start() + 1]):
                hi = end + m.start() + 1
                break
    nl2 = par.find("\n", end)
    if nl2 != -1 and nl2 < hi:
        hi = nl2
    return lo, hi


# Openers that only the PREVIOUS sentence resolves, and the words a span can
# begin right after when the annotator's cut left an attribution or concession
# frame behind ("these studies suggested that |X|", "although |X|").
_PRON_START = {"they", "these", "this", "it", "those", "both", "such", "he",
               "she", "its", "their", "them"}
_FRAME_WORDS = {"that", "whether", "because", "while", "although", "if", "how",
                "when", "where", "which"}
# a sentence can end in a superscript/bracketed citation, so strip those before
# asking "did the previous sentence end here?"
_TRAIL_CITE = re.compile(r"(?:<\|[a-z_]*cit\|>|\[[^\]]*\]|\(\s*\)|\d+)\s*$")
_TAIL_MIN_WORDS = 3


def _strip_trailing_cites(text):
    prev = None
    while text != prev:
        prev = text
        text = _TRAIL_CITE.sub("", text).rstrip()
    return text


def _span_context(row):
    """How much of the annotated span's meaning lives OUTSIDE the span.

    The span is the unit the benchmark label describes, but it is carved out of
    a citing sentence, so some spans lean on text that stays behind. This
    records which way, per row, so a disagreement between our tool and the
    benchmark can be CHECKED against span shape instead of blamed on it.
    Measured over the 339 dev spans: 40% are whole self-contained sentences,
    8% drop a frame, 1% open with a dangling pronoun, 29% have same-sentence
    text discarded after them.

    `classes` is the slice key; an empty list means the span stands alone. A
    span can carry several classes and is counted under each. Under
    `--claim-unit sentence` the classes still describe the SPAN, so they mark
    the rows where the text we feed the tool says more than the label covers.
    """
    cc = row.get("citation_context") or []
    par = row.get("citing_paragraph") or ""
    out = {"starts_at_sentence_start": None, "ends_at_sentence_end": None,
           "dropped_frame": None, "pronoun_start": None,
           "tail_words_dropped": 0, "classes": []}
    if not cc or not par:
        return out
    s = min(c["start"] for c in cc)
    e = max(c["end"] for c in cc)
    if e > len(par):
        return out
    span = par[s:e]

    before = _strip_trailing_cites(par[:s].rstrip())
    at_start = before == "" or before.endswith((".", "!", "?", ":", ";"))
    after = par[e:]
    at_end = (span.rstrip().endswith((".", "!", "?"))
              or after.lstrip()[:1] in {"", ".", ";"})
    out["starts_at_sentence_start"] = at_start
    out["ends_at_sentence_end"] = at_end

    if not at_start:
        raw_before = par[:s].rstrip()
        last = re.search(r"(\b\w+)\s*$", raw_before)
        last_word = last.group(1).lower() if last else ""
        first = span.split()[0].lower().strip(",.;:") if span.split() else ""
        if last_word in _FRAME_WORDS:
            out["dropped_frame"] = raw_before[-80:].lstrip()
            out["classes"].append("frame_dropped")
        if first in _PRON_START:
            out["pronoun_start"] = first
            out["classes"].append("pronoun_start")
        if not out["classes"]:
            out["classes"].append("mid_clause_start")

    if not at_end:
        m = re.search(r"[.!?](?:\s|$)", after)
        tail = after[:m.start()] if m else after
        tail = _OTHER_BARE.sub("", _CIT_BARE.sub("", tail))
        tail = re.sub(r"\[[^\]]*\]", "", tail)
        n = len(tail.split())
        out["tail_words_dropped"] = n
        if n >= _TAIL_MIN_WORDS:
            out["classes"].append("tail_dropped")
    return out


def _span_info(row):
    """(raw span text, is the span already a whole sentence?) or (None, None)."""
    cc = row.get("citation_context") or []
    par = row.get("citing_paragraph") or ""
    if not cc:
        return None, None
    s = min(c["start"] for c in cc)
    e = max(c["end"] for c in cc)
    if not par or e > len(par):
        return " ".join(c.get("text", "") for c in cc), None
    lo, hi = _sentence_bounds(par, s, e)
    return par[s:e], (lo >= s and hi <= e + 1)


def _raw_claim_text(row, unit="span"):
    """The citing text we will emit, still carrying its citation tokens."""
    cc = row.get("citation_context") or []
    par = row.get("citing_paragraph") or ""
    if unit == "sentence" and cc and par:
        s = min(c["start"] for c in cc)
        e = max(c["end"] for c in cc)
        if e <= len(par):
            lo, hi = _sentence_bounds(par, s, e)
            return par[lo:hi]
    return " ".join(c.get("text", "") for c in cc)


def _co_citation(raw_text):
    """Did the paper cite this statement to several articles?

    Recorded per row (task #17, 2026-08-01) because it decides whether the
    question we ask the tool is a FAIR one. The benchmark hands us the text of
    ONE cited article, and the converter drops the co-citation tokens, so on a
    multi-cited row the tool is told to prove a whole sentence from one of the
    several papers that actually back it. A red card there is the setup's
    fault, not the tool's, and a false-alarm rate computed over all rows
    overstates it — measured on the dev-100 pilot, false alarms ran 30% on
    single-cited rows against 67% where siblings sat inside the span.

    `<|multi_cit|>` means the citation under test shares its bracket with
    others; `<|other_cit|>` means further citations sit elsewhere in the same
    span. A row can have both. `class` is the slice key; `single` is the only
    class where the emitted text asks for exactly what the label describes."""
    t = raw_text or ""
    shared = bool(re.search(r"<\|multi_cit\|>", t))
    siblings = len(re.findall(r"<\|other_cit\|>", t))
    cls = ("both" if shared and siblings else
           "shared_spot" if shared else
           "siblings_in_span" if siblings else "single")
    return {"shared_spot": shared, "siblings_in_span": siblings,
            "class": cls, "is_single_cited": cls == "single"}


def _claim_text(row, key, unit="span"):
    """Rewrite the citing text so the citation under test becomes our [[key]]
    marker and every other citation is dropped. Returns (text, None), or
    (None, reason) for the ~4% of rows whose annotated span is mangled — the
    span cuts a citation token in half, or omits the marker altogether."""
    text = _raw_claim_text(row, unit)
    if not text.strip():
        return None, "annotated span is empty"
    marker = f"[[{key}]]"
    text = _CIT_GROUP.sub(marker, text)
    text = _CIT_BARE.sub(marker, text)
    text = _OTHER_GROUP.sub("", text)
    text = _OTHER_PAREN.sub("", text)
    text = _OTHER_BARE.sub("", text)
    text = _WS.sub(" ", text).strip()
    text = re.sub(r"\s+([,;.])", r"\1", text)
    text = re.sub(r"\(\s*\)|\[\s*\]", "", text).strip()
    n = text.count(marker)
    if n != 1:
        return None, (f"annotated span carries {n} citation markers, not 1"
                      if n else "annotated span does not contain the citation "
                                "marker under test")
    if "<|" in text or "|>" in text:
        return None, "annotated span cuts a co-citation token in half"
    if text.endswith(marker):
        text += "."
    return text, None


def _norm(s):
    return _WS.sub(" ", (s or "")).strip().lower()


def _rows(split):
    """Every annotation row of a split, in a deterministic order."""
    sdir = os.path.join(CI_ROOT, SPLIT_DIR[split])
    paths = sorted(glob.glob(os.path.join(sdir, "citations", "*", "*.json")))
    if not paths:
        sys.exit(f"no annotation files under {sdir}/citations — see the module "
                 f"docstring for how to fetch and unzip the data")
    out = []
    for p in paths:
        ref = os.path.basename(os.path.dirname(p))
        inst = os.path.splitext(os.path.basename(p))[0]  # <citingPMCID>_<n>
        with open(p, encoding="utf-8") as f:
            row = json.load(f)
        row["_ci_id"] = f"{split}/{ref}/{inst}"
        row["_ref"] = ref
        row["_citing"] = inst.rsplit("_", 1)[0]
        row["_instance"] = inst.rsplit("_", 1)[-1]
        row["_source_path"] = os.path.join(sdir, "references", f"{ref}.txt")
        out.append(row)
    return out


def _used_ci_ids():
    """ci_ids consumed by any prior converted batch — so a later batch can be
    disjoint from what has already been run (a row informs the tool once)."""
    used = set()
    for pat in ("citation_integrity_runs/*/ci_ground_truth.json",
                "../data/citation_integrity/*/ci_ground_truth.json"):
        for p in glob.glob(os.path.join(HERE, pat)):
            with open(p, encoding="utf-8") as f:
                for v in json.load(f)["claims"].values():
                    used.add(v.get("ci_id"))
    used.discard(None)
    return used


def _pick(rows, size, stratify, seed):
    """Deterministic selection. 'balanced' fills half the batch with ACCURATE
    and spreads the rest round-robin over the error labels, so a small pilot
    still sees MISQUOTE/INDIRECT (1-2% of the corpus each); 'natural' keeps the
    corpus base rates."""
    import random
    rng = random.Random(seed)
    pool = list(rows)
    rng.shuffle(pool)
    if stratify == "natural":
        return pool[:size]
    by = defaultdict(list)
    for r in pool:
        by[r.get("label")].append(r)
    picked = by.get(ACCURATE, [])[:size // 2]
    others = sorted(k for k in by if k != ACCURATE)
    i = 0
    while len(picked) < size and others:
        lab = others[i % len(others)]
        if by[lab]:
            picked.append(by[lab].pop(0))
        else:
            others.remove(lab)
            continue
        i += 1
    # top up with ACCURATE if the error labels ran dry
    spare = by.get(ACCURATE, [])[size // 2:]
    while len(picked) < size and spare:
        picked.append(spare.pop(0))
    picked.sort(key=lambda r: r["_ci_id"])
    return picked


def convert(split, batch, size, output_dir, seed=7, stratify="balanced",
            exclude_used=False, claim_unit="span"):
    all_rows = _rows(split)
    unknown = [r["_ci_id"] for r in all_rows if r.get("label") not in ALL_LABELS]
    # drop mangled rows BEFORE sampling, so --size is honoured and the reasons
    # are reported once for the whole pool instead of per batch
    rows, unconvertible = [], []
    for r in all_rows:
        if r.get("label") not in ALL_LABELS:
            continue
        _, why = _claim_text(r, "probe", claim_unit)
        if why:
            unconvertible.append({"ci_id": r["_ci_id"], "label": r["label"],
                                  "reason": why})
        elif not os.path.exists(r["_source_path"]):
            unconvertible.append({"ci_id": r["_ci_id"], "label": r["label"],
                                  "reason": "cited reference text is missing"})
        else:
            rows.append(r)
    if unconvertible:
        print(f"pool: {len(rows)} of {len(rows) + len(unconvertible)} labelled "
              f"rows are convertible; {len(unconvertible)} dropped:")
        for reason, k in Counter(e["reason"] for e in unconvertible).most_common():
            print(f"    {k:4d}  {reason}")
    if exclude_used:
        used = _used_ci_ids()
        before = len(rows)
        rows = [r for r in rows if r["_ci_id"] not in used]
        print(f"used-id exclusion: {before - len(rows)} already-converted rows "
              f"removed ({len(used)} used ids)")
    # earlier batches consume earlier picks — batches never overlap
    consumed = set()
    picked = []
    for _ in range(batch):
        avail = [r for r in rows if r["_ci_id"] not in consumed]
        picked = _pick(avail, size, stratify, seed)
        consumed.update(r["_ci_id"] for r in picked)
    if len(picked) < size:
        print(f"WARNING: only {len(picked)}/{size} rows available for batch {batch}")

    os.makedirs(os.path.join(output_dir, "sources"), exist_ok=True)
    refs, paras, gt, excluded = [], [], {}, []
    for n, row in enumerate(picked, 1):
        key = f"ci{split}{n:04d}"
        text, why = _claim_text(row, key, claim_unit)
        if text is None:
            excluded.append({"ci_id": row["_ci_id"], "label": row.get("label"),
                             "reason": why})
            continue
        with open(row["_source_path"], encoding="utf-8") as f:
            src = f.read()
        # one physical copy per claim: several claims cite the same article, and
        # a 1:1 key->claim mapping is what makes scoring unambiguous. Identical
        # copies share one embedding-cache entry (content-hash keyed), so the
        # duplication is free. Side effect: the cross-source nudges
        # (component_hunt / over_citation) are meaningless on such a project —
        # they are display-only and never touch a verdict.
        with open(os.path.join(output_dir, "sources", f"{key}.txt"), "w",
                  encoding="utf-8") as f:
            f.write(src)
        title = src.lstrip("# ").split("\n", 1)[0][:120]
        refs.append(f"# {title} (Citation-Integrity {row['_ci_id']})\n"
                    f"{key} = {key}.txt")
        paras.append(text)
        segs = [e.get("text", "") for e in row.get("evidence_segments") or []]
        span, span_full = _span_info(row)
        gt[key] = {
            "ci_id": row["_ci_id"],
            "claim_unit": claim_unit,
            "claim_text": text,
            "annotated_span": span,
            "span_words": len((span or "").split()),
            "span_is_full_sentence": span_full,
            "span_context": _span_context(row),
            "co_citation": _co_citation(_raw_claim_text(row, claim_unit)),
            "split": split,
            "ref": row["_ref"],
            "citing_pmcid": row["_citing"],
            "instance": row["_instance"],
            "label": row["label"],
            "strict_side": strict_side(row["label"]),
            "grounding_side": grounding_side(row["label"]),
            "evidence_segments": segs,
            "n_evidence": len(segs),
            "source_chars": len(src),
            "citing_paragraph": row.get("citing_paragraph", ""),
        }

    with open(os.path.join(output_dir, "my_text.md"), "w", encoding="utf-8") as f:
        f.write(f"# Citation-Integrity {split} batch {batch}\n\n"
                + "\n\n".join(paras) + "\n")
    with open(os.path.join(output_dir, "my_text.md.refs.txt"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(refs) + "\n")
    with open(os.path.join(output_dir, "ci_ground_truth.json"), "w",
              encoding="utf-8") as f:
        json.dump({"dataset": "citation_integrity", "split": split,
                   "batch": batch, "seed": seed, "stratify": stratify,
                   "claim_unit": claim_unit,
                   "size_requested": size, "claims": gt,
                   "excluded": excluded,
                   "pool_unconvertible": unconvertible}, f, indent=1)

    labs = Counter(v["label"] for v in gt.values())
    n_frag = sum(1 for v in gt.values() if v["span_is_full_sentence"] is False)
    print(f"wrote {len(gt)} claims -> {output_dir}  (claim unit: {claim_unit})")
    print(f"  labels: {dict(labs.most_common())}")
    print(f"  annotated spans shorter than their sentence: {n_frag}/{len(gt)}")
    ctx = Counter()
    for v in gt.values():
        cls = (v["span_context"] or {}).get("classes") or ["self_contained"]
        for c in cls:
            ctx[c] += 1
    print(f"  span context classes: {dict(ctx.most_common())}"
          f"  (a span can carry several)")
    if unknown:
        print(f"  note: {len(unknown)} rows had an unrecognised label and were "
              f"never in the pool")
    for e in excluded:
        print(f"  EXCLUDED {e['ci_id']}: {e['reason']}")
    print(f"\nrun: venv/bin/python3 verify_my_text.py "
          f"--text {output_dir}/my_text.md --sources {output_dir}/sources "
          f"--references {output_dir}/my_text.md.refs.txt "
          f"--output-dir {output_dir}_run --yes")


# --- scoring --------------------------------------------------------------
def _collapse(bucket):
    """3-way tool bucket -> the two-label line."""
    return "pass" if bucket == "supported" else "flag"


def own_paper_side(claim, key):
    """What the tool concluded about the ONE paper the answer key is about.

    The key judges a single citation; the tool keeps a separate yes/no for
    every cited paper. This reads the tool's verdict for that single source out
    of the per-source list, ignoring what the other cited papers said and
    ignoring the sentence-level warning chips. This is the scored reading
    (author ruling 2026-08-10, task #32). Returns None when the source is not
    in the claim's per-source list at all. `ci_sibling_compare` imports this
    function so the two scorers cannot drift apart."""
    for ev in (claim.get("evidences") or []):
        if isinstance(ev, dict) and ev.get("source_title") == key:
            return "pass" if ev.get("supported") else "flag"
    ev = claim.get("evidence")
    if isinstance(ev, dict) and ev.get("source_title") == key:
        return "pass" if ev.get("supported") else "flag"
    return None


def _row_co_citation(g):
    """(co-citation info, was it recomputed?) for one ground-truth entry.

    Ground truth written before 2026-08-01 has no `co_citation` field, so it is
    recomputed from the stored `annotated_span`, which keeps its raw citation
    tokens. That is exact for `--claim-unit span` (the span IS what was
    emitted) and a LOWER BOUND under `--claim-unit sentence`, where the emitted
    sentence can carry siblings the span did not — so a recomputed row can only
    understate how multi-cited it was, never overstate it."""
    info = g.get("co_citation")
    if info:
        return info, False
    return _co_citation(g.get("annotated_span")), True


def _shown_sentences(claim):
    """Every source sentence the run puts in front of the reader."""
    out = []
    for r in (claim.get("covering") or {}).get("covered", []) or []:
        if r.get("sentence"):
            out.append(r["sentence"])
    for e in claim.get("evidences") or []:
        if isinstance(e, dict) and e.get("sentence"):
            out.append(e["sentence"])
        elif isinstance(e, str):
            out.append(e)
    if isinstance(claim.get("evidence"), str) and claim["evidence"]:
        out.append(claim["evidence"])
    return out


def _evidence_hit(shown, gold_segments):
    """Does any shown sentence overlap any gold proof segment? Containment
    either way, else >=60% token overlap of the shorter side."""
    for s in shown:
        ns = _norm(s)
        if len(ns) < 20:
            continue
        for g in gold_segments:
            ng = _norm(g)
            if len(ng) < 20:
                continue
            if ns in ng or ng in ns:
                return True
            ts, tg = set(ns.split()), set(ng.split())
            if ts and tg:
                inter = len(ts & tg)
                if inter / min(len(ts), len(tg)) >= 0.6:
                    return True
    return False


def evaluate(analysis, meta):
    """Pure scoring: (analysis dict, ground-truth dict) -> a result dict. No
    printing, no I/O — so every published number can be recounted by script."""
    gt = meta["claims"]
    by_key = {}
    for c in analysis.get("text_claims", []):
        for key in c.get("markers") or []:
            by_key.setdefault(key, c)

    rows = []
    conf = defaultdict(Counter)       # fine label -> Counter(pass/flag/MISSING)
    conf_adj = defaultdict(Counter)
    conf_own = defaultdict(Counter)   # the scored reading: that one paper
    # the same two tables over SINGLE-CITED rows only — the subset where the
    # emitted text asks for exactly what the label describes (task #17)
    conf_single = defaultdict(Counter)
    conf_single_adj = defaultdict(Counter)
    ctx = defaultdict(Counter)        # span-context class -> Counter(outcomes)
    cocite = defaultdict(Counter)     # co-citation class -> Counter(outcomes)
    methods = Counter()               # grounding method -> n (escalation proxy)
    methods_accurate = Counter()
    recomputed_cocite = 0
    has_arbiter = False
    ev_hits = ev_total = 0
    scoped = []

    def add_ctx(classes, lab, tool):
        """Slice disagreements by span shape, so 'the span was a fragment' is a
        checkable explanation rather than an excuse. Multi-class rows count in
        each of their classes, so these columns do not sum to n."""
        for cl in classes:
            ctx[cl]["n"] += 1
            if tool == "MISSING":
                ctx[cl]["missing"] += 1
                continue
            for name, mapping in (("strict", strict_side),
                                  ("grounding", grounding_side)):
                side = mapping(lab)
                if side is None or side == tool:
                    continue
                ctx[cl][f"{name}_false_"
                        + ("support" if tool == "pass" else "flag")] += 1

    def add_cocite(cls, lab, tool):
        """Same slicing by how many articles the paper cited the statement to.
        Unlike span classes a row has exactly one class, so these DO sum to n."""
        cocite[cls]["n"] += 1
        if tool == "MISSING":
            cocite[cls]["missing"] += 1
            return
        for name, mapping in (("strict", strict_side),
                              ("grounding", grounding_side)):
            side = mapping(lab)
            if side is None or side == tool:
                continue
            cocite[cls][f"{name}_false_"
                        + ("support" if tool == "pass" else "flag")] += 1

    for key, g in sorted(gt.items()):
        c = by_key.get(key)
        lab = g["label"]
        # a ground truth written before span classes existed says "unclassified"
        # rather than silently claiming its spans were self-contained
        sc = g.get("span_context")
        classes = ["unclassified"] if sc is None else (sc.get("classes")
                                                      or ["self_contained"])
        cc, was_recomputed = _row_co_citation(g)
        recomputed_cocite += was_recomputed
        cc_class, single = cc["class"], cc["is_single_cited"]
        if c is None:
            conf[lab]["MISSING"] += 1
            conf_adj[lab]["MISSING"] += 1
            conf_own[lab]["MISSING"] += 1
            if single:
                conf_single[lab]["MISSING"] += 1
                conf_single_adj[lab]["MISSING"] += 1
            add_ctx(classes, lab, "MISSING")
            add_cocite(cc_class, lab, "MISSING")
            rows.append({"key": key, "ci_id": g.get("ci_id"), "label": lab,
                         "tool": "MISSING", "adj": "MISSING", "own": "MISSING",
                         "note": "",
                         "context_classes": classes, "co_citation": cc_class,
                         "single_cited": single, "method": None})
            continue
        has_arbiter = has_arbiter or bool(c.get("arbiter"))
        tool = _collapse(_tool_bucket(c))
        adj = _collapse(_adjudicated_bucket(c)[0])
        own = own_paper_side(c, key) or "NOT_LISTED"
        conf[lab][tool] += 1
        conf_adj[lab][adj] += 1
        conf_own[lab][own] += 1
        if single:
            conf_single[lab][tool] += 1
            conf_single_adj[lab][adj] += 1
        add_ctx(classes, lab, tool)
        add_cocite(cc_class, lab, tool)
        method = c.get("method") or "unknown"
        methods[method] += 1
        if lab == ACCURATE:
            methods_accurate[method] += 1
        scope = (c.get("citation_scope") or {}).get("scope")
        if scope not in (None, "full"):
            scoped.append({"key": key, "label": lab, "scope": scope})
        note = ""
        if g.get("n_evidence"):
            shown = _shown_sentences(c)
            if shown:
                ev_total += 1
                hit = _evidence_hit(shown, g["evidence_segments"])
                ev_hits += hit
                note = "proof_overlaps_gold=" + ("yes" if hit else "no")
        rows.append({"key": key, "ci_id": g.get("ci_id"), "label": lab,
                     "tool": tool, "adj": adj, "own": own, "note": note,
                     "scope": scope,
                     "context_classes": classes, "co_citation": cc_class,
                     "single_cited": single, "method": method})

    def tally(table, mapping):
        ok = tot = 0
        for lab, counts in table.items():
            side = mapping(lab)
            if side is None:
                continue
            for got, k in counts.items():
                if got in ("MISSING", "NOT_LISTED"):
                    continue
                tot += k
                ok += k if got == side else 0
        return {"ok": ok, "total": tot}

    def band(table, labels, side):
        return sum(table[l][side] for l in labels)

    n_major = sum(sum(conf[l].values()) for l in MAJOR)
    n_minor = sum(sum(conf[l].values()) for l in MINOR_CONTENT)
    n_acc = sum(conf[ACCURATE].values())
    n_single = sum(sum(v.values()) for v in conf_single.values())
    n_acc_single = sum(conf_single[ACCURATE].values())

    def n_scored(table, labels):
        """Rows of these labels that actually carry a verdict in this table."""
        return sum(k for l in labels for got, k in table[l].items()
                   if got not in ("MISSING", "NOT_LISTED"))

    def esc_rate(counter):
        """Share of judged claims that fell through to full-text component
        checking. Free quality proxy: across the five dev-100 arms it tracked
        the false-alarm count almost step for step, and it costs no labels."""
        tot = sum(counter.values())
        return {"llm_fulltext": counter.get("llm_fulltext", 0), "judged": tot,
                "by_method": dict(counter.most_common())}

    return {
        "n": sum(sum(v.values()) for v in conf.values()),
        "split": meta.get("split"), "batch": meta.get("batch"),
        "claim_unit": meta.get("claim_unit", "span"),
        "rows": rows, "conf": conf, "conf_adj": conf_adj,
        "by_context_class": {k: dict(v) for k, v in ctx.items()},
        "by_co_citation": {k: dict(v) for k, v in cocite.items()},
        "co_citation_recomputed": recomputed_cocite,
        "has_arbiter": has_arbiter,
        "strict": tally(conf, strict_side),
        "strict_adj": tally(conf_adj, strict_side),
        "grounding": tally(conf, grounding_side),
        "grounding_adj": tally(conf_adj, grounding_side),
        # the scored reading (Option B, 2026-08-10): the tool's verdict for the
        # one paper the key is about. No adjudicated variant — the arbiter's
        # finding is claim-level, not per-source.
        "own_paper": {
            "strict": tally(conf_own, strict_side),
            "grounding": tally(conf_own, grounding_side),
            "false_support_major": {"k": band(conf_own, MAJOR, "pass"),
                                    "n": n_scored(conf_own, MAJOR)},
            "false_support_minor": {"k": band(conf_own, MINOR_CONTENT, "pass"),
                                    "n": n_scored(conf_own, MINOR_CONTENT)},
            "false_flag_accurate": {"k": conf_own[ACCURATE]["flag"],
                                    "n": n_scored(conf_own, [ACCURATE])},
            "not_listed": sum(v.get("NOT_LISTED", 0) for v in conf_own.values()),
        },
        "etiquette_rows": sum(sum(conf[l].values()) for l in ETIQUETTE),
        "false_support_major": {"k": band(conf, MAJOR, "pass"), "n": n_major},
        "false_support_minor": {"k": band(conf, MINOR_CONTENT, "pass"),
                                "n": n_minor},
        "false_flag_accurate": {"k": conf[ACCURATE]["flag"], "n": n_acc},
        # the same measurements over single-cited rows only. On a multi-cited
        # row the tool is asked to prove a statement from one of the several
        # papers that back it, so a red card there is the converter's doing;
        # this subset is the honest false-alarm rate (task #17).
        "single_cited": {
            "n": n_single,
            "strict": tally(conf_single, strict_side),
            "strict_adj": tally(conf_single_adj, strict_side),
            "grounding": tally(conf_single, grounding_side),
            "grounding_adj": tally(conf_single_adj, grounding_side),
            "false_support_major": {"k": band(conf_single, MAJOR, "pass"),
                                    "n": sum(sum(conf_single[l].values())
                                             for l in MAJOR)},
            "false_flag_accurate": {"k": conf_single[ACCURATE]["flag"],
                                    "n": n_acc_single},
        },
        "escalation": esc_rate(methods),
        "escalation_accurate": esc_rate(methods_accurate),
        "evidence_overlap": {"hits": ev_hits, "total": ev_total},
        "scoped": scoped,
        "missing": [r["key"] for r in rows if r["tool"] == "MISSING"],
    }


def _pct(d):
    return f"{100 * d['ok'] / d['total']:.0f}%" if d["total"] else "n/a"


def report(res, tag=None):
    # Row ids are only unique WITHIN a batch (every batch numbers from
    # cidev0001), so qualify them whenever the caller knows which batch this is.
    q = (lambda k: qualify(tag, k)) if tag else (lambda k: k)
    where = f" [{tag}]" if tag else ""
    print(f"Citation-Integrity {res['split']} batch {res['batch']}{where} — "
          f"{res['n']} rows scored (claim unit: {res['claim_unit']})\n")
    etq = f"{res['etiquette_rows']} ETIQUETTE rows excluded"
    own = res.get("own_paper")
    if own:
        print("=== THAT ONE PAPER — the scored reading (author ruling "
              "2026-08-10, task #32) ===")
        print("The answer key judges one citation. This is the tool's verdict "
              "for that one\ncited paper, ignoring the other cited papers and "
              "the sentence-level warnings.")
        print(f"strict    two-label agreement: {own['strict']['ok']}/"
              f"{own['strict']['total']} ({_pct(own['strict'])})")
        print(f"grounding two-label agreement: {own['grounding']['ok']}/"
              f"{own['grounding']['total']} ({_pct(own['grounding'])})   [{etq}]")
        ofs, ofn = own["false_support_major"], own["false_support_minor"]
        off = own["false_flag_accurate"]
        print(f"false-supports, major errors: {ofs['k']}/{ofs['n']}   "
              f"minor content errors: {ofn['k']}/{ofn['n']}   "
              f"false-flags on ACCURATE rows: {off['k']}/{off['n']}")
        if own["not_listed"]:
            print(f"rows whose per-source list does not name the paper under "
                  f"test: {own['not_listed']} (scored under neither side)")
        print()
    print("=== whole sentence — secondary reading (every complaint the viewer "
          "shows) ===")
    print(f"strict    two-label agreement: {res['strict']['ok']}/"
          f"{res['strict']['total']} ({_pct(res['strict'])})")
    print(f"grounding two-label agreement: {res['grounding']['ok']}/"
          f"{res['grounding']['total']} ({_pct(res['grounding'])})   [{etq}]")
    if res["has_arbiter"]:
        print("\n=== arbiter-adjudicated (scoring-time only, verdict field "
              "untouched) ===")
        print(f"strict    two-label agreement: {res['strict_adj']['ok']}/"
              f"{res['strict_adj']['total']} ({_pct(res['strict_adj'])})")
        print(f"grounding two-label agreement: {res['grounding_adj']['ok']}/"
              f"{res['grounding_adj']['total']} "
              f"({_pct(res['grounding_adj'])})   [{etq}]")

    order = ([ACCURATE] + sorted(MAJOR) + sorted(MINOR_CONTENT)
             + sorted(PROVENANCE) + sorted(ETIQUETTE))
    for table, title in ((res["conf"], "per-fine-label confusion"),
                         (res["conf_adj"], "per-fine-label confusion, adjudicated")):
        if title.endswith("adjudicated") and not res["has_arbiter"]:
            continue
        print(f"\n{title} (fine label -> tool side):")
        for lab in order:
            counts = table.get(lab)
            if not counts:
                continue
            b = ("accurate" if lab == ACCURATE else
                 "major" if lab in MAJOR else
                 "minor-content" if lab in MINOR_CONTENT else
                 "provenance" if lab in PROVENANCE else "etiquette")
            bits = " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
            print(f"  {lab:20s} [{b:13s}] {bits}")

    fsj, fsn = res["false_support_major"], res["false_support_minor"]
    ff = res["false_flag_accurate"]
    print(f"\nfalse-supports, major errors (contradicted / unsubstantiated / "
          f"irrelevant): {fsj['k']}/{fsj['n']}")
    print(f"false-supports, minor content errors (misquote / oversimplify): "
          f"{fsn['k']}/{fsn['n']}")
    print(f"false-flags on ACCURATE rows: {ff['k']}/{ff['n']}")
    print("(the three are separate measurements — do not average them)")

    sc = res["single_cited"]
    cc = res.get("by_co_citation") or {}
    if sc["n"]:
        print(f"\n=== single-cited rows only ({sc['n']} of {res['n']}) ===")
        print("The paper cited these statements to ONE article, so proving the "
              "text from that article is a fair question. On the rest the "
              "converter dropped the co-citations, and a red card there is the "
              "setup's doing — see the class counts below.")
        print(f"strict    two-label agreement: {sc['strict']['ok']}/"
              f"{sc['strict']['total']} ({_pct(sc['strict'])})")
        print(f"grounding two-label agreement: {sc['grounding']['ok']}/"
              f"{sc['grounding']['total']} ({_pct(sc['grounding'])})")
        print(f"false-flags on ACCURATE rows: "
              f"{sc['false_flag_accurate']['k']}/{sc['false_flag_accurate']['n']}"
              f"   (all rows: {ff['k']}/{ff['n']})")
        print(f"false-supports, major errors: "
              f"{sc['false_support_major']['k']}/{sc['false_support_major']['n']}")
    if cc:
        print("\nby how many articles the paper cited the statement to:")
        print(f"  {'class':18s} {'n':>4s} {'strict FS':>10s} {'strict FF':>10s}"
              f" {'grnd FS':>8s} {'grnd FF':>8s} {'miss':>5s}")
        for cl in sorted(cc, key=lambda k: -cc[k]["n"]):
            d = cc[cl]
            print(f"  {cl:18s} {d['n']:4d} {d.get('strict_false_support', 0):10d}"
                  f" {d.get('strict_false_flag', 0):10d}"
                  f" {d.get('grounding_false_support', 0):8d}"
                  f" {d.get('grounding_false_flag', 0):8d}"
                  f" {d.get('missing', 0):5d}")
        print("  (single = one citation, the fair question; shared_spot = the "
              "citation under test shares its bracket; siblings_in_span = "
              "other citations elsewhere in the same span; both = each.)")
        if res.get("co_citation_recomputed"):
            print(f"  note: {res['co_citation_recomputed']} rows had no "
                  f"recorded co-citation status and were recomputed from the "
                  f"stored span (exact for claim-unit span; a lower bound for "
                  f"claim-unit sentence).")

    esc, esca = res["escalation"], res["escalation_accurate"]
    if esc["judged"]:
        def _r(d):
            return (f"{d['llm_fulltext']}/{d['judged']} "
                    f"({100 * d['llm_fulltext'] / d['judged']:.0f}%)")
        print(f"\nescalated to full-text component checking: {_r(esc)}"
              + (f", on ACCURATE rows {_r(esca)}" if esca["judged"] else ""))
        print(f"  methods: {esc['by_method']}")
        print("  (a label-free quality proxy — across the five dev-100 arms "
              "this tracked the false-alarm count almost step for step)")
    ctx = res.get("by_context_class") or {}
    if ctx:
        print("\ndisagreements by span shape (primary verdicts; a span can "
              "carry several classes, so rows do not sum to n):")
        print(f"  {'class':18s} {'n':>4s} {'strict FS':>10s} {'strict FF':>10s}"
              f" {'grnd FS':>8s} {'grnd FF':>8s} {'miss':>5s}")
        for cl in sorted(ctx, key=lambda k: -ctx[k]["n"]):
            d = ctx[cl]
            print(f"  {cl:18s} {d['n']:4d} {d.get('strict_false_support', 0):10d}"
                  f" {d.get('strict_false_flag', 0):10d}"
                  f" {d.get('grounding_false_support', 0):8d}"
                  f" {d.get('grounding_false_flag', 0):8d}"
                  f" {d.get('missing', 0):5d}")
        print("  (FS = tool said supported where the benchmark flags; FF = the "
              "reverse. 'self_contained' = the span is a whole sentence; "
              "'unclassified' = ground truth written before span classes.)")
    ev = res["evidence_overlap"]
    if ev["total"]:
        print(f"\nshown proof overlaps the annotator's proof sentence: "
              f"{ev['hits']}/{ev['total']} rows that had both")
    if res["scoped"]:
        print(f"\ncitation-scope tags (indigo class, own viewer bucket): "
              f"{len(res['scoped'])}")
        for s in res["scoped"]:
            print(f"  {q(s['key']):22s} label={s['label']:20s} scope={s['scope']}")
    if res["missing"]:
        print(f"\nrows with no claim in the run: {len(res['missing'])} "
              f"({', '.join(q(k) for k in res['missing'])})")

    print("\nper-claim (OK/DIFF judged on the scored 'own' reading when present):")
    for r in res["rows"]:
        want = strict_side(r["label"])
        got = r.get("own") if r.get("own") in ("pass", "flag") else r["tool"]
        flag = ("MISS" if r["tool"] == "MISSING" else
                "OK  " if got == want else "DIFF")
        owncol = (f"own={r['own']:10s} " if r.get("own") else "")
        adjcol = (f"adj={r['adj']:5s} "
                  if res["has_arbiter"] and r["adj"] != r["tool"] else "")
        print(f"  {flag} {q(r['key']):22s} {r['label']:20s} "
              f"strict_want={want:5s} {owncol}tool={r['tool']:5s} "
              f"{adjcol}{r['note']}")

    own_fs = (res.get("own_paper") or {}).get("false_support_major",
                                              {}).get("k", 0)
    if res["false_support_major"]["k"] or own_fs:
        print("\n*** FALSE-SUPPORT on a major-error row — stop and report ***")
        return 1
    return 0


def score(analysis_path, gt_path):
    with open(analysis_path, encoding="utf-8") as f:
        analysis = json.load(f)
    with open(gt_path, encoding="utf-8") as f:
        meta = json.load(f)
    return report(evaluate(analysis, meta), batch_tag(os.path.dirname(gt_path)))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("convert")
    c.add_argument("--split", default="dev", choices=["train", "dev", "test"],
                   help="dev/train for development; keep test for a recorded eval")
    c.add_argument("--batch", type=int, default=1,
                   help="1-based batch number (deterministic, non-overlapping)")
    c.add_argument("--size", type=int, default=30)
    c.add_argument("--seed", type=int, default=7)
    c.add_argument("--stratify", default="balanced", choices=["balanced", "natural"])
    c.add_argument("--exclude-used", action="store_true",
                   help="drop rows any prior batch already converted")
    c.add_argument("--claim-unit", default="span", choices=["span", "sentence"],
                   help="span = the annotated citation context (the labelled "
                        "unit, default); sentence = its enclosing sentence")
    c.add_argument("--output-dir", required=True)
    s = sub.add_parser("score")
    s.add_argument("--analysis", required=True)
    s.add_argument("--ground-truth", required=True)
    a = ap.parse_args()
    if a.cmd == "convert":
        convert(a.split, a.batch, a.size, a.output_dir, a.seed, a.stratify,
                a.exclude_used, a.claim_unit)
    else:
        sys.exit(score(a.analysis, a.ground_truth))


if __name__ == "__main__":
    main()
