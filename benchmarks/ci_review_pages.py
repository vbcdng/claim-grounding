#!/usr/bin/env python3
"""Build ONE self-contained HTML page with a review card for every problem row of
the 2026-08-02 Citation-Integrity run — the rows where the answer key, our tool
and the two blind readers did not all agree (follow-up item 2, task #29).

Each card carries what a person needs to rule on the row without opening a
paper: the citing paragraph with the checked sentence highlighted, the benchmark
creators' label and their own proof sentences, our tool's verdict WITH its
reasoning and the sentence it showed, both blind readers' verdicts with their
reasons and quotes, every quote shown inside the surrounding paper text, and
(folded) the whole cited paper. The author answers one question per card —
reading it yourself, is the sentence supported? — and downloads the answers as
JSON.

Pure: no API calls, no network. Everything shown is already on disk.

    python3 benchmarks/ci_review_pages.py \
        --disagreements data/citation_integrity/blind_readers_2026-08-03.json \
        --out data/citation_integrity/review_2026-08-03/problem_rows.html

The pile split, the reader votes and the tool column all come from the
disagreement JSON written by `ci_disagreement_list.py`, so no column can drift
from the published document. As a guard the tool's verdict is re-collapsed from
the run's own `analysis.json` with the benchmark's `_tool_bucket`/`_collapse`
and compared against the recorded column; a mismatch stops the build.

NOT a grading packet: it shows the labels on purpose. Never hand it to a rater.
Also not safe for Fable — the rows are real biomedical papers quoted verbatim
(task #27 builds the reworded twins for that).
"""

import argparse
import glob
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)


from ci_row_review_page import _source_path  # noqa: E402
from wice_bench import _tool_bucket  # noqa: E402

CONTEXT_CHARS = 260          # paper text shown either side of a quote
LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl",
             "’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"}
MIN_QUOTE_CHARS = 24         # shorter than this and a "match" means nothing

PILES = {
    "A": ("Pile A — both readers disagree with the answer key",
          "Both readers read the paper and both landed on the opposite side of "
          "the key, so this is where a wrong label is most likely. Your answer "
          "here can change the key itself, and every score anyone has quoted "
          "from this benchmark is computed from that key."),
    "B": ("Pile B — the readers disagree with each other",
          "Genuinely hard rows: one reader passed the sentence and the other "
          "flagged it. Nothing can settle these except your own reading. On "
          "almost all of them at least one reader marked its own answer as "
          "medium or low confidence."),
    "C": ("Pile C — both readers agree with the key, our tool does not",
          "Nothing to rule on unless you disagree with all three: the key and "
          "both readers say the same thing and our tool says the other. These "
          "are the tool's own mistakes, and reading them is what tells us "
          "which fix they need."),
    "D": ("Pile D — rows with no verdict",
          "A column is missing a verdict for these rows."),
}


# ---------- the citing paragraph -----------------------------------------

# The benchmark marks citations with tokens. Stripping them leaves empty
# brackets, which reads as if the paper cited nothing, so they are spelled out
# instead. `<|cit|>` is the citation under test on its own, `<|multi_cit|>` the
# same citation sharing its bracket with others, `<|other_cit|>` a citation
# somewhere else in the paragraph.
READABLE = {"<|multi_cit|>": "‹the cited paper, plus other references›",
            "<|cit|>": "‹the cited paper›",
            "<|other_cit|>": "‹other references›"}


def citation_note(span):
    """How this citation sat in the text, in plain words. (The benchmark's own
    wording for this calls the checked text a "span", which the author ruled out
    on 2026-07-25, so it is spelled out here instead.)"""
    shared = span.count("<|multi_cit|>")
    others = span.count("<|other_cit|>")
    if shared and others:
        return ("This citation shared its brackets with other references, and "
                "the checked sentence carries %d further citation(s)." % others)
    if shared:
        return ("This citation shared its brackets with other references, so "
                "the paper never claimed this one article proved the sentence "
                "on its own.")
    if others:
        return ("This citation stood alone where it sits, but the checked "
                "sentence carries %d other citation(s)." % others)
    return "This was the only citation in the checked sentence."


# How the tool reached its verdict, in plain words.
METHOD_WORDS = {
    "llm": "it read the passages that looked closest to the sentence",
    "llm_fulltext": "it went through the whole cited paper in pieces",
    "arbiter_rescue": "the second checker found proof and the judge confirmed it",
    "tail_rescue": "proof turned up in a late part of the paper",
    "component_rescue": "each missing part was found separately, then re-judged",
    "none": "nothing was checked (no citation on this sentence)",
}

# What the second checker's answer means, in plain words.
ARBITER_WORDS = {
    "supported": "the paper does prove the sentence",
    "wrong_or_insufficient_evidence": "the proof exists in the paper, but our "
                                      "tool showed the wrong sentence for it",
    "add_citation_or_rewrite": "the paper really does not prove all of it — the "
                               "author should cite something else or reword",
    "conflict": "the paper says something that conflicts with the sentence",
}

