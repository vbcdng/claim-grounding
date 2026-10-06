#!/usr/bin/env python3
"""Card 86 (stage 3b): the viewer card for the new grounding path (card 85's
claim["conversion"] object), shared by viewer.py and viewer_v2.py.

Design: docs/STAGE3B_DESIGN_2026-09-30.md §4 with the author's answers of
2026-10-01 (vault page "Stage 3b"): Q5 = a, the local checker's per-part score
is saved and NOT shown; Q6 = a, both dependency notes are shown as grey lines;
Q7 = build both layouts — one card per claim with a numbered block per
sentence (default) and, with --viewer-per-sentence, a second file with one
card per sentence (`per_sentence_analysis`). ARCHITECTURE §14.23.

Worked example (the bridges claim t4 of card 85). The judge said unsupported,
so the plain check flagged it; the converter wrote two parts. The card gains
the chip "split into 2 parts: the check flagged it", the line "Not found in
the source: 'The lifetime of steel bridges near the sea is halved.'", the
line "Shadow result: this did not change the verdict." and, one click away,
each part with its result in plain words — "✓ found in the source" with the
real quoted sentence under it, "✗ not found in the source" — and every link
in words.

Display only. Nothing here is read when a claim carries no `conversion`
object, and every string this module adds is empty then, so both viewers
write byte-identical pages with the new path switched off
(tests/test_stage3b_viewer.py pins the bytes).
"""
from __future__ import annotations

import copy
import html
from typing import Any, Callable, Dict, List, Optional

TRIGGER_WORDS = {
    "plain_flag": "the check flagged it",
    "quantity_word": "it has a quantity word",
    "gate_score": "its sentence structure is complex",
    "multi_sentence": "it has more than one sentence",
}

PIECE_STATUS = {
    "proven": ("✓", "found in the source"),
    "not_found": ("✗", "not found in the source"),
    "contradicted": ("⚡", "the source says the opposite"),
}

SENTENCE_RESULT = {
    "proven": "every part was found in the source",
    "writer_own_only": "only your own words, nothing to check against the source",
    "not_proven_as_written": "not proven as written: at least one part was not found",
    "not_proven": "not proven: the source says the opposite of at least one part",
    "writer_own": "your own words: not judged against the paper",
    "cannot_be_converted": "judged whole: could not be split into checkable parts",
    "source_not_available": "not split: the cited source is not available",
    "m10_fallback": "judged whole: a part leans on more than one earlier sentence",
    "not_converted": "not split: nothing flagged this sentence",
}

CSS = """
  .aidachip { font-size:9px; font-weight:700; letter-spacing:.02em; padding:1px 6px;
    border-radius:8px; background:#ecfeff; color:#0e7490; border:1px solid #67e8f9; margin-left:4px; }
  .aidachip.reused { background:#f1f5f9; color:#475569; border-color:#cbd5e1; }
  .aida-block { font-size:12px; border:1px solid #a5f3fc; background:#f0fdff; border-radius:6px;
    padding:6px 8px; margin:6px 0; color:#164e63; }
  .aida-block .aida-fail { color:#b91c1c; margin:3px 0; }
  .aida-block .aida-shadow { color:#475569; margin:3px 0; }
  .aida-block details summary { cursor:pointer; color:#0e7490; }
  .aida-sent { margin:6px 0 2px; padding-top:4px; border-top:1px dashed #a5f3fc; }
  .aida-sent .aida-stext { font-style:italic; }
  .aida-reading { margin:4px 0 2px 6px; font-weight:600; }
  .aida-piece { margin:4px 0 4px 10px; }
  .aida-piece .aida-quote { margin:2px 0 2px 14px; padding-left:6px; border-left:3px solid #10b981;
    color:#065f46; }
  .aida-grey { color:#6b7280; margin:2px 0 2px 14px; }
  .fbtn.aidaf { border-color:#67e8f9; color:#0e7490; }
"""


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def _conv(c: Dict[str, Any]) -> Dict[str, Any]:
    cv = c.get("conversion")
    return cv if isinstance(cv, dict) else {}


def is_split(c: Dict[str, Any]) -> bool:
    """True when at least one sentence of the claim was split into parts."""
    return any(s.get("readings") for s in _conv(c).get("sentences") or [])


def shows(c: Dict[str, Any]) -> bool:
    """True when the card has anything to show for the new path: a split
    sentence, or a sentence the path looked at and judged whole or left to
    the writer."""
    return any(s.get("readings") or (s.get("result") not in (None, "not_converted"))
               for s in _conv(c).get("sentences") or [])


def any_shown(claims: List[Dict[str, Any]]) -> bool:
    return any(shows(c) for c in claims)


def n_parts(c: Dict[str, Any]) -> int:
    return sum(len(rd.get("pieces") or [])
               for s in _conv(c).get("sentences") or [] for rd in s.get("readings") or [])