TRIGGER_WORDS = {
    "unsupported": "our tool had rejected the sentence",
    "uncovered_components": "our tool accepted the sentence but could not show "
                            "proof for every part of it",
    "partial_support": "our tool accepted the sentence but one part was in none "
                       "of the cited papers",
    "conflict_candidate": "a sentence in the paper looked like a contradiction",
}


def _readable(text):
    for token, words in READABLE.items():
        text = text.replace(token, words)
    return re.sub(r"[ \t]+", " ", text).strip()


def _cit_spans(escaped):
    return re.sub(r"‹([^›]*)›",
                  lambda m: "<span class='cit'>" + m.group(1) + "</span>", escaped)


def paragraph_html(par, span):
    """The citing paragraph with the checked part highlighted, citation tokens
    spelled out. Returns (html, located?)."""
    par_r, span_r = _readable(par), _readable(span)
    i = par_r.find(span_r) if span_r else -1
    if i < 0:
        return _cit_spans(html.escape(par_r)), False
    return (_cit_spans(html.escape(par_r[:i]))
            + "<mark>" + _cit_spans(html.escape(span_r)) + "</mark>"
            + _cit_spans(html.escape(par_r[i + len(span_r):])), True)


# ---------- finding a quote inside the paper ------------------------------

def _fold(text):
    for lig, plain in LIGATURES.items():
        text = text.replace(lig, plain)
    return text


KEEP = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")


def _norm_map(text):
    """(normalised text, offset map) — map[i] is where normalised char i sits in
    the original. Normalising drops everything that is not a plain letter or
    digit, so hyphenation, lost superscripts, Greek letters mangled by the PDF
    extractor and broken spacing all stop mattering. It MUST keep exactly the
    characters `_norm` keeps, or a quote will fail to match text it is in — that
    bug (`isalnum()` here keeping α, the regex there dropping it) lost 13 of 323
    quotes on the first build."""
    out, offsets = [], []
    for i, ch in enumerate(_fold(text)):
        low = ch.lower()
        if low in KEEP:
            out.append(low)
            offsets.append(i)
    return "".join(out), offsets


def _norm(text):
    return re.sub(r"[^a-z0-9]+", "", _fold(text).lower())


def locate(quote, norm_src, offsets, src):
    """Where `quote` sits in the paper: (start, end) in the original text, or
    None. A quote that only matches in pieces (the "..." join defect) returns
    the pieces separately."""
    key = _norm(quote)
    if len(key) < MIN_QUOTE_CHARS:
        return []
    i = norm_src.find(key)
    if i >= 0:
        return [(offsets[i], offsets[i + len(key) - 1] + 1)]
    parts = [p for p in re.split(r"\.\.\.|…", quote) if _norm(p)]
    if len(parts) < 2:
        return []
    spans = []
    for part in parts:
        pkey = _norm(part)
        if len(pkey) < MIN_QUOTE_CHARS:
            continue
        j = norm_src.find(pkey)
        if j < 0:
            return []
        spans.append((offsets[j], offsets[j + len(pkey) - 1] + 1))
    return sorted(spans)


def in_context(quote, norm_src, offsets, src):
    """The quote rendered inside the paper text around it. Returns
    (html, note) — note says what went wrong when there is something to say."""
    spans = locate(quote, norm_src, offsets, src)
    if not spans:
        return None, ("This quote could not be found in the paper text, even "
                      "allowing for hyphenation and lost superscripts.")
    note = None
    if len(spans) > 1:
        note = ("Shown as one quote but it is %d separate passages in the "
                "paper, joined here with an ellipsis — see follow-up item 7."
                % len(spans))
    blocks = []
    for start, end in spans:
        before = src[max(0, start - CONTEXT_CHARS):start]
        after = src[end:end + CONTEXT_CHARS]
        if max(0, start - CONTEXT_CHARS) > 0:
            before = "…" + before.split(" ", 1)[-1]
        if end + CONTEXT_CHARS < len(src):
            after = after.rsplit(" ", 1)[0] + "…"
        blocks.append("<span class='ctx'>%s</span><mark>%s</mark>"
                      "<span class='ctx'>%s</span>"
                      % (html.escape(before), html.escape(src[start:end]),
                         html.escape(after)))
    return "<p class='inpaper'>%s</p>" % "</p><p class='inpaper'>".join(blocks), note


def highlight_all(src, quotes):
    """The whole paper with every quote marked. Overlaps are merged."""
    norm_src, offsets = _norm_map(src)
    spans = []
    for q in quotes:
        spans.extend(locate(q, norm_src, offsets, src))
    if not spans:
        return html.escape(src)
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    out, at = [], 0
    for start, end in merged:
        out.append(html.escape(src[at:start]))
        out.append("<mark>" + html.escape(src[start:end]) + "</mark>")
        at = end
    out.append(html.escape(src[at:]))
    return "".join(out)


# ---------- loading -------------------------------------------------------

def _read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_claims(run_dir):
    """{marker: the run's whole claim record}."""
    path = run_dir if run_dir.endswith(".json") else os.path.join(run_dir, "analysis.json")
    if not os.path.isabs(path):
        path = os.path.join(ROOT, path)
    out = {}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    for c in data.get("text_claims", []):
        for marker in c.get("markers") or []:
            out.setdefault(marker, c)
    return out


def load_sets(dis):
    """The disagreement JSON's spec -> {tag: {gt, claims, sources_dir, run_name}}."""
    spec_path = dis["spec"]
    if not os.path.isabs(spec_path):
        spec_path = os.path.join(ROOT, spec_path)
    with open(spec_path, encoding="utf-8") as fh:
        spec = json.load(fh)
    sets = {}
    for entry, meta in zip(spec["sets"], dis["sets"]):
        batch = entry["batch"] if os.path.isabs(entry["batch"]) \
            else os.path.join(ROOT, entry["batch"])
        sets[meta["tag"]] = {
            "gt": _read_json(os.path.join(batch, "ci_ground_truth.json"))["claims"],
            "claims": load_claims(entry["run"]),
            "sources_dir": os.path.join(batch, "sources"),
            "run_name": meta["run_name"],
        }
    return sets


# ---------- rendering one card -------------------------------------------

SIDE_WORD = {"pass": "supported", "flag": "flagged"}


def side_chip(who, side, extra=""):
    cls = {"pass": "s-pass", "flag": "s-flag"}.get(side, "s-none")
    word = SIDE_WORD.get(side, side or "no verdict")
    return ("<span class='chip %s'><span class='who'>%s</span> %s%s</span>"
            % (cls, html.escape(who), html.escape(word), extra))


def reader_block(name, vote, norm_src, offsets, src):
    if not vote:
        return "<p class='dim'>%s gave no verdict for this row.</p>" % html.escape(name)
    parts = ["<div class='sub'><b>%s</b> — %s, confidence %s%s</div>"
             % (html.escape(name.capitalize()),
                html.escape(SIDE_WORD.get(vote.get("side"), str(vote.get("side")))),
                html.escape(str(vote.get("confidence") or "not stated")),
                (" · problem it names: <code>%s</code>" % html.escape(str(vote["defect"])))
                if vote.get("defect") else "")]
    if vote.get("reason"):
        parts.append("<p class='why'>%s</p>" % html.escape(vote["reason"]))
    if vote.get("quote"):
        body, note = in_context(vote["quote"], norm_src, offsets, src)
        parts.append("<div class='lbl'>the sentence it rests on, in the paper</div>")
        if body:
            parts.append(body)
        else:
            parts.append("<blockquote>%s</blockquote>" % html.escape(vote["quote"]))
        if note:
            parts.append("<p class='flagnote'>%s</p>" % html.escape(note))
    return "<div class='reader'>%s</div>" % "".join(parts)