def _reason(c: Dict[str, Any]) -> str:
    out: List[str] = []
    for s in _conv(c).get("sentences") or []:
        if not s.get("readings"):
            continue
        t = s.get("triggers") or {}
        for k in t.get("fired") or []:
            w = TRIGGER_WORDS.get(k, k)
            if k == "quantity_word":
                words = (t.get("quantity_word") or {}).get("words") or []
                if words:
                    w += " (" + ", ".join(f"'{x}'" for x in words[:3]) + ")"
            if w not in out:
                out.append(w)
    return "; ".join(out)


def chips(c: Dict[str, Any]) -> str:
    """The head chips: "split into N parts: why" and, when every split
    sentence came from the conversion cache, "reused from an earlier run"."""
    if not is_split(c):
        return ""
    n = n_parts(c)
    why = _reason(c)
    out = (f'<span class="aidachip" title="the new check split this claim into short '
           f'parts and judged each part alone">split into {n} part{"s" if n != 1 else ""}'
           f'{": " + _esc(why) if why else ""}</span>')
    split = [s for s in _conv(c).get("sentences") or [] if s.get("readings")]
    if split and all(s.get("conversion_reused") for s in split):
        out += ('<span class="aidachip reused" title="the split was saved by an earlier '
                'run of this text and was not asked for again; use --aida-reconvert to '
                'ask again">reused from an earlier run</span>')
    elif any(s.get("conversion_reused") for s in split):
        out += ('<span class="aidachip reused" title="some sentences were split by an '
                'earlier run of this text and not asked for again">partly reused from an '
                'earlier run</span>')
    return out


def _link_words(ln: Dict[str, Any]) -> str:
    ptr = ln.get("pointer_words") or ln.get("anchor_text") or "it"
    if isinstance(ptr, list):
        ptr = " ".join(str(x) for x in ptr)
    kind = ln.get("target_kind")
    tgt = ln.get("target_text") or ""
    if kind == "not_available":
        return f'The text does not say which &ldquo;{_esc(ptr)}&rdquo; is meant.'
    if kind == "same_entry_sentence":
        where = "another part of this sentence"
    elif kind == "earlier_text":
        where = "the earlier text, closest in"
    else:
        where = "an earlier sentence"
    return (f'&ldquo;{_esc(ptr)}&rdquo; is explained by {where}: '
            f'&ldquo;{_esc(tgt)}&rdquo;')


def _piece_row(p: Dict[str, Any], proof_row: Callable[[Dict[str, Any]], str]) -> str:
    if p.get("counted_as") == "writer_own":
        mark, words = "○", "your own words, not checked"
    else:
        mark, words = PIECE_STATUS.get(p.get("status"), ("?", str(p.get("status") or "")))
    extra = ""
    if "source less sure" in (p.get("chips") or []):
        extra += ' <span class="aidachip reused">source less sure</span>'
    if p.get("judge_error"):
        extra += ' <span class="aidachip reused">the judge call failed</span>'
    rows = (f'<div class="aida-piece">{mark} <b>{_esc(p.get("text"))}</b> '
            f'&mdash; {_esc(words)}{extra}')
    if p.get("status") == "proven":
        for pr in p.get("proof") or []:
            rows += f'<div class="aida-quote">{proof_row(pr)}</div>'
    elif p.get("status") == "contradicted" and (p.get("plain") or {}).get("reason"):
        rows += (f'<div class="aida-grey">The judge: &ldquo;'
                 f'{_esc(p["plain"]["reason"])}&rdquo;</div>')
    for ln in p.get("links") or []:
        rows += f'<div class="aida-grey">{_link_words(ln)}</div>'
    for n in p.get("notes") or []:
        rows += f'<div class="aida-grey">{_esc(n)}</div>'
    for n in p.get("dependency_notes") or []:          # the author, Q6 = a
        rows += f'<div class="aida-grey">{_esc(n)}</div>'
    hint = p.get("link_hint") or {}
    if hint.get("verdict") == "supported":
        rows += ('<div class="aida-grey">The paper cited on the earlier sentence may back '
                 'this part.</div>')
        for pr in hint.get("proof") or []:
            rows += f'<div class="aida-quote">{proof_row(pr)}</div>'
    # p["hhem_score"] is saved and NOT shown (the author's answer to Q5: not on
    # the card until it has been tested more).
    return rows + "</div>"


def _sentence_block(s: Dict[str, Any], numbered: bool,
                    proof_row: Callable[[Dict[str, Any]], str]) -> str:
    head = ""
    if numbered:
        head = (f'<div><b>Sentence {s.get("index")} of {s.get("of")}:</b> '
                f'<span class="aida-stext">{_esc(s.get("text"))}</span></div>')
    res = s.get("result")
    out = f'<div class="aida-sent">{head}'
    out += f'<div>Result: {_esc(SENTENCE_RESULT.get(res, res or ""))}.'
    if s.get("conversion_reused"):
        out += ' <span class="aidachip reused">reused from an earlier run</span>'
    out += "</div>"
    reach = s.get("citation_reach") or {}
    if reach.get("answer") == "unsure":
        out += ('<div class="aida-grey">This sentence has no citation of its own, and it is '
                'not clear whether the citation at the end of the passage covers it.</div>')
    readings = s.get("readings") or []
    if len(readings) > 1:
        out += '<div>This sentence can be read two ways.</div>'
    for rd in readings:
        if len(readings) > 1:
            out += f'<div class="aida-reading">Reading {int(rd.get("reading", 0)) + 1}:</div>'
        for p in rd.get("pieces") or []:
            out += _piece_row(p, proof_row)
    if s.get("note") and res != "not_converted":
        out += f'<div class="aida-grey">{_esc(s["note"])}</div>'
    return out + "</div>"