def tool_block(claim, norm_src, offsets, src, run_name):
    if not claim:
        return "<p class='dim'>No claim record found in the run for this row.</p>"
    verdict = claim.get("verdict") or ""
    method = claim.get("method") or "none"
    parts = ["<div class='sub'><b>Verdict:</b> <span class='v-%s'>%s</span>"
             " · how it got there: %s (<code>%s</code>) · run: %s</div>"
             % (html.escape(verdict), html.escape(verdict),
                html.escape(METHOD_WORDS.get(method, "no description recorded")),
                html.escape(method), html.escape(run_name))]
    if claim.get("reason"):
        parts.append("<p class='why'>%s</p>" % html.escape(claim["reason"]))
    ev = claim.get("evidence") or {}
    if ev.get("sentence"):
        body, note = in_context(ev["sentence"], norm_src, offsets, src)
        parts.append("<div class='lbl'>the sentence the tool put on the card"
                     + (" (judge votes %s)" % html.escape(str(ev["votes"]))
                        if ev.get("votes") else "") + "</div>")
        parts.append(body or "<blockquote>%s</blockquote>" % html.escape(ev["sentence"]))
        if note:
            parts.append("<p class='flagnote'>%s</p>" % html.escape(note))
    comp = claim.get("component_check") or {}
    if comp.get("found") or comp.get("missing"):
        parts.append("<div class='lbl'>parts of the sentence it checked one by one</div>"
                     "<ul class='parts'>")
        for c in comp.get("found") or []:
            parts.append("<li class='y'>found: %s</li>" % html.escape(str(c)))
        for c in comp.get("missing") or []:
            parts.append("<li class='n'>not found: %s</li>" % html.escape(str(c)))
        parts.append("</ul>")
        if comp.get("rescued"):
            parts.append("<p class='dim'>The missing parts were later found "
                         "separately, so the verdict was flipped back to "
                         "supported.</p>")
    scope = claim.get("citation_scope") or {}
    if scope.get("scope") and scope["scope"] != "full":
        parts.append("<p class='dim'>Citation-scope check: the tool read this "
                     "citation as backing only <i>%s</i> (<code>%s</code>), not "
                     "the whole sentence.</p>"
                     % (html.escape(scope.get("scoped_assertion") or "part of the sentence"),
                        html.escape(scope["scope"])))
    arb = claim.get("arbiter") or {}
    if arb.get("action"):
        rows = ["<div class='lbl'>what the second checker (the arbiter) said "
                "— this did NOT change the verdict above; the rows where letting "
                "it settle the complaint would change the score are follow-up "
                "item 3</div>",
                "<p class='dim'>Model <code>%s</code>. It was asked because %s "
                "(<code>%s</code>). Its answer: <b>%s</b> (<code>%s</code>).</p>"
                % (html.escape(arb.get("model") or "?"),
                   html.escape(TRIGGER_WORDS.get(arb.get("trigger"),
                                                 "the row was flagged")),
                   html.escape(arb.get("trigger") or "?"),
                   html.escape(ARBITER_WORDS.get(arb["action"],
                                                 "no description recorded")),
                   html.escape(arb["action"]))]
        if arb.get("why"):
            rows.append("<p class='why'>%s</p>" % html.escape(arb["why"]))
        for q in arb.get("proofs") or []:
            rows.append("<blockquote class='small'>%s</blockquote>" % html.escape(str(q)))
        parts.append("<details class='arb'><summary>the arbiter's reading</summary>"
                     + "".join(rows) + "</details>")
    return "".join(parts)


PMC_URL = "https://pmc.ncbi.nlm.nih.gov/articles/%s/"


def _pmcid(text):
    m = re.search(r"PMC\d+", text or "")
    return m.group(0) if m else None


def read_the_whole_thing(gt, src_path):
    """Links to the real papers, not only to our extracted copy. The author asked
    for this on 2026-08-03: a plain-text extraction is what the tool and the
    readers had to work with, but where it and the published page differ the page
    is the truth, and on some rows that difference IS the explanation."""
    cited = _pmcid(gt.get("ref"))
    citing = _pmcid(gt.get("citing_pmcid"))
    bits = []
    if cited:
        bits.append("the <b>cited paper</b>, which has to prove the sentence: "
                    "<a href='%s'>%s on PubMed Central</a>"
                    % (PMC_URL % cited, cited))
    if citing:
        bits.append("the <b>paper that wrote the sentence</b>: "
                    "<a href='%s'>%s</a>" % (PMC_URL % citing, citing))
    if os.path.exists(src_path):
        bits.append("the <b>plain-text copy our tool and the readers actually "
                    "read</b>: <a href='file://%s'>%s</a>"
                    % (html.escape(src_path), html.escape(os.path.basename(src_path))))
    orig = _source_path(gt.get("split", "dev"), gt.get("ref", ""))
    if orig:
        bits.append("the benchmark's own copy: <a href='file://%s'>%s</a>"
                    % (html.escape(orig), html.escape(os.path.basename(orig))))
    if not bits:
        return ""
    return ("<div class='lbl'>read the whole thing</div><p class='dim'>%s. Where "
            "the published page and our extracted copy differ, the published page "
            "is the truth.</p>" % "; ".join(bits))


def card(qid, row, pile, st, opts):
    gt = st["gt"].get(row["cid"]) or {}
    claim = st["claims"].get(row["cid"])
    src_path = os.path.join(st["sources_dir"], row["cid"] + ".txt")
    src = ""
    if os.path.exists(src_path):
        with open(src_path, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    norm_src, offsets = _norm_map(src)

    readers = row.get("readers") or {}
    quotes = [q for q in ([(readers.get("sonnet") or {}).get("quote"),
                           (readers.get("opus") or {}).get("quote"),
                           ((claim or {}).get("evidence") or {}).get("sentence")]
                          + list(gt.get("evidence_segments") or [])) if q]

    ask = (getattr(opts, "questions", None) or {}).get(qid)
    p = ["<article class='card%s' id='%s' data-qid='%s' data-pile='%s' data-fair='%d'>"
         % (" starred" if ask else "", qid, qid, pile,
            1 if row.get("fair") else 0)]
    p.append("<div class='cardtop'><span class='id'>%s</span>"
             "<span class='pilechip'>pile %s</span>" % (html.escape(qid), pile))
    if ask:
        p.append("<span class='pilechip star'>start here</span>")
    p.append(side_chip("answer key", row.get("key"),
                       " <span class='lab'>%s</span>" % html.escape(row.get("label") or "")))
    p.append(side_chip("our tool", row.get("tool")))
    for name in ("sonnet", "opus"):
        p.append(side_chip(name, (readers.get(name) or {}).get("side")))
    p.append("<span class='agree' data-for='%s'></span></div>" % qid)
    if ask:
        p.append("<div class='ask-box'><b>What I need from you:</b> %s</div>"
                 % html.escape(ask))

    # the sentence under test
    span = gt.get("annotated_span", "")
    marked, located = paragraph_html(gt.get("citing_paragraph", ""), span)
    p.append("<div class='lbl'>the sentence under test, inside the paragraph it came from</div>")
    p.append("<blockquote class='par'>%s</blockquote>" % marked)
    if not located:
        p.append("<p class='dim'>The checked part could not be located inside "
                 "that paragraph, so it is shown on its own:</p>"
                 "<blockquote class='par'>%s</blockquote>"
                 % _cit_spans(html.escape(_readable(span) or row.get("claim", ""))))
    p.append("<p class='dim'>%s The checked part is %s words, %s. %s</p>"
             % (html.escape(citation_note(span)) if span
                else "Where the citation sat is not recorded.",
                gt.get("span_words", "?"),
                "a whole sentence" if gt.get("span_is_full_sentence")
                else "a piece cut out of a longer sentence",
                "Fair question: the paper cited this to exactly one article."
                if row.get("fair") else
                "<b>Not a fair question:</b> the paper cited this to several "
                "articles and our converter kept only one, so a flag here may "
                "be the setup's fault rather than the paper's."))

    # the creators
    p.append("<div class='lbl'>what the benchmark's creators say</div>")
    p.append("<p class='sub'>Label <code>%s</code> — for us that means the "
             "citation should be <b>%s</b>.</p>"
             % (html.escape(row.get("label") or "?"),
                SIDE_WORD.get(row.get("key"), "?")))
    segs = gt.get("evidence_segments") or []
    if segs:
        p.append("<div class='lbl'>the proof sentences they marked in the paper</div>")
        for s in segs:
            body, note = in_context(s, norm_src, offsets, src)
            p.append(body or "<blockquote>%s</blockquote>" % html.escape(s))
            if note:
                p.append("<p class='flagnote'>%s</p>" % html.escape(note))
    else:
        p.append("<p class='dim'>They marked no proof sentence for this row.</p>")

    # the tool
    p.append("<div class='lbl big'>what our tool said</div>")
    p.append(tool_block(claim, norm_src, offsets, src, st["run_name"]))

    # the readers
    p.append("<div class='lbl big'>what the two blind readers said</div>")
    p.append("<p class='dim'>Each was given this sentence and the whole cited "
             "paper, and never saw the label or the tool's verdict. Both are "
             "Anthropic models — a smaller and a larger one — so they are two "
             "readings, not two independent opinions.</p>")
    for name in ("sonnet", "opus"):
        p.append(reader_block(name, readers.get(name), norm_src, offsets, src))

    # the paper
    if src and opts.full_text:
        p.append("<details class='paper'><summary>the whole cited paper "
                 "(%s characters), every quote above marked</summary>"
                 "<div class='fulltext'>%s</div></details>"
                 % (f"{len(src):,}", highlight_all(src, quotes)))
    p.append(read_the_whole_thing(gt, src_path))

    # the ruling
    p.append("""<div class='decide'>
<div class='ask'>Reading it yourself: does the cited paper support this sentence as written?</div>
<label><input type='radio' name='r-{q}' value='pass'> yes, supported</label>
<label><input type='radio' name='r-{q}' value='flag'> no, something is wrong</label>
<label><input type='radio' name='r-{q}' value='unsure'> not sure / skip</label>
<input type='text' id='n-{q}' placeholder='a note, if you want one (optional)'>
</div></article>""".format(q=qid))
    return "".join(p)


# ---------- the page -----------------------------------------------------

CSS = """
:root{--bg:#fbfaf8;--card:#fff;--ink:#1c1a17;--dim:#6b655c;--line:#e4dfd7;
--warn:#b45309;--ok:#166534;--bad:#b3261e;--accent:#1d4ed8;--mark:#fde68a}
@media(prefers-color-scheme:dark){:root{--bg:#17161a;--card:#201f24;--ink:#eceaf0;
--dim:#a09aa8;--line:#343139;--warn:#fbbf24;--ok:#86efac;--bad:#ff9d94;
--accent:#93b4ff;--mark:#5c4a00}}
body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}
.wrap{max-width:920px;margin:0 auto;padding:32px 20px 90px}
h1{font-size:26px;margin:0 0 6px}h2{font-size:20px;margin:44px 0 4px}
h3{font-size:16px;margin:22px 0 6px}
p.sub,.sub{color:var(--dim);font-size:14.5px;margin:4px 0}
.banner{background:#fff4e5;border:1px solid #f5c88a;color:#7c3b02;padding:10px 14px;
border-radius:8px;font-size:14px;margin:18px 0}
@media(prefers-color-scheme:dark){.banner{background:#3a2a10;border-color:#6b4b12;color:#fcd9a1}}
.words{border:1px solid var(--line);border-radius:8px;padding:6px 16px;margin:18px 0}
.words dt{font-weight:600;margin-top:8px}.words dd{margin:0 0 4px;color:var(--dim);font-size:14.5px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:18px 20px;margin:0 0 20px}
.cardtop{position:sticky;top:0;z-index:2;background:var(--card);
margin:-18px -20px 14px;padding:12px 20px 10px;border-bottom:1px solid var(--line);
border-radius:10px 10px 0 0;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.id{font:13px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--dim)}
.pilechip{font-size:12px;color:var(--dim);border:1px solid var(--line);
border-radius:20px;padding:1px 8px}
.chip{font-size:13px;border:1px solid var(--line);border-radius:20px;padding:1px 9px}
.chip .who{color:var(--dim)}
.chip.s-pass{color:var(--ok)}.chip.s-flag{color:var(--bad)}.chip.s-none{color:var(--dim)}
.chip .lab{font-size:11px;color:var(--dim);letter-spacing:.04em}
.agree{font-size:13px;color:var(--accent);margin-left:auto}
.lbl{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--dim);
margin:18px 0 4px}
.lbl.big{margin-top:26px;border-top:1px solid var(--line);padding-top:14px;
font-size:13px;color:var(--ink);font-weight:700}
blockquote{margin:6px 0;padding:4px 0 4px 12px;border-left:3px solid var(--line)}
blockquote.par{font-size:16.5px}blockquote.small{font-size:14px;color:var(--dim)}
.pilechip.star{color:var(--accent);border-color:var(--accent);font-weight:600}
.ask-box{background:rgba(29,78,216,.08);border-left:4px solid var(--accent);
padding:10px 14px;margin:2px 0 14px;font-size:15px;border-radius:0 6px 6px 0}
.cit{font-size:12.5px;color:var(--dim);border:1px solid var(--line);border-radius:20px;
padding:0 6px;white-space:nowrap}
.inpaper{background:rgba(125,125,125,.07);border-radius:6px;padding:9px 12px;
font-size:14.5px;margin:6px 0}
.ctx{color:var(--dim)}
mark{background:var(--mark);color:inherit;padding:0 2px;border-radius:2px}
.why{margin:4px 0;font-size:15px}
.dim{color:var(--dim);font-size:14px}
.flagnote{color:var(--warn);font-size:13.5px;margin:4px 0}
.reader{border-top:1px dashed var(--line);padding-top:10px;margin-top:12px}
.parts{margin:4px 0;padding-left:22px;font-size:14.5px}
.parts li.y::marker{color:var(--ok)}.parts li.n::marker{color:var(--bad)}
.v-supported{color:var(--ok);font-weight:600}
.v-unsupported{color:var(--bad);font-weight:600}
.v-own{color:var(--accent);font-weight:600}
details{margin:10px 0}summary{cursor:pointer;color:var(--dim);font-size:14px}
.fulltext{max-height:60vh;overflow:auto;white-space:pre-wrap;font-size:13.5px;
line-height:1.5;background:rgba(125,125,125,.06);border-radius:6px;padding:10px 12px;
margin-top:8px}
.decide{margin-top:18px;padding-top:14px;border-top:1px dashed var(--line)}
.ask{font-weight:600;margin-bottom:6px}
.decide label{margin-right:18px;font-size:14.5px}
.decide input[type=text]{width:100%;margin-top:10px;padding:7px 9px;font:inherit;
font-size:14px;border:1px solid var(--line);border-radius:6px;background:var(--bg);
color:var(--ink)}
.bar{position:sticky;bottom:0;background:var(--bg);border-top:1px solid var(--line);
padding:12px 0;margin-top:30px;display:flex;flex-wrap:wrap;gap:10px;align-items:center;z-index:3}
.topbar{position:static;border-top:none;border-bottom:1px solid var(--line);
margin:24px 0 26px;padding:0 0 12px}
button{font:inherit;font-size:14px;padding:7px 13px;border-radius:7px;cursor:pointer;
border:1px solid var(--line);background:var(--card);color:var(--ink)}
button.primary{background:var(--accent);color:#fff;border-color:transparent}
button.on{border-color:var(--accent);color:var(--accent)}
.count{color:var(--dim);font-size:14px;margin-left:auto}
.card.hide{display:none}
"""

JS = """
const KEY = "ci-problem-rows-2026-08-03";
const ROWS = __ROWS__;
let state = JSON.parse(localStorage.getItem(KEY) || "{}");
let filters = {pile:null, undecided:false, fair:false, starred:false};

function save(){ localStorage.setItem(KEY, JSON.stringify(state)); paint(); }

function agreeText(qid){
  const r = state[qid], row = ROWS[qid];
  if(!r || !r.ruling || r.ruling === "unsure") return "";
  const same = [];
  if(row.key === r.ruling) same.push("the key");
  if(row.tool === r.ruling) same.push("our tool");
  if(row.sonnet === r.ruling) same.push("Sonnet");
  if(row.opus === r.ruling) same.push("Opus");
  return same.length ? "you agree with " + same.join(", ") : "you disagree with all four";
}

function paint(){
  let decided = 0, shown = 0;
  for(const qid in ROWS){
    const r = state[qid] || {};
    if(r.ruling) decided++;
    const a = document.querySelector(`.agree[data-for="${CSS.escape(qid)}"]`);
    if(a) a.textContent = agreeText(qid);
    const card = document.getElementById(qid);
    let hide = false;
    if(filters.pile && ROWS[qid].pile !== filters.pile) hide = true;
    if(filters.undecided && r.ruling) hide = true;
    if(filters.fair && !ROWS[qid].fair) hide = true;
    if(filters.starred && !ROWS[qid].ask) hide = true;
    card.classList.toggle("hide", hide);
    if(!hide) shown++;
  }
  const line = `${decided} of ${Object.keys(ROWS).length} answered · ${shown} cards shown`;
  for(const id of ["count", "count2"]){
    const c = document.getElementById(id);
    if(c) c.textContent = line;
  }
  document.querySelectorAll("h2[data-pile]").forEach(h => {
    h.parentElement.classList.toggle("hide",
      !!filters.pile && h.dataset.pile !== filters.pile);
  });
}

document.addEventListener("change", e => {
  const t = e.target;
  if(t.name && t.name.startsWith("r-")){
    const qid = t.name.slice(2);
    state[qid] = Object.assign({}, state[qid], {ruling: t.value});
    save();
  }
});
document.addEventListener("input", e => {
  if(e.target.id && e.target.id.startsWith("n-")){
    const qid = e.target.id.slice(2);
    state[qid] = Object.assign({}, state[qid], {note: e.target.value});
    localStorage.setItem(KEY, JSON.stringify(state));
  }
});

function pile(p){
  filters.pile = (filters.pile === p) ? null : p;
  document.querySelectorAll("button[data-pile]").forEach(b =>
    b.classList.toggle("on", b.dataset.pile === filters.pile));
  paint();
}
function toggle(name, btn){
  filters[name] = !filters[name];
  btn.classList.toggle("on", filters[name]);
  paint();
}
function download(){
  const out = {page: "ci problem rows 2026-08-03", rulings: state};
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(out, null, 1)],
           {type:"application/json"}));
  a.download = "ci_problem_row_rulings.json";
  a.click();
}
function restore(){
  for(const qid in state){
    const r = state[qid] || {};
    if(r.ruling){
      const el = document.querySelector(
        `input[name="r-${CSS.escape(qid)}"][value="${r.ruling}"]`);
      if(el) el.checked = true;
    }
    if(r.note){
      const n = document.getElementById("n-" + qid);
      if(n) n.value = r.note;
    }
  }
  paint();
}
restore();
"""

INTRO = """<h1>The 73 rows worth arguing about</h1>
<p class="sub">Built {date} from the 2026-08-02 benchmark run. Every row here is
one where the four opinions on the same sentence did not all agree: the
benchmark creators' answer key, our tool, and two strong models that each read
the whole cited paper on their own. The other {clean} of the 150 rows are left
out because everyone agreed on them.</p>
<p class="sub">Each card shows the sentence in its own paragraph, the label the
creators gave it and the proof sentences they marked, our tool's verdict with
its reasoning and the sentence it put on the card, and each reader's verdict
with its reason — every quote set inside the paper text around it, so you can
see whether it was quoted in or out of context. At the bottom of the card you
answer one question, and the buttons at the foot of the page download your
answers as a file.</p>
<div class="banner"><b>Your eyes only.</b> This page shows the answer key, so it
must never go into a grading kit or in front of a rater. It also quotes real
biomedical papers word for word, which is why the Fable-safe version of this
material is a separate, reworded document.</div>
<div class="words"><h3>Words used on this page</h3><dl>
<dt>Answer key</dt><dd>The label the benchmark's creators gave a row, and what
that label means for us: <b>supported</b> or <b>flagged</b>.</dd>
<dt>Blind reader</dt><dd>A strong model that was given the sentence and the
whole cited paper and asked for its own verdict, without ever seeing the answer
key or our tool's verdict. Two of them here, Sonnet and Opus.</dd>
<dt>Supported / flagged</dt><dd>The only two answers anyone gives on this page.
<i>Supported</i> = the cited paper backs the sentence as written. <i>Flagged</i>
= anything else is wrong with the citation.</dd>
<dt>Fair question</dt><dd>The paper cited that sentence to exactly one article.
Where it cited several, our converter kept only one, so demanding that the one
kept article prove the whole sentence can be unfair — and a flag there may be
our setup's fault rather than the paper's.</dd>
<dt>Row id</dt><dd>Written <code>pilot100:cidev0007</code> or
<code>fresh50:cidev0007</code>, because each batch numbers its rows from one and
the same number means a different row in the other batch.</dd>
<dt>The arbiter</dt><dd>A second, different model our tool asks to re-read a row
it has complained about. On this page its opinion is shown but never changes the
verdict above it.</dd>
</dl></div>
"""


def build(dis_path, out_path, opts):
    dis = _read_json(dis_path)
    sets = load_sets(dis)
    piles = dis["piles"]
    rows = dis["rows"]

    js_rows, drift = {}, []
    body = []
    for pile in ("A", "B", "C", "D"):
        qids = piles.get(pile) or []
        if not qids:
            continue
        title, blurb = PILES[pile]
        body.append("<section><h2 data-pile='%s'>%s <span class='sub'>(%d rows)"
                    "</span></h2><p class='sub'>%s</p>"
                    % (pile, html.escape(title), len(qids), html.escape(blurb)))
        for qid in qids:
            row = rows[qid]
            st = sets[row["tag"]]
            claim = st["claims"].get(row["cid"])
            if claim is not None:
                recomputed = "pass" if _tool_bucket(claim) == "supported" else "flag"
                if recomputed != row["tool"]:
                    drift.append("%s: list says tool=%s, run says %s"
                                 % (qid, row["tool"], recomputed))
            readers = row.get("readers") or {}
            js_rows[qid] = {"pile": pile, "key": row.get("key"),
                            "tool": row.get("tool"),
                            "sonnet": (readers.get("sonnet") or {}).get("side"),
                            "opus": (readers.get("opus") or {}).get("side"),
                            "fair": bool(row.get("fair")),
                            "ask": qid in (getattr(opts, "questions", None) or {})}
            body.append(card(qid, row, pile, st, opts))
        body.append("</section>")

    if drift:
        sys.exit("the tool column does not match the run's own analysis.json:\n  "
                 + "\n  ".join(drift))

    n_rows = len(js_rows)
    n_starred = sum(1 for q in js_rows if js_rows[q]["ask"])
    total = len(rows)
    page = ["<!doctype html><meta charset='utf-8'>",
            "<title>Citation-Integrity — the rows worth arguing about</title>",
            "<meta name='viewport' content='width=device-width,initial-scale=1'>",
            "<style>%s</style><div class='wrap'>" % CSS,
            INTRO.replace("{date}", opts.date).replace("{clean}", str(total - n_rows)),
            "<div class='bar topbar'>",
            "".join("<button data-pile='%s' onclick=\"pile('%s')\">pile %s (%d)</button>"
                    % (p, p, p, len(piles.get(p) or [])) for p in "ABCD" if piles.get(p)),
            ("<button onclick=\"toggle('starred',this)\">start here (%d)</button>"
             % n_starred) if n_starred else "",
            "<button onclick=\"toggle('undecided',this)\">not yet answered</button>",
            "<button onclick=\"toggle('fair',this)\">fair questions only</button>",
            "<span class='count' id='count'></span></div>",
            "".join(body),
            "<div class='bar'><button class='primary' onclick='download()'>"
            "Download my answers</button>"
            "<button onclick=\"if(confirm('Clear every answer on this page?'))"
            "{localStorage.removeItem(KEY);location.reload()}\">Clear</button>"
            "<span class='count' id='count2'></span></div>",
            "</div><script>%s</script>" % JS.replace("__ROWS__", json.dumps(js_rows))]

    out_path = out_path if os.path.isabs(out_path) else os.path.join(ROOT, out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("".join(page))
    return n_rows, out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--disagreements", required=True,
                    help="the JSON written by ci_disagreement_list.py")
    ap.add_argument("--out", required=True)
    ap.add_argument("--date", default="2026-08-03")
    ap.add_argument("--questions",
                    help="JSON {row id: the one question to answer}; those rows "
                         "get a blue box and a 'start here' filter button")
    ap.add_argument("--no-full-text", dest="full_text", action="store_false",
                    help="leave the whole cited papers out (much smaller file)")
    a = ap.parse_args()
    a.questions = _read_json(a.questions) if a.questions else {}
    n, path = build(a.disagreements, a.out, a)
    print("wrote %d review cards -> %s (%.1f MB)"
          % (n, path, os.path.getsize(path) / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