def block(c: Dict[str, Any], proof_row: Callable[[Dict[str, Any]], str]) -> str:
    """The always-visible block on the card ("" when nothing to show). The
    named failing parts and the shadow line are always visible; the full
    list of parts opens on a click (the design's simple-mode rule)."""
    if not shows(c):
        return ""
    cv = _conv(c)
    sents = cv.get("sentences") or []
    failing = [t for s in sents for t in s.get("failing_parts") or []]
    out = '<div class="aida-block">'
    if failing:
        out += ('<div class="aida-fail">Not found in the source: '
                + "; ".join(f'&ldquo;{_esc(t)}&rdquo;' for t in failing) + '</div>')
    vc = cv.get("verdict_changed")
    if vc:
        out += (f'<div class="aida-shadow">The new check changed the verdict from '
                f'{_esc(vc.get("from"))} to {_esc(vc.get("to"))}.</div>')
    else:
        out += '<div class="aida-shadow">Shadow result: this did not change the verdict.</div>'
    shown = [s for s in sents if s.get("readings") or s.get("result") not in (None, "not_converted")]
    numbered = len(sents) > 1
    body = "".join(_sentence_block(s, numbered, proof_row) for s in (sents if numbered else shown))
    n = n_parts(c)
    label = ((f"show the {n} parts and their results" if n != 1
              else "show the part and its result") if n else "show what the new check did")
    out += f'<details class="aida-parts"><summary>{label}</summary>{body}</details>'
    return out + "</div>"


def card_class(c: Dict[str, Any]) -> str:
    return " aidasplit" if is_split(c) else ""


def filter_button(claims: List[Dict[str, Any]]) -> str:
    n = sum(1 for c in claims if is_split(c))
    if not n:
        return ""
    return (f'<button class="fbtn aidaf" data-f="aidasplit" title="claims the new check '
            f'split into short parts, each judged alone">Split into parts ({n})</button>')


def css(claims: List[Dict[str, Any]]) -> str:
    return CSS if any_shown(claims) else ""


# ------------------------------------------------------------------ per-sentence layout

def per_sentence_analysis(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """A copy of the analysis for the second layout (the author's Q7): every
    claim whose conversion holds more than one sentence becomes one card per
    sentence. Each sentence card keeps the parent claim's verdict and proof
    (the verdict belongs to the claim) and carries only its own sentence of
    the conversion; `aida_parent` names the claim. Other claims are unchanged.

    Worked example: claim t12 with sentences t12.1, t12.2 and t12.3 gives
    three cards t12.1, t12.2 and t12.3, each showing t12's verdict and one
    sentence's parts."""
    a = copy.deepcopy(analysis)
    out = []
    for c in a.get("text_claims") or []:
        sents = _conv(c).get("sentences") or []
        if len(sents) < 2:
            out.append(c)
            continue
        for s in sents:
            sc = copy.deepcopy(c)
            sc["id"] = s.get("id") or f'{c["id"]}.{s.get("index")}'
            sc["text"] = s.get("text") or ""
            sc["aida_parent"] = c["id"]
            cv = dict(_conv(c))
            cv["sentences"] = [s]
            cv["result"] = s.get("result")
            sc["conversion"] = cv
            out.append(sc)
    a["text_claims"] = out
    return a


PER_SENTENCE_FILES = ("viewer_per_sentence.html", "viewer_v2_per_sentence.html")


def write_per_sentence_viewers(analysis: Dict[str, Any], output_dir: str, title: str,
                               source_texts: Optional[Dict[str, str]] = None,
                               assessment: Optional[Dict[str, Any]] = None) -> List[str]:
    """Write both viewers in the one-card-per-sentence layout next to the
    default files (--viewer-per-sentence). analysis.json is not touched."""
    import os
    from . import viewer, viewer_v2
    a = per_sentence_analysis(analysis)
    paths = []
    for fn, mod in zip(PER_SENTENCE_FILES, (viewer, viewer_v2)):
        p = os.path.join(output_dir, fn)
        mod.generate(a, p, title=title + " (one card per sentence)",
                     source_texts=source_texts, assessment=assessment)
        paths.append(p)
    return paths


def parent_note(c: Dict[str, Any]) -> str:
    """The grey line a per-sentence card carries ("" on a normal card)."""
    if not c.get("aida_parent"):
        return ""
    return (f'<div class="aida-grey">This sentence is part of claim '
            f'{_esc(c["aida_parent"])}; the verdict and proof shown are the whole '
            f'claim&rsquo;s.</div>')
