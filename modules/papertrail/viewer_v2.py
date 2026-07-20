"""
Viewer v2 — the 2026-07-15 owner-approved card redesign (docs/VIEWER_V2_DESIGN.md).

Written as `viewer_v2.html` NEXT TO the untouched v1 `viewer.html` for the
side-by-side comparison period; v1 stays the shipped default until the owner
retires it. Both viewers derive the same RUN_ID and reuse the same REVIEW_JS,
so review-triage marks (localStorage) are shared between them.

What changes vs v1 (all display-only; analysis.json and every verdict field
are untouched):
  • A supported claim whose NOT-PROVEN-AS-WRITTEN flag SURVIVES the arbiter is
    a full AMBER card (color, left-panel highlight, own filter) — the third
    display state between green and red. The gap line naming the unproven part
    and the arbiter's suggested rewrite are always visible.
  • Supported/amber cards show ALL per-part proof sentences (the covering-set
    rows) as the main evidence display, each with source links — not just the
    judge's single pick.
  • Unsupported cards: the reason is always visible; "◐ Partly proven despite
    the verdict" is its own named expander WITH source links; everything else
    (arbiter reading, non-supporting passages, fix suggestions) sits behind a
    "▸ more checks" expander that names its contents.
  • The review-triage row is always visible (slim), on every card.
  • Expert view = show internals chips + open every expander; no per-card
    "details & review" button.

Standalone regeneration from a finished run (no LLM, no network):
    python3 -m modules.papertrail.viewer_v2 <run_dir>
"""

import os
import sys
import json
import hashlib
import logging
from typing import Dict, Any, Optional, List, Tuple

from modules.papertrail.viewer import (
    _esc, _confidence, _filename_map, _paper_meta, _paper_link,
    _coverage_bars, _assessment_panel, _review_data,
    _source_actions, _fix_section, _clamped_quote, _is_scoped, _omitted_card,
    _norm_ws, _SECONDHAND_RE, _DISAGREE_RE, REVIEW_JS,
    OMITTED_SHOWN, OMITTED_EMBED_CAP)

logger = logging.getLogger(__name__)


def _display_class(c: Dict[str, Any]) -> str:
    """The v2 display state. NEVER derived into the verdict field — this is
    the card/highlight color only (docs/VIEWER_V2_DESIGN.md decision 1)."""
    if c["verdict"] == "own":
        return "own"
    if _is_scoped(c):
        return "scopedcite"
    if c["verdict"] == "unsupported":
        return "unsupported"
    if c.get("proof_state") == "partial":
        return "amber"
    return "supported"


def _claim_span_v2(c: Dict[str, Any]) -> str:
    """Left-panel sentence highlight; like v1 but surviving ambers are amber."""
    cls = _display_class(c)
    markers = "".join(f'<sup class="mark" title="citation marker — the source this sentence cites">[{_esc(m)}]</sup>' for m in c.get("markers", []))
    if (c.get("prev") or {}).get("changed"):
        markers += '<sup class="changedmark" title="edited since the last run">✎</sup>'
    rescue = c.get("tail_rescue") or {}
    if rescue.get("supported"):
        return (f'<span class="claim own" data-card="card-{c["id"]}" title="{_esc(c["id"])} — your own lead-in" '
                f'onclick="brush(\'{c["id"]}\', \'text\')">{_esc(rescue["lead_in"])}</span> '
                f'<span class="claim {cls}" id="text-{c["id"]}" '
                f'data-card="card-{c["id"]}" title="{_esc(c["id"])} — click to jump to this claim\'s card" onclick="brush(\'{c["id"]}\', \'text\')">'
                f'{_esc(rescue["tail"])}{markers}</span> ')
    return (f'<span class="claim {cls}" id="text-{c["id"]}" '
            f'data-card="card-{c["id"]}" title="{_esc(c["id"])} — click to jump to this claim\'s card" onclick="brush(\'{c["id"]}\', \'text\')">'
            f'{_esc(c["text"])}{markers}</span> ')


def _proof_row(parts: List[str], sentence: str, source_title: str, paper_id: str,
               page: Optional[int], snippet: str, fname_map: Dict[str, str],
               source_texts: Dict[str, str], paper_meta: Dict[str, Dict[str, str]],
               rescue_tag: str = "") -> str:
    """One main-display evidence row: ✓ part(s) → the proving sentence →
    source links. The unit of the v2 supported/amber card."""
    actions = _source_actions(fname_map, paper_id, page, sentence, snippet, source_texts)
    plink = _paper_link(paper_meta, paper_id, source_title)
    parts_html = " · ".join(f'✓ {_esc(p)}' for p in parts)
    return (f'<div class="proof{" rescued" if rescue_tag else ""}">'
            f'<div class="part">{parts_html}{rescue_tag}</div>'
            f'{_clamped_quote(sentence)}'
            f'<div class="srcline"><span class="srcname">{_esc(source_title)}</span> {plink}'
            f'<button class="copy" onclick="copyText(this, event)" title="copy this sentence to the clipboard" data-quote="{_esc(sentence)}">Copy</button></div>'
            f'{actions}</div>')


def _covering_rows(c: Dict[str, Any], fname_map: Dict[str, str],
                   source_texts: Dict[str, str],
                   paper_meta: Dict[str, Dict[str, str]]) -> Tuple[str, str]:
    """(main proof rows, context-spans html for the expander) from the
    covering-set payload. Groups ADJACENT parts proven by the same sentence
    (owner r2 t5); a part proven by several sentences shows them all."""
    cov = c.get("covering") or {}
    groups = []
    for ce in (cov.get("covered") or []):
        sentence = ce.get("sentence") or ""
        if not sentence:
            continue
        part = ce.get("component") or ""
        if groups and groups[-1]["sentence"] == sentence:
            if part not in groups[-1]["parts"]:
                groups[-1]["parts"].append(part)
        else:
            groups.append({"sentence": sentence, "parts": [part],
                           "paper_id": ce.get("paper_id"), "page": ce.get("page"),
                           "snippet": ce.get("snippet", ""),
                           "source_title": ce.get("source_title") or ""})
    rows = "".join(_proof_row(g["parts"], g["sentence"], g["source_title"],
                              g["paper_id"], g["page"], g["snippet"],
                              fname_map, source_texts, paper_meta)
                   for g in groups)
    spans = ""
    for sp in (cov.get("spans") or []):
        span_text = sp.get("text") or ""
        if not span_text:
            continue
        spans += (f'<details class="covspan"><summary>Read it in context — '
                  f'the {sp.get("n_used", "?")} used sentence'
                  f'{"s" if sp.get("n_used") != 1 else ""} with the original '
                  f'text between them ({_esc(sp.get("source_title") or "")})'
                  f'</summary><div class="judged-text">{_esc(span_text)}</div>'
                  f'</details>')
    return rows, spans


def _evidence_blocks(c: Dict[str, Any], fname_map: Dict[str, str],
                     source_texts: Dict[str, str],
                     paper_meta: Dict[str, Dict[str, str]]):
    """v1's per-cited-source evidence rows, kept intact but returned for
    PLACEMENT by the caller (visible fallback on supported cards without a
    covering payload; otherwise inside the "more checks" expander).
    Returns (blocks_html, n_supporting, n_other, secondhand_hits, disagree_rows)."""
    evidences = c.get("evidences")
    if evidences is None:
        ev = c.get("evidence")
        evidences = [ev] if ev else []
    blocks, secondhand_hits, disagree_rows = "", [], []
    n_sup = n_other = 0
    for e in evidences:
        sentence = e.get("sentence") or ""
        fulltext = e.get("via") == "llm_fulltext"
        if e.get("supported"):
            n_sup += 1
            chip = ('<span class="srcchip ok" title="this source\'s own passage was '
                    'judged to support the claim">supports</span>')
            m = _SECONDHAND_RE.search(sentence)
            if m:
                secondhand_hits.append((e.get("source_title") or "", m.group(0).strip()))
        elif not sentence:
            n_other += 1
            chip = ('<span class="srcchip no" title="nothing in this source was even '
                    'close — possibly the wrong source for this claim">no relevant '
                    'passage found</span>')
        elif fulltext:
            n_other += 1
            chip = ('<span class="srcchip no" title="the best passage a full-text read '
                    'found — judged NOT to support the claim on its own; shown so you '
                    'can see what was checked">relevant — not enough alone</span>')
        else:
            n_other += 1
            chip = ('<span class="srcchip no" title="the closest passage by similarity — '
                    'judged NOT to support the claim; shown so you can see what was '
                    'checked">closest — not supporting</span>')
        if not e.get("supported") and _DISAGREE_RE.search(e.get("reason") or ""):
            disagree_rows.append((e.get("source_title") or "", e.get("reason") or ""))
        if sentence:
            actions = _source_actions(fname_map, e.get("paper_id"), e.get("page"),
                                      sentence, e.get("snippet", ""), source_texts)
            body = (f'{_clamped_quote(sentence)}'
                    f'<button class="copy" onclick="copyText(this, event)" title="copy this sentence to the clipboard" data-quote="{_esc(sentence)}">Copy</button>'
                    f'{actions}')
            window = e.get("window")
            if window and _norm_ws(window) != _norm_ws(sentence):
                body += (f'<details class="judged"><summary>Context — what the judge read '
                         f'({len(window.split())} words)</summary>'
                         f'<div class="judged-text">{_esc(window)}</div></details>')
        else:
            # Friend feedback (2026-07-19): the row that TELLS the reader to open
            # the source must let them — open/side-window buttons render even with
            # no sentence to highlight (PDF opens at the start, text un-highlighted).
            body = ('<div class="reason">No relevant passage found in this source — '
                    'nothing was close enough to quote. If you believe the source '
                    'does back the claim, open it and check; the search can miss.</div>'
                    + _source_actions(fname_map, e.get("paper_id"), e.get("page"),
                                      "", "", source_texts))
        plink = _paper_link(paper_meta, e.get("paper_id"), e.get("source_title") or "")
        blocks += f"""
          <div class="evidence">
            <div class="ev-label">{chip} {_esc(e.get('source_title') or '')} {plink}</div>
            {body}
          </div>"""
    return blocks, n_sup, n_other, secondhand_hits, disagree_rows


def _coverage_ratio_bar_v2(n_green: int, n_partly: int, n_uns: int,
                           n_unverifiable: int, n_own: int) -> str:
    """The one-glance stacked bar, in v2's three-state vocabulary (owner 7/15
    round 3 #6): the amber display state is its own segment, and "supported"
    counts only fully-proven green cards — same numbers as the filter buttons."""
    judged_uns = max(n_uns - n_unverifiable, 0)
    total = n_green + n_partly + judged_uns + n_unverifiable + n_own
    if total == 0:
        return ""
    # Segment hues match v2's card palette exactly (one hue per meaning;
    # friend feedback #2).
    segs = [("supported", n_green, "#10b981"),
            ("not proven as written", n_partly, "#f59e0b"),
            ("unsupported", judged_uns, "#ef4444"),
            ("unverifiable", n_unverifiable, "#9ca3af"),
            ("your own", n_own, "#6366f1")]
    bar, legend = [], []
    for label, n, color in segs:
        if not n:
            continue
        pct = n / total * 100
        bar.append(f'<div class="ccseg" style="width:{pct:.1f}%;background:{color}" '
                   f'title="{label}: {n} ({pct:.0f}%)"></div>')
        legend.append(f'<span class="ccleg"><i style="background:{color}"></i>'
                      f'{label} {n}</span>')
    return (f'<div class="covratio" title="the whole document at a glance — every claim '
            f'by its card color"><div class="ccbar">{"".join(bar)}</div>'
            f'<div class="cclegend">{"".join(legend)}</div></div>')


def _triage_row(claim_id: str) -> str:
    """Always-visible slim triage (decision 3). Same classes/data attributes
    as v1 so REVIEW_JS drives it unchanged (shared localStorage marks)."""
    return (f'<div class="triage" data-id="{claim_id}" onclick="event.stopPropagation()">'
            f'<button class="cbtn" title="mark this card as reviewed by you — checked with no repair marks means \'looked, it is fine\'">✓ checked</button>'
            f'<span class="tlabel">repair:</span>'
            f'<button class="tbtn" data-mark="wrong_source" title="the claim is fine but the cited source does not back it — cite a different one">wrong source</button>'
            f'<button class="tbtn" data-mark="rewrite" title="the text overclaims — rewrite it to match the evidence">rewrite text</button>'
            f'<button class="tbtn" data-mark="more_support" title="the claim may be right but the shown evidence doesn\'t prove it — first hunt the source(s) for stronger supporting sentences; only rewrite if no proof exists">find proof / rewrite</button>'
            f'<button class="tbtn" data-mark="verdict_wrong" title="I disagree with the tool\'s verdict — feedback, no text change">verdict wrong</button>'
            f'<button class="tbtn" data-mark="needs_citation" title="this passage should cite a source — find one and add a [[key]] citation">needs citation</button>'
            f'<button class="tbtn" data-mark="other" title="anything else — write it in the note; the fixer surfaces it to you instead of acting on its own">other</button>'
            f'<textarea class="tnote" placeholder="optional note for the fixer" rows="2"></textarea>'
            f'</div>')


def _card_v2(c: Dict[str, Any], fname_map: Dict[str, str], source_texts: Dict[str, str],
             paper_meta: Dict[str, Dict[str, str]]) -> str:
    verdict = c["verdict"]
    dclass = _display_class(c)
    cscope = c.get("citation_scope") or {}
    method = c.get("method", "")
    ab = c.get("arbiter") or {}
    cov = c.get("covering") or {}
    resd = cov.get("arbiter_resolution")

    badge, badge_cls, badge_title = {
        "supported":  ("SUPPORTED", "supported",
                       "every part of this claim has a quoted proof sentence from the "
                       "cited source — not that the source is strong or the claim is true"),
        "amber":      ("NOT PROVEN AS WRITTEN", "amber",
                       "judged supported overall, but the amber line names a part with "
                       "no proof found — read that part skeptically"),
        "unsupported": ("UNSUPPORTED", "unsupported",
                        "no cited source was found to back this claim as written"),
        "scopedcite": (f"SCOPED CITATION ({(cscope.get('scope') or '').upper()})", "scoped",
                       "the citation backs only a named fragment of this passage — "
                       "not an authoring error"),
        "own":        ("YOUR OWN CLAIM", "own",
                       "your uncited claim — thesis, argument, transition; nothing was checked"),
    }[dclass]

    vis_chips = ""     # shown always
    x_chips = ""       # expert view only (internals)
    key_html = ""      # always-visible verdict explanation blocks
    more = []          # (label, html) sections for the "more checks" expander
    extra_details = "" # standalone named expanders (partly-proven, why-own)
    proofs = ""        # main-display proof rows

    if c.get("cosine") is not None:
        x_chips += f'<span class="xchip">cosine {c["cosine"]}</span>'
    if method and method != "none":
        x_chips += f'<span class="xchip">method: {_esc(method)}</span>'
    if dclass == "amber":
        x_chips += '<span class="xchip">verdict field: supported</span>'

    conf = _confidence(c)
    conf_cls = ""
    if conf:
        conf_cls = f" conf-{conf[0]}"
        vis_chips += (f'<span class="confchip {conf[0]}" title="{_esc(conf[1])}">'
                      f'{conf[0]} confidence</span>')

    # Model call died while judging this claim — verdict may be an outage
    # artifact; a plain re-run retries it (rerun.reusable skips reuse).
    if c.get("judge_error"):
        vis_chips += ('<span class="jechip" title="the model API stopped '
                      'responding while this claim was being judged — the '
                      'verdict may be an artifact of the outage, not the '
                      'sources. Re-running the same command retries just these '
                      'claims.">⚠ not fully judged — API failed</span>')

    # ---------- lead-in split (tail_rescue) ----------
    rescue = c.get("tail_rescue") or {}
    if verdict == "supported" and rescue.get("supported"):
        x_chips += '<span class="leadin-chip">lead-in</span>'
        claim_html = (f'<span class="leadin">{_esc(rescue["lead_in"])}</span> '
                      f'{_esc(rescue["tail"])}')
        more.append(("lead-in", '<div class="leadin-note">✓ The <b>cited assertion</b> is '
                     'supported. The sentences before it are your own lead-in — not covered '
                     'by the citation, nothing was checked.</div>'))
    else:
        claim_html = _esc(c["text"])

    # ---------- main body per display state ----------
    evid_html, n_evsup, n_evother, secondhand_hits, disagree_rows = \
        _evidence_blocks(c, fname_map, source_texts, paper_meta)

    if dclass in ("supported", "amber"):
        rows, spans = _covering_rows(c, fname_map, source_texts, paper_meta)
        unc_all = [u for u in (cov.get("uncovered") or []) if u]
        common_set = set(cov.get("common_knowledge") or [])
        common = [u for u in unc_all if u in common_set]
        unc = [u for u in unc_all if u not in common_set]

        if dclass == "amber" and unc:
            second = (" A second model re-read the full source and found no proof "
                      "for it either." if ab.get("action") else "")
            part_word = "this part" if len(unc) == 1 else "these parts"
            key_html += ('<div class="gapline" title="the named part of the claim has no '
                         'proof in the cited source(s) — that is why this card is amber">'
                         '⚠ No proof found for: <b>'
                         + '</b> · <b>'.join(_esc(u) for u in unc)
                         + f'</b>.{second} The rest of the claim is proven below — '
                           f'rewrite or re-cite {part_word}.</div>')
            if ab.get("action") == "add_citation_or_rewrite" and ab.get("rewrite_suggestion"):
                key_html += (f'<div class="rewrite" title="the arbiter\'s proposal for a '
                             f'version of the sentence the cited source actually proves — '
                             f'yours to accept, edit, or ignore">✍ Suggested rewrite: '
                             f'&ldquo;{_esc(ab["rewrite_suggestion"])}&rdquo;'
                             f'<button class="copy" onclick="copyText(this, event)" '
                             f'title="copy the suggested rewrite to the clipboard" data-quote="{_esc(ab["rewrite_suggestion"])}">Copy rewrite</button></div>')
        if common:
            # Owner ruling (round 4, t1): grey and quiet, but never hidden.
            key_html += ('<div class="covset-common">◦ Not checked — commonly known: <b>'
                         + '</b> · <b>'.join(_esc(u) for u in common)
                         + '</b>. The tool judged this an everyday fact that needs '
                           'no citation; if it matters to your argument, cite it anyway.</div>')

        # Amber resolution (t5-class): the gap's arbiter-fetched verbatim proofs
        # render as a teal proof row, so the reader sees ALL the evidence in one
        # place; the history note goes to the expander.
        if c.get("proof_state") == "arbiter_resolved" and resd:
            vis_chips += ('<span class="rescuechip" title="this card was flagged '
                          '&quot;not proven as written&quot;; the arbiter re-read the '
                          'full source and found verbatim-verified proof, so the flag '
                          'was cleared — the verdict itself never changed">'
                          '⛑ gap closed by arbiter</span>')
            tag = ('<span class="rescuetag" title="proof located by the arbiter, '
                   'verified verbatim against the source">⛑ found by the arbiter</span>')
            gap_parts = unc or ["the flagged gap"]
            quotes = "".join(f'{_clamped_quote(q)}' for q in (resd.get("proofs") or []))
            proofs += (f'<div class="proof rescued"><div class="part">'
                       + " · ".join(f'✓ {_esc(p)}' for p in gap_parts) + f' {tag}</div>'
                       f'{quotes}</div>')
            more.append(("arbiter", f'<div class="rescue-note">⛑ This card was flagged '
                         f'&ldquo;not proven as written&rdquo; because the displayed '
                         f'sentences did not prove every part. The arbiter '
                         f'({_esc(resd.get("model") or ab.get("model") or "arbiter")}) '
                         f're-read the full source and found the proof quoted above. '
                         f'{_esc(resd.get("why") or "")} The verdict was always supported.</div>'))

        if rows:
            proofs = rows + proofs
            if spans:
                more.append(("context", spans))
        elif evid_html and n_evsup:
            # No covering payload (combined verdicts, pre-covering analyses):
            # fall back to the v1 per-source rows as the visible evidence.
            proofs += evid_html
            evid_html = ""

        if method in ("combined", "combined_fulltext"):
            more.append(("how it was judged",
                         '<div class="combined-note">✓ Supported by the cited sources '
                         '<b>together</b> — no single source states it alone.</div>'))
        elif method == "component_rescue":
            more.append(("how it was judged",
                         '<div class="combined-note">✓ Supported <b>piece by piece</b>: no single '
                         'passage states everything, so each part of the claim was verified '
                         'separately in the source and the combination re-judged.</div>'))
        elif method == "arbiter_rescue":
            vis_chips += ('<span class="rescuechip" title="originally judged unsupported; the '
                          'arbiter found the proof sentences (verified verbatim against the '
                          'source) and the primary judge re-judged them unanimously positive">'
                          '⛑ arbiter rescue</span>')
            more.append(("arbiter", '<div class="rescue-note">⛑ This claim was first judged '
                         'unsupported because the shown evidence missed the proving sentences. '
                         'The arbiter located them (each verified verbatim against the source) '
                         'and the primary judge confirmed unanimously.</div>'))

        # Multi-citation OR semantics + the non-supporting co-cited rows.
        if n_evsup and n_evother and evid_html:
            more.append((f'{n_evother} co-cited source{"s" if n_evother != 1 else ""} '
                         f'without a supporting passage',
                         '<div class="multisource-note">ℹ A claim counts as supported when at '
                         'least ONE cited source backs it; the rows below are the other cited '
                         'sources\' best-found passages, shown for reference. (A separate check '
                         'flags claims where a specific part appears in NONE of the cited '
                         'sources — the &ldquo;partial support?&rdquo; flag.)</div>' + evid_html))
        elif evid_html and rows:
            # covering rows carry the display; the raw judge rows stay reachable
            more.append(("the judge's original evidence rows", evid_html))

    elif dclass in ("unsupported", "scopedcite"):
        if dclass == "scopedcite":
            sa = cscope.get("scoped_assertion") or ""
            key_html += ('<div class="scope-note">✎ This passage mainly describes the '
                         'authors’ own work; the citation was read as a '
                         f'<b>{_esc(cscope.get("scope") or "")}</b> pointer'
                         + (f' backing only: &ldquo;{_esc(sa)}&rdquo;' if sa else "")
                         + '. The cited source was never asserted to prove the whole '
                           'passage — read this as “not applicable” rather than an '
                           'authoring error.</div>')
        elif c.get("reason"):
            key_html += f'<div class="unsupp-note">✗ Not supported: {_esc(c["reason"])}</div>'

        # "Partly proven despite the verdict" — its own named expander, rows
        # WITH source links (v2 fix; v1 quoted the sentence link-less).
        cc = c.get("component_check") or {}
        found, missing = cc.get("found") or [], cc.get("missing") or []
        if found:
            ev_by_comp = {}
            for x in (cc.get("evidence") or []):
                if x.get("sentence") and x.get("component") not in ev_by_comp:
                    ev_by_comp[x.get("component")] = x
            rows = ""
            for p in found:
                x = ev_by_comp.get(p)
                if x:
                    rows += _proof_row([p], x["sentence"], x.get("source_title") or "",
                                       x.get("paper_id"), x.get("page"), "",
                                       fname_map, source_texts, paper_meta)
                else:
                    rows += f'<div class="proof"><div class="part">✓ {_esc(p)}</div></div>'
            tail = ""
            if missing:
                ml = "; ".join(f'&ldquo;{_esc(x)}&rdquo;' for x in missing)
                tail = (f'<div class="compcheck-missing">✗ Not found in the cited '
                        f'sources: {ml} — support these parts elsewhere, or they may '
                        f'be wrong.</div>')
            else:
                tail = ('<div class="compcheck-tail">But the judges did not accept '
                        'that these pieces together prove the whole claim — read '
                        'the evidence and decide yourself.</div>')
            extra_details += (f'<details class="x"><summary>◐ Partly proven despite the '
                              f'verdict — {len(found)} part{"s" if len(found) != 1 else ""} '
                              f'{"were" if len(found) != 1 else "was"} found</summary>'
                              f'<div class="xbody">{rows}{tail}</div></details>')
        elif missing:
            more.append(("missing parts",
                         '<div class="compcheck-missing">✗ Not found in the cited sources: '
                         + "; ".join(f'&ldquo;{_esc(x)}&rdquo;' for x in missing)
                         + ' — support these parts elsewhere, or they may be wrong.</div>'))

        # "What was checked" — its own one-click expander right under the red
        # box (owner 7/15 #10), not lumped into "more checks".
        if evid_html:
            n_src = n_evsup + n_evother
            extra_details += (f'<details class="x"><summary title="every cited source was '
                              f'searched; these are the closest passages found — none was '
                              f'judged to prove the claim on its own">▸ what was checked — '
                              f'best passage{"s" if n_src != 1 else ""} from {n_src} cited '
                              f'source{"s" if n_src != 1 else ""}</summary>'
                              f'<div class="xbody">{evid_html}</div></details>')
        elif c.get("paper_ids"):
            # No evidence rows at all — the cited sources must still be openable
            # from the card (friend feedback 2026-07-19): one expander, a row per
            # cited paper with open + side-window buttons.
            rows = ""
            for pid in c.get("paper_ids") or []:
                acts = _source_actions(fname_map, pid, None, "", "", source_texts)
                if acts:
                    title = (paper_meta.get(pid) or {}).get("title") or fname_map.get(pid, pid or "")
                    rows += (f'<div class="evidence"><div class="ev-label">{_esc(title)} '
                             f'{_paper_link(paper_meta, pid, title)}</div>{acts}</div>')
            if rows:
                n_pid = len(c.get("paper_ids") or [])
                extra_details += (f'<details class="x"><summary>▸ open the cited '
                                  f'source{"s" if n_pid != 1 else ""}</summary>'
                                  f'<div class="xbody">{rows}</div></details>')

        # Owner 7/15 #11: no CLI command on reader-facing cards — passing "" keeps
        # only the precomputed rewrite box (when a --fix-claim result exists).
        fix_html = _fix_section(c, "")
        if fix_html:
            more.append(("suggested fix", fix_html))

        split = ([c.get("votes")] + [e.get("votes") for e in (c.get("evidences") or [])]).count("2-1") > 0
        if split:
            more.append(("borderline", '<div class="borderline-note">⚖ Borderline: the judges '
                         'split 2–1 on this one — read the evidence and decide yourself.</div>'))

    else:   # own
        ok = c.get("own_kind") or {}
        if ok.get("kind") == "fact":
            vis_chips += ('<span class="citechip loud" title="this uncited passage asserts a '
                          'checkable fact">📎 citation needed?</span>')
            key_html += (f'<div class="cite-note">📎 This uncited passage asserts a checkable '
                         f'fact — consider adding a [[key]] citation. '
                         f'&ldquo;{_esc(ok.get("reason") or "")}&rdquo; '
                         f'Nothing was checked against any source; this is a prompt, not a verdict.</div>')
        elif ok.get("kind"):
            x_chips += (f'<span class="kindchip" title="{_esc(ok.get("reason") or "")}">'
                        f'{_esc(ok["kind"])}</span>')
        why = ('No citation — the tool treats this as your own idea, argument, or '
               'transition. Nothing was checked; add a [[key]] marker if it should '
               'be grounded in a source.')
        if ok.get("kind") and ok.get("kind") != "fact":
            why += (f' Classified <b>{_esc(ok["kind"])}</b>: {_esc(ok.get("reason") or "")}')
        extra_details += (f'<details class="x"><summary>▸ why is this “your own claim”?'
                          f'</summary><div class="xbody"><div class="own-note">{why}</div>'
                          f'</div></details>')

    # ---------- cross-state nudges (into "more checks") ----------
    ps = c.get("partial_support") or {}
    partial_cls = ""
    if ps:
        partial_cls = " partial"
        esc_note = (" (Checked against the sources' full decomposed claims, not just "
                    "the matched passages.)" if ps.get("escalated") else "")
        x_chips += ('<span class="partialchip" title="the cited sources back only part of '
                    'this claim — a component was not found">partial support?</span>')
        html_ps = (f'<div class="partial-note">◑ Partial support: the sources back the claim '
                   f'in general, but a specific component appears in none of them — '
                   f'&ldquo;{_esc(ps.get("reason") or "")}&rdquo;{esc_note} '
                   f'The verdict above is unchanged — read the evidence and confirm that part.</div>')
        for h in (ps.get("component_hunt") or []):
            comp = _esc(h.get("component") or "")
            found_in = h.get("found_in") or []
            if found_in:
                ft = ", ".join(f'<b>{_esc(f.get("source_title") or f.get("key") or "?")}</b>'
                               for f in found_in)
                html_ps += (f'<div class="hunt-note">🔎 The missing part (&ldquo;{comp}&rdquo;) '
                            f'may be covered by {ft} — a full-text probe of your other '
                            f'downloaded sources found it there. Consider citing that source '
                            f'for this part.</div>')
            else:
                html_ps += (f'<div class="hunt-note">🔎 A full-text search of your other '
                            f'downloaded sources did not find &ldquo;{comp}&rdquo; either — '
                            f'source that part elsewhere, or it may simply be wrong.</div>')
        more.append(("partial support", html_ps))

    oc = c.get("over_citation") or {}
    overcite_cls = ""
    if oc.get("sources"):
        overcite_cls = " overcite"
        oc_titles = ", ".join(_esc(s.get("source_title") or s.get("paper_id") or "?")
                              for s in oc["sources"])
        x_chips += ('<span class="overchip" title="the other cited sources already cover '
                    'this claim — this citation adds nothing detectable">over-cited?</span>')
        more.append(("over-citation", f'<div class="overcite-note">◔ Possible over-citation: '
                     f'{oc_titles} — the remaining cited sources already cover the claim, and '
                     f'this one does not back it on its own. Check whether the citation belongs '
                     f'on a different sentence, or trim it. Not a verdict — nothing is wrong '
                     f'with the claim itself.</div>'))

    if verdict == "supported" and secondhand_hits:
        cites = "; ".join(f'&ldquo;{_esc(m)}&rdquo; in {_esc(t or "?")}'
                          for t, m in secondhand_hits[:2])
        x_chips += ('<span class="shchip" title="the supporting sentence itself cites '
                    'another work — consider citing the original">secondhand evidence?</span>')
        more.append(("secondhand evidence", f'<div class="sh-note">↩ The supporting sentence '
                     f'itself carries a citation ({cites}) — the cited source may be relaying '
                     f'someone else\'s finding. Consider citing the original work directly.</div>'))

    if verdict == "supported" and disagree_rows:
        dt = "; ".join(f'<b>{_esc(t or "?")}</b>: &ldquo;{_esc(r)}&rdquo;'
                       for t, r in disagree_rows[:2])
        x_chips += ('<span class="disagreechip" title="a co-cited source\'s evidence was '
                    'judged to contradict this claim">sources may disagree?</span>')
        more.append(("co-cited source disagrees", f'<div class="disagree-note">⇄ A co-cited '
                     f'source\'s best passage argues the other way — {dt}. The verdict rests on '
                     f'the supporting source; read both and decide whether your claim should '
                     f'acknowledge the disagreement.</div>'))

    so = c.get("second_opinion") or {}
    if so.get("agrees") is False:
        so_dir = ("would call this SUPPORTED — the judge may have been too strict"
                  if so.get("verdict") == "supported"
                  else "would call this UNSUPPORTED — a false-positive risk")
        x_chips += (f'<span class="sochip" title="{_esc(so.get("model") or "second model")} '
                    f'read the same evidence and disagrees">⚠ 2nd opinion disagrees</span>')
        more.append(("second opinion", f'<div class="so-note">⚠ Second opinion '
                     f'({_esc(so.get("model") or "")}): {so_dir}. '
                     f'&ldquo;{_esc(so.get("reason") or "")}&rdquo; '
                     f'The verdict above is unchanged — read the evidence and decide.</div>'))

    dc = c.get("deep_check") or {}
    if dc:
        agrees = bool(dc.get("agrees"))
        x_chips += (f'<span class="dcchip {"agree" if agrees else "flag"}" '
                    f'title="deep check: {_esc(dc.get("model") or "a stronger model")} re-read '
                    f'the claim with source context">'
                    f'{"✓ deep check agrees" if agrees else "⚠ deep check disagrees"}</span>')
        q = (f' Anchoring quote: &ldquo;{_esc(dc.get("quote"))}&rdquo;' if dc.get("quote") else "")
        better = (f'<div class="dc-better">Suggested better evidence: '
                  f'&ldquo;{_esc(dc.get("better_sentence"))}&rdquo;</div>'
                  if dc.get("better_sentence") else "")
        more.append(("deep check", f'<div class="dc-note{"" if agrees else " flag"}">🔎 Deep check '
                     f'({_esc(dc.get("model") or "")}, {_esc(dc.get("confidence") or "?")} '
                     f'confidence; testing aid, never a veto): '
                     f'{_esc(dc.get("commentary") or "")}{q}{better}</div>'))

    # ---------- arbiter reading (the states not already surfaced above) ----------
    if ab.get("action"):
        ab_model = _esc(ab.get("model") or "arbiter")
        quotes = "".join(f'<div class="ab-quote">&ldquo;{_esc(q)}&rdquo;</div>'
                         for q in (ab.get("proofs") or []))
        if dclass == "amber" and ab["action"] == "add_citation_or_rewrite":
            miss = _esc(ab.get("missing_subclaim") or "a component")
            x_chips += ('<span class="abchip authorfix" title="the arbiter read the '
                        'source and says a component needs a new citation or a rewrite">'
                        '✍ arbiter: author fix?</span>')
            more.append(("the arbiter's full reading",
                         f'<div class="ab-note">✍ Arbiter ({ab_model}): not provable from '
                         f'the cited source(s) — &ldquo;{miss}&rdquo;. '
                         f'{_esc(ab.get("why") or "")}'
                         + (quotes and f'<div>Provable parts, verified verbatim:</div>{quotes}' or "")
                         + '</div>'))
        elif verdict == "unsupported" and ab["action"] == "wrong_or_insufficient_evidence" \
                and ab.get("proofs"):
            vis_chips += ('<span class="abchip fetch" title="the arbiter found verbatim '
                          'sentences in the cited source that may prove this claim">'
                          '🔷 proof may exist</span>')
            more.append(("arbiter: proof may exist",
                         f'<div class="ab-note">🔷 Arbiter ({ab_model}): the cited source '
                         f'may contain the proof the judge never saw — these sentences are '
                         f'verified verbatim from the source:{quotes}'
                         f'{_esc(ab.get("why") or "")} The verdict above is unchanged — '
                         f'read them and decide.</div>'))
        elif verdict == "unsupported" and ab["action"] == "add_citation_or_rewrite":
            miss = _esc(ab.get("missing_subclaim") or "a component")
            rewrite = (f'<div class="ab-rewrite">Suggested rewrite: '
                       f'&ldquo;{_esc(ab.get("rewrite_suggestion"))}&rdquo;</div>'
                       if ab.get("rewrite_suggestion") else "")
            x_chips += ('<span class="abchip authorfix" title="the arbiter read the '
                        'source and says a component needs a new citation or a rewrite">'
                        '✍ arbiter: author fix?</span>')
            more.append(("the arbiter's reading",
                         f'<div class="ab-note">✍ Arbiter ({ab_model}): agrees this is not '
                         f'provable from the cited source(s) — &ldquo;{miss}&rdquo;. '
                         f'{_esc(ab.get("why") or "")}{rewrite}'
                         + (quotes and f'<div>Provable parts, verified verbatim:</div>{quotes}' or "")
                         + '</div>'))
        elif ab["action"] == "wrong_or_insufficient_evidence" and c.get("proof_state") != "arbiter_resolved":
            x_chips += ('<span class="abchip fetch" title="the arbiter says better '
                        'evidence exists in the source than what is shown">'
                        '🔷 better proof exists</span>')
            more.append(("arbiter: better proof exists",
                         f'<div class="ab-note">🔷 Arbiter ({ab_model}): the shown '
                         f'sentences don\'t fully prove the claim, but these verified '
                         f'source sentences would:{quotes}{_esc(ab.get("why") or "")}</div>'))
        elif verdict == "unsupported" and ab["action"] == "supported":
            vis_chips += ('<span class="abchip fetch" title="the arbiter read the source '
                          'and thinks the shown evidence already proves the claim">'
                          '🔷 arbiter disagrees: looks proven</span>')
            more.append(("arbiter disagrees",
                         f'<div class="ab-note">🔷 Arbiter ({ab_model}): the shown evidence '
                         f'already appears to prove this claim — the judge may have been '
                         f'too strict. {_esc(ab.get("why") or "")}{quotes} '
                         f'The verdict above is unchanged — read and decide.</div>'))
        elif verdict == "supported" and ab["action"] == "supported" \
                and c.get("proof_state") == "partial":
            more.append(("arbiter", f'<div class="ab-note ok">Arbiter ({ab_model}): read the '
                         f'flagged gaps against the source — they look minor; the shown '
                         f'evidence holds. {_esc(ab.get("why") or "")}</div>'))
        if ab.get("conflict"):
            cf = ab["conflict"]
            vis_chips += ('<span class="abchip conflict" title="a source sentence may '
                          'CONTRADICT this claim — read it">⚡ conflicting evidence?</span>')
            more.append(("conflicting evidence",
                         f'<div class="ab-note conflict">⚡ Possible conflicting evidence '
                         f'(verified verbatim): &ldquo;{_esc(cf.get("sentence") or "")}&rdquo; '
                         f'— {_esc(cf.get("why") or "")}</div>'))

    # ---------- date/byline caveats, owner flag, diff ----------
    if c.get("date_inferred"):
        x_chips += ('<span class="datechip" title="a relative time reference in the '
                    'evidence was resolved against the article’s publication date">'
                    'date inferred from article date</span>')
    if c.get("byline_inferred"):
        x_chips += ('<span class="datechip" title="an attribution in this claim was '
                    'resolved against the article’s author byline">'
                    'attribution from article byline</span>')
    of = c.get("owner_flag") or {}
    if of:
        of_note = f' — “{_esc(of["note"])}”' if of.get("note") else ""
        x_chips += (f'<span class="ownerchip" title="you marked this verdict wrong '
                    f'({_esc(of.get("timestamp") or "")}){of_note}">author disputed</span>')
    prev = c.get("prev") or {}
    changed_cls = ""
    if prev.get("changed"):
        changed_cls = " changed"
        x_chips += '<span class="changed-chip" title="edited since the last run">✎ changed</span>'
        if prev.get("text"):
            was = f'was {_esc(prev.get("verdict") or "?")}' if prev.get("verdict") else "previous version"
            more.append(("edited since last run",
                         f'<div class="changed-note">✎ Edited since the last run ({was}):'
                         f'<div class="prev-text">{_esc(prev["text"])}</div></div>'))
        else:
            more.append(("new", '<div class="changed-note">✎ New since the last run.</div>'))

    # Missing cited source files: rare and important — always visible.
    for mm in (c.get("missing_markers") or []):
        key_html += (f'<div class="unsupp-note">⚠ The cited file '
                     f'<code>{_esc(mm.get("filename") or "")}</code> ([[{_esc(mm.get("key") or "")}]]) '
                     f'is not in the sources folder — this citation was not verified. '
                     f'Add the file and re-run to check it.</div>')

    # ---------- assemble ----------
    more_html = ""
    if more:
        labels = ", ".join(l for l, _ in more)
        if len(labels) > 110:
            labels = labels[:107] + "…"
        more_html = (f'<details class="x more"><summary title="secondary checks and notes — '
                     f'nothing in here changes the verdict">▸ more checks — {labels}</summary>'
                     f'<div class="xbody">' + "".join(h for _, h in more) + '</div></details>')

    cite_cls = " citeneeded" if (verdict == "own"
                                 and (c.get("own_kind") or {}).get("kind") == "fact") else ""
    partly_cls = " partlyproven" if dclass == "amber" else ""
    scoped_cls = " scoped" if dclass == "scopedcite" else ""
    card_cls = (f'{dclass}{" supportedish" if dclass == "amber" else ""}'
                f'{changed_cls}{cite_cls}{partial_cls}{partly_cls}{scoped_cls}'
                f'{overcite_cls}{conf_cls}')

    # Proof sentences one click away in simple view (owner 7/15 #5); expert
    # view opens the expander like every other details.x.
    proofs_html = ""
    if proofs:
        n_rows = proofs.count('<div class="proof') + proofs.count('<div class="evidence"')
        s = "s" if n_rows != 1 else ""
        plabel = (f"✓ proof for the proven parts ({n_rows} sentence{s})"
                  if dclass == "amber" else f"✓ show proof sentences ({n_rows})")
        proofs_html = (f'<details class="x proofsx"><summary title="the exact source '
                       f'sentence{s} the verdict rests on — each with a link that opens '
                       f'the source at the right spot">{plabel}</summary>'
                       f'<div class="proofs">{proofs}</div></details>')

    # The text↔card sync click lives on the header and claim only (owner 7/15
    # #9): clicking quotes/expanders must not recenter the panels.
    sync = f'onclick="brush(\'{c["id"]}\', \'card\')" title="click to highlight this claim in your text"'
    return f"""
      <div class="card {card_cls}" id="card-{c['id']}" data-text="text-{c['id']}">
        <div class="card-head" {sync}><span class="head-left"><span class="badge {badge_cls}" title="{_esc(badge_title)}">{badge}</span><span class="claimno" title="claim id">{_esc(c['id'])}</span>{vis_chips}<span class="xchips">{x_chips}</span></span></div>
        <div class="card-claim" {sync}>{claim_html}</div>
        {key_html}
        {proofs_html}
        {_triage_row(c['id'])}
        {extra_details}
        {more_html}
      </div>"""


def generate(analysis: Dict[str, Any], output_path: str, title: str = "Claim Verification",
             source_texts: Optional[Dict[str, str]] = None,
             assessment: Optional[Dict[str, Any]] = None) -> str:
    """Same contract as viewer.generate(); writes the v2 page."""
    claims = analysis["text_claims"]
    omitted = analysis.get("omitted", [])
    totals = analysis.get("coverage", {}).get("totals", {})
    fname_map = _filename_map(analysis)
    paper_meta = _paper_meta(analysis)
    source_texts = source_texts or {}

    text_html = "".join(_claim_span_v2(c) for c in claims)
    meta = analysis.get("metadata", {})
    out_dir = meta.get("output_dir") or os.path.dirname(os.path.abspath(output_path))
    claim_cards = "".join(_card_v2(c, fname_map, source_texts, paper_meta)
                          for c in claims)

    # ---- counts: the three display states are separate filters in v2 ----
    n_partly = sum(1 for c in claims if _display_class(c) == "amber")
    n_green = sum(1 for c in claims if _display_class(c) == "supported")
    n_sup = n_green + n_partly                     # verdict-field supported
    n_scoped_cite = sum(1 for c in claims if _is_scoped(c))
    n_uns = sum(1 for c in claims if c["verdict"] == "unsupported") - n_scoped_cite
    n_own = sum(1 for c in claims if c["verdict"] == "own")
    n_unverifiable = sum(1 for c in claims if c["verdict"] == "unsupported"
                         and str(c.get("reason", "")).startswith("source_file_missing"))
    n_changed = sum(1 for c in claims if (c.get("prev") or {}).get("changed"))
    n_cite = sum(1 for c in claims if c["verdict"] == "own"
                 and (c.get("own_kind") or {}).get("kind") == "fact")

    partly_btn = (f'<button class="fbtn partlyf" data-f="partlyproven" title="judged supported '
                  f'overall, but one named part has no proof — two models could not close the '
                  f'gap; rewrite or re-cite that part">'
                  f'<span class="dot" style="background:#f59e0b"></span>'
                  f'Not proven as written ({n_partly})</button>' if n_partly else "")
    scoped_btn = (f'<button class="fbtn scopedf" data-f="scoped" title="unsupported passages '
                  f'that mainly describe the authors\' own work — the citation backs only a '
                  f'method/concept/related pointer, so the red question never applied">'
                  f'Scoped citation ({n_scoped_cite})</button>' if n_scoped_cite else "")
    cite_btn = (f'<button class="fbtn cite" data-f="citeneeded" title="uncited passages that '
                f'assert a checkable fact — consider citing a source">Citation needed ({n_cite})</button>'
                if n_cite else "")
    changed_btn = (f'<button class="fbtn" data-f="changed">Changed ({n_changed})</button>'
                   if n_changed else "")
    conf_counts = {"low": 0, "medium": 0}
    for c in claims:
        cf = _confidence(c)
        if cf and cf[0] in conf_counts:
            conf_counts[cf[0]] += 1
    conf_btns = "".join(
        f'<button class="fbtn conff" data-f="conf-{lvl}" title="claims whose judge '
        f'confidence is {lvl} — the ones worth a human read">'
        f'{lvl.capitalize()} confidence ({n})</button>'
        for lvl, n in conf_counts.items() if n)

    filter_bar = f"""
      <div class="filterbar">
        <button class="fbtn active" data-f="all" title="show every claim card, in text order">All ({len(claims)})</button>
        <button class="fbtn" data-f="supported" title="fully proven claims — every part has a quoted proof sentence"><span class="dot" style="background:#10b981"></span>Supported ({n_green})</button>
        {partly_btn}
        <button class="fbtn" data-f="unsupported" title="claims no cited source was found to back"><span class="dot" style="background:#ef4444"></span>Unsupported ({n_uns})</button>
        {scoped_btn}
        <button class="fbtn" data-f="own" title="your uncited claims — nothing was checked">Your own ({n_own})</button>
        {conf_btns}
        {cite_btn}
        {changed_btn}
        <button class="fbtn hchk" data-f="hchecked" title="cards you marked ✓ checked">✓ Checked (<span id="chkN">0</span>)</button>
        <button class="fbtn hunchk" data-f="hunchecked" title="cards you have not marked ✓ checked yet">Unchecked (<span id="unchkN">0</span>)</button>
      </div>"""

    review_bar = """
      <div class="reviewbar">
        <span class="rev-title">Review</span>
        <span id="revCount" class="rev-count" title="how many cards you have marked for repair, and how many you have ✓-checked">0 claims marked</span>
        <button id="briefBtn" class="rbtn" onclick="copyBrief()" title="builds a self-contained markdown summary of every card you marked — claim, verdict, evidence quotes, your notes, a task per mark — to paste into any LLM chat that will fix the text">Copy repair brief</button>
        <button class="rbtn" onclick="downloadReview()" title="the same marks as a review.json file — the machine-readable twin of the brief, for automated repair tools. Saves to your chosen folder if set, else the browser's Downloads">Download review file</button>
        <input type="file" id="revfile" accept="application/json,.json" style="display:none" onchange="if(this.files[0]){importReview(this.files[0]); this.value='';}">
        <button class="rbtn" onclick="document.getElementById('revfile').click()" title="load a review file you saved earlier (or a walkthrough review) — its comments appear on the matching cards">Load review file</button>
        <button id="saveLocBtn" class="rbtn" onclick="chooseSaveDir()"
                title="pick a folder once (e.g. the run folder) — every future review file saves there without asking. Chromium only; elsewhere the file goes to Downloads.">Save location…</button>
        <button id="sciBtn" class="rbtn sci" onclick="copyScience()"
                title="only the cards marked 'wrong source', bundled into ONE research prompt for a deep-research tool (Claude Science, Elicit, ...) to find better sources">Copy research request</button>
        <span class="rev-hint">mark any card with the buttons at its bottom (✓ checked / wrong source / rewrite …), then export — paste the brief into the LLM that wrote your text</span>
      </div>"""

    # "This run" facts for the legend (owner 7/15 #3: models must be on record).
    arb_models = sorted({(c.get("arbiter") or {}).get("model") for c in claims
                         if (c.get("arbiter") or {}).get("model")})
    so_models = sorted({(c.get("second_opinion") or {}).get("model") for c in claims
                        if (c.get("second_opinion") or {}).get("model")})
    run_info = f'primary judge: <b>{_esc(meta.get("model") or "unknown")}</b>'
    if arb_models:
        run_info += f' · arbiter: <b>{_esc(", ".join(arb_models))}</b>'
    if so_models:
        run_info += f' · second opinion: <b>{_esc(", ".join(so_models))}</b>'
    if meta.get("timestamp"):
        run_info += f' · run date: {_esc(str(meta["timestamp"]))}'

    legend_html = f"""
      <details class="legend">
        <summary title="a two-minute guide — what this page is, how the checking works, and what every color means">How to read this — start here if someone just sent you this file</summary>
        <div class="legend-body">
          <div class="legend-grp">
            <div class="legend-h">What is this?</div>
            <div class="legrow">Someone wrote the text on the left and cited sources for its claims. This page is an automatic audit of those citations: every cited sentence was checked against the actual source files, and each got a card on the right with a verdict and the exact quotes that verdict rests on.</div>
            <div class="legrow"><b>How it works:</b> ① citation markers in the text tie each claim to a source file → ② the tool reads those files and pulls the most relevant passages → ③ an LLM judge decides whether the passages really state the claim (majority of three votes; failures get a deeper full-text read) → ④ flagged cards are re-read by a second model, the &ldquo;arbiter&rdquo; → ⑤ you review the cards and export the fixes.</div>
            <div class="legrow"><b>What to do:</b> click a sentence on the left (or press <kbd>&rarr;</kbd>) — its card shows on the right, one at a time; <kbd>&larr;</kbd>/<kbd>&rarr;</kbd> step through claims (<b>show all cards</b> restores the full list). Check the quoted proofs (links open the source at the right spot), press <b>✓ checked</b> as you go — the thin progress line tracks what&rsquo;s left and <b>&#8618; last checked</b> resumes where you stopped. Mark problems with the buttons at a card&rsquo;s bottom, then open <b>&#9656; export review</b> (next to the progress line) and <b>Copy repair brief</b> into the LLM that wrote the text.</div>
          </div>
          <div class="legend-grp">
            <div class="legend-h">Card colors</div>
            <div class="legrow"><span class="badge supported">SUPPORTED</span> every part of the claim has a quoted proof sentence from the cited source — not that the source is strong or the claim is true</div>
            <div class="legrow"><span class="badge amber">NOT PROVEN AS WRITTEN</span> the core is proven, but the amber line names a part with NO proof — two models read the full source; rewrite or re-cite that part. The underlying verdict stays supported</div>
            <div class="legrow"><span class="badge unsupported">UNSUPPORTED</span> no cited source backs the claim as a whole (or the source file is missing) — &ldquo;partly proven&rdquo; parts, if any, are one click away</div>
            <div class="legrow"><span class="badge scoped">SCOPED CITATION</span> the passage is the authors&rsquo; own work; the citation backs only a method/concept/related pointer inside it — not an authoring error</div>
            <div class="legrow"><span class="badge own">YOUR OWN CLAIM</span> your uncited claim — thesis, argument, transition; nothing was checked</div>
            <div class="legrow"><span class="badge omitted">UNUSED</span> a point one of your sources makes that your text didn't cite — a menu, not an error (expert view)</div>
          </div>
          <div class="legend-grp">
            <div class="legend-h">Expert corner — how the checking actually works</div>
            <div class="legrow"><b>The judge:</b> an LLM given your claim plus the best-matching passages from the cited source (found by semantic similarity). It votes three times; the majority wins. A claim that fails gets a second chance: a full-text read of the whole source, and a piece-by-piece check of its parts. &ldquo;Supported&rdquo; needs the source to state the claim, not merely be about the topic.</div>
            <div class="legrow"><b>The arbiter:</b> a second, different model that re-reads only the flagged cards — those marked NOT PROVEN AS WRITTEN, and the unsupported ones — with the whole source in view. Every quote it produces is machine-verified to exist verbatim in the source. It can clear a NOT-PROVEN flag by finding real proof (⛑), and it can suggest rewrites — but it never changes a verdict; only the primary judge can do that.</div>
            <div class="legrow"><b>Confidence chips</b> <span class="confchip high">high</span><span class="confchip medium">medium</span><span class="confchip low">low</span> are derived from the vote tallies, which pipeline stage decided, and match strength — no extra model call, and deliberately a proxy, not a probability.</div>
            <div class="legrow"><b>This run:</b> {run_info}.</div>
          </div>
          <div class="legend-grp">
            <div class="legend-h">Review — mark, track, export</div>
            <div class="legrow">Mark cards as you read: <b>✓ checked</b> = you looked at it (the progress line above the cards fills up; <b>&#8618; last checked</b> takes you back after a break); the repair buttons say WHY a card needs work — <b>find proof / rewrite</b> means: hunt the source for stronger sentences first, rewrite only if none exist.</div>
            <div class="legrow">When done, open <b>&#9656; export review</b> — the small button next to the progress line. Three ways out:</div>
            <div class="legrow"><b>Copy repair brief</b> — a self-contained markdown summary of every marked card (claim, verdict, evidence quotes, your notes, a task per mark). Paste it into any LLM chat — it has everything needed to fix the text.</div>
            <div class="legrow"><b>Download review file</b> — the same marks as a <code>review.json</code> file: the machine-readable twin of the brief, for automated repair tools.</div>
            <div class="legrow"><b>Copy research request</b> — only the cards marked &ldquo;wrong source&rdquo;, bundled into one prompt for a deep-research tool to find better sources for them.</div>
            <div class="legrow">Marks live in this browser only (until exported) and are shared with the v1 viewer of this run.</div>
          </div>
        </div>
      </details>"""

    shown_omitted = omitted[:OMITTED_SHOWN]
    tail_omitted = omitted[OMITTED_SHOWN:OMITTED_EMBED_CAP]
    n_beyond_cap = max(0, len(omitted) - OMITTED_EMBED_CAP)
    shown_html = "".join(_omitted_card(o, fname_map, source_texts, paper_meta) for o in shown_omitted)
    if tail_omitted:
        tail_html = "".join(_omitted_card(o, fname_map, source_texts, paper_meta) for o in tail_omitted)
        if n_beyond_cap:
            tail_html += (f'<p class="meta">… and {n_beyond_cap} more below this relevance level '
                          f'— not embedded in the viewer (see analysis.json).</p>')
        omitted_cards = (shown_html
                         + f'<button class="toggle" id="omitToggle" onclick="toggleOmitted()" title="less-relevant unused source points — ranked by similarity to your text">Show {len(tail_omitted)} more (less relevant)</button>'
                         + f'<div id="omittedTail" class="collapsed">{tail_html}</div>')
    else:
        omitted_cards = shown_html
    omitted_sec_label = (
        f"Unused source points — most relevant first (top {len(shown_omitted)} of {len(omitted)})"
        if len(omitted) > len(shown_omitted) else
        f"Unused source points — most relevant first ({len(omitted)})"
        if omitted else
        "Unused source points — not analyzed in this run"
        if analysis.get("metadata", {}).get("decompose") is False else
        "Unused source points (things your sources say that your text didn't cite)")
    # An empty section is expert-view-only noise for a first-time reader
    # (owner 7/15 #8) — and never print a dangling "none" under it.
    if omitted:
        omitted_section = f'<h2 class="sec">{_esc(omitted_sec_label)}</h2>{omitted_cards}'
    else:
        omitted_section = f'<div class="omitempty"><h2 class="sec">{_esc(omitted_sec_label)}</h2></div>'

    coverage_html = _coverage_bars(analysis.get("coverage", {}))
    n_sources = len(analysis.get("coverage", {}).get("per_source", {}))
    cov_collapsed = " collapsed" if n_sources > 4 else ""
    cov_btn_label = "show" if n_sources > 4 else "hide"
    has_pdf = any((fn or "").lower().endswith(".pdf") for fn in fname_map.values())
    has_text = bool(source_texts)
    assess_section = _assessment_panel(assessment)
    coverage_ratio = _coverage_ratio_bar_v2(n_green, n_partly, n_uns, n_unverifiable,
                                            n_own + n_scoped_cite)

    marker_errors = analysis.get("metadata", {}).get("marker_errors", []) or []
    warn_html = ""
    if marker_errors:
        items = "".join(f"<li>{_esc(w)}</li>" for w in marker_errors)
        warn_html = (f'<div class="warnbanner"><b>⚠ {len(marker_errors)} input warning(s)</b>'
                     f' — cited sources that could not be used (their claims show as'
                     f' unsupported):<ul>{items}</ul></div>')

    st_json = json.dumps(source_texts, ensure_ascii=False).replace("</", "<\\/")
    # SAME derivation as v1 -> same RUN_ID -> shared review marks between viewers.
    run_id = hashlib.sha1(
        f"{meta.get('text_file', '')}|{meta.get('timestamp', '')}".encode("utf-8")
    ).hexdigest()[:12]
    rd_json = json.dumps(_review_data(analysis, claims, out_dir),
                         ensure_ascii=False).replace("</", "<\\/")

    # Header counts speak the same language as the filters (owner 7/15 #4):
    # "supported" here = fully-proven green cards; ambers get their own term.
    partly_total = (f' &nbsp;·&nbsp; <b style="color:#fbbf24">{n_partly} not proven as written</b>'
                    if n_partly else "")
    omitted_total = (f' &nbsp;·&nbsp; <b class="o">{totals.get("omitted", 0)} unused source points</b>'
                     if omitted else "")
    unverifiable_total = (f' &nbsp;·&nbsp; <b style="color:#9ca3af">{n_unverifiable} unverifiable '
                          f'(source file missing)</b>' if n_unverifiable else "")
    scoped_total = (f'\n    <b style="color:#818cf8">{n_scoped_cite} scoped citation</b> &nbsp;·&nbsp;'
                    if n_scoped_cite else "")
    cite_total = (f'&nbsp;·&nbsp; <b style="color:#e5e7eb">📎 {n_cite} citation '
                  f'suggestion{"s" if n_cite != 1 else ""}</b>' if n_cite else "")
    changed_total = (f'&nbsp;·&nbsp; <b style="color:#e5e7eb">{n_changed} changed since last run</b>'
                     if n_changed else "")

    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{_esc(title)} · v2</title>
<style>
  :root {{ --green:#10b981; --green-bg:#d1fae5; --amber:#f59e0b; --amber-bg:#fde68a;
          --amber-tx:#92400e; --red:#ef4444; --red-bg:#fecaca; --red-tx:#991b1b;
          --gray:#6b7280; }}
  * {{ box-sizing:border-box; }}
  html,body {{ height:100%; }}
  body {{ font-family:-apple-system,Segoe UI,Roboto,sans-serif; margin:0; color:#1f2937; background:#f8fafc;
         display:flex; flex-direction:column; }}
  header {{ background:#111827; color:#fff; padding:12px 24px; }}
  header h1 {{ margin:0 0 4px; font-size:17px; }}
  .head-row {{ display:flex; justify-content:space-between; align-items:center; gap:12px; }}
  .totals {{ font-size:13px; opacity:.85; }}
  .totals b.s {{ color:#5eead4; }} .totals b.u {{ color:#fca5a5; }} .totals b.o {{ color:#fdba74; }}
  .scopenote {{ font-size:11px; opacity:.55; margin-top:3px; }}
  .v2tag {{ font-size:10px; font-weight:700; background:#6b7280; color:#fff; border-radius:4px;
            padding:1px 6px; margin-left:8px; vertical-align:middle; }}
  .modebar {{ font-size:12px; padding:6px 24px; background:#0b1220; color:#cbd5e1; border-top:1px solid #1f2937; }}
  .modebar .dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; vertical-align:middle; }}
  .coverage {{ padding:10px 24px; background:#fff; border-bottom:1px solid #e5e7eb; }}
  .cov-head {{ display:flex; justify-content:space-between; align-items:center; }}
  .cov-title {{ font-size:12px; color:var(--gray); margin-bottom:6px; }}
  .cov-title b {{ text-transform:uppercase; letter-spacing:.04em; }}
  .cov-bars {{ max-height:30vh; overflow:auto; }}
  .cov-bars.collapsed {{ display:none; }}
  .cov-row {{ display:flex; align-items:center; gap:12px; margin:5px 0; font-size:13px; }}
  .cov-label {{ width:220px; font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  .cov-track {{ flex:1; height:10px; background:var(--red-bg); border-radius:5px; overflow:hidden; }}
  .cov-fill {{ height:100%; background:var(--green); }}
  .cov-num {{ width:340px; color:var(--gray); }}
  .cov-status {{ display:inline-block; margin-right:8px; padding:0 6px; border-radius:8px;
                 background:#eef1f4; font-size:11px; }}

  .layout {{ display:flex; flex:1; min-height:0; }}
  .doc-wrap {{ flex:1; display:flex; flex-direction:column; min-width:0; background:#fff; border-right:1px solid #e5e7eb; }}
  .cards {{ flex:1; display:flex; flex-direction:column; min-width:0; }}
  .doc-head {{ display:flex; justify-content:space-between; align-items:center; padding:6px 16px; background:#f3f4f6; }}
  .doc-head h2 {{ font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--gray); margin:0; }}
  .toggle {{ font-size:11px; padding:2px 8px; border:1px solid #d1d5db; background:#fff; border-radius:4px; cursor:pointer; }}
  .head-controls {{ display:inline-flex; align-items:center; gap:8px; }}
  /* Mode SHOWS state (owner 7/15 #7): the highlighted side is the current view. */
  .modeseg {{ display:inline-flex; border:1px solid #6b7280; border-radius:6px; overflow:hidden; }}
  .modeseg button {{ font-size:11px; padding:3px 10px; border:0; background:transparent;
                     color:#9ca3af; cursor:pointer; font-family:inherit; }}
  .modeseg button.active {{ background:#fff; color:#111827; font-weight:700; cursor:default; }}
  /* Bibliography health check + empty unused section: expert-view-only (owner 7/15 #2, #8). */
  body:not(.expert) .coverage {{ display:none; }}
  body:not(.expert) .omitempty {{ display:none; }}
  .doc {{ flex:1; padding:14px 18px; line-height:1.9; overflow:auto; }}
  .doc.collapsed {{ display:none; }}
  .doc-wrap.collapsed {{ flex:0 0 auto; min-width:0; }}  /* hand the width to the cards */
  .cards-body {{ flex:1; overflow:auto; padding:12px 18px; }}
  h2.sec {{ font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--gray); margin:14px 0 6px; }}

  /* left-panel highlights — amber is its own state (decision 1) */
  .claim {{ cursor:pointer; padding:1px 2px; border-radius:3px; border-bottom:2px solid transparent; }}
  .claim.supported {{ background:var(--green-bg); border-bottom-color:var(--green); }}
  .claim.amber {{ background:var(--amber-bg); border-bottom-color:var(--amber); }}
  .claim.unsupported {{ background:var(--red-bg); border-bottom-color:var(--red); }}
  .claim.own {{ background:#eef2ff; border-bottom-color:#818cf8; }}
  .claim.scopedcite {{ background:#eef2ff; border-bottom-color:#6366f1; }}
  /* Selected state = Material-style state layer: a translucent darkening of the
     element's OWN background (friend feedback #3) — never a separate accent hue. */
  .claim.active {{ box-shadow:inset 0 0 0 999px rgba(15,23,42,.16); }}
  sup.mark {{ color:#475569; font-weight:700; }}
  sup.changedmark {{ color:#6b7280; font-weight:700; cursor:default; }}

  /* ---------- cards ---------- */
  .card {{ border:1px solid #e5e7eb; border-left-width:5px; border-radius:10px;
          padding:13px 16px 10px; margin:10px 0; background:#fff;
          box-shadow:0 1px 2px rgba(0,0,0,.05); transition:opacity .2s ease; }}
  .card.leaving {{ opacity:0; }}
  .card-head, .card-claim {{ cursor:pointer; }}  /* the text-sync click zone */
  .card.supported {{ border-left-color:var(--green); }}
  .card.amber {{ border-left-color:var(--amber); background:#fffdf5; }}
  .card.unsupported {{ border-left-color:var(--red); }}
  .card.own {{ border-left:3px solid #818cf8; }}
  .card.scopedcite {{ border-left:3px solid #6366f1; }}
  .card.omitted {{ border-left-color:#d97706; }}
  .card.active {{ box-shadow:inset 0 0 0 999px rgba(15,23,42,.05), 0 1px 2px rgba(0,0,0,.05); border-color:#94a3b8; }}
  .card-head {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:7px; }}
  .head-left {{ display:flex; align-items:center; gap:7px; flex-wrap:wrap; }}
  .claimno {{ font-size:11px; color:#9ca3af; font-family:monospace; }}
  .badge {{ font-size:10.5px; font-weight:700; letter-spacing:.05em; padding:3px 9px; border-radius:5px; white-space:nowrap; }}
  .badge.supported {{ background:var(--green-bg); color:#065f46; }}
  .badge.amber {{ background:var(--amber-bg); color:var(--amber-tx); }}
  .badge.unsupported {{ background:var(--red-bg); color:var(--red-tx); }}
  .badge.own {{ background:#e0e7ff; color:#4338ca; }}
  .badge.scoped {{ background:#e0e7ff; color:#4338ca; }}
  .badge.omitted {{ background:#fef3c7; color:#b45309; }}
  .card-claim {{ font-size:14.5px; line-height:1.55; margin-bottom:8px; }}
  .card-claim .leadin {{ background:#eef2ff; color:#4b5563; border-bottom:2px solid #818cf8; }}
  .meta {{ font-size:11px; color:#9ca3af; }}

  /* expert-only internals chips */
  .xchips {{ display:none; }}
  body.expert .xchips {{ display:inline-flex; align-items:center; gap:5px; flex-wrap:wrap; }}
  .xchip {{ font-size:10px; padding:1px 7px; border-radius:999px; background:#f3f4f6;
           color:#6b7280; border:1px solid #e5e7eb; white-space:nowrap; }}

  /* always-visible verdict explanations */
  .gapline {{ font-size:13px; background:#fffbeb; border:1px solid var(--amber); color:var(--amber-tx);
             border-radius:7px; padding:8px 12px; margin:0 0 9px; line-height:1.5; }}
  .unsupp-note {{ font-size:13px; background:#fef2f2; border:1px solid #fca5a5; color:var(--red-tx);
             border-radius:7px; padding:8px 12px; margin:0 0 9px; line-height:1.5; }}
  .unsupp-note code {{ background:#fff; padding:0 4px; border-radius:3px; }}
  .rewrite {{ font-size:13px; background:#f0fdfa; border:1px solid #99f6e4; color:#115e59;
             border-radius:7px; padding:8px 12px; margin:0 0 9px; line-height:1.5; }}
  .scope-note {{ font-size:13px; background:#eef2ff; border:1px solid #a5b4fc; color:#4338ca;
                 border-radius:7px; padding:8px 12px; margin:0 0 9px; line-height:1.5; }}
  .cite-note {{ font-size:13px; background:#f8fafc; border:1px solid #cbd5e1; color:#475569;
                border-radius:7px; padding:8px 12px; margin:0 0 9px; line-height:1.5; }}
  .covset-common {{ font-size:12px; background:#f9fafb; border:1px solid #e5e7eb; color:#6b7280;
                    border-radius:6px; padding:6px 9px; margin:0 0 9px; }}

  /* main proof rows */
  .proofs .proof {{ border-top:1px dashed #e5e7eb; padding:8px 0 7px; }}
  .proofs .proof:first-child {{ border-top:none; }}
  .part {{ font-size:12.5px; font-weight:600; color:#065f46; margin-bottom:4px; }}
  .card.amber .part {{ color:var(--amber-tx); }}
  .proof.rescued .part {{ color:#0f766e; }}
  .rescuetag {{ font-size:10px; font-weight:700; color:#0f766e; background:#ccfbf1;
                border:1px solid #5eead4; border-radius:8px; padding:1px 6px; margin-left:6px; }}
  blockquote {{ margin:2px 0 5px; padding:6px 10px; background:#f9fafb;
               border-left:3px solid #d1d5db; border-radius:0 5px 5px 0;
               font-size:13px; line-height:1.5; color:#374151; font-style:italic; }}
  .srcline {{ font-size:11.5px; color:#6b7280; display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
  .srcname {{ max-width:60%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  .paperlink {{ font-size:11px; color:#2563eb; text-decoration:none; white-space:nowrap; }}
  .paperlink:hover {{ text-decoration:underline; }}
  .copy {{ font-size:10.5px; padding:1px 8px; cursor:pointer; color:#6b7280;
          border:1px solid #e5e7eb; border-radius:5px; background:#fff; margin-left:4px; }}
  .copy:hover {{ background:#f3f4f6; }}
  .src-actions {{ display:flex; gap:8px; margin-top:6px; flex-wrap:wrap; }}
  .deeplink, .opentext-btn {{ font-size:11.5px; padding:3px 9px; border:1px solid #94a3b8;
          background:#fff; color:#334155; border-radius:5px; text-decoration:none; cursor:pointer; font-family:inherit; }}
  .deeplink:hover, .opentext-btn:hover {{ background:#f1f5f9; }}
  .side-btn {{ font-size:11.5px; padding:3px 9px; border:1px solid #d1d5db; background:#fff;
          color:#6b7280; border-radius:5px; cursor:pointer; font-family:inherit; }}
  .side-btn:hover {{ background:#f3f4f6; }}

  /* v1 evidence rows (fallback + inside expanders) */
  .evidence {{ margin-top:8px; padding-top:8px; border-top:1px dashed #e5e7eb; }}
  .ev-label {{ font-size:11px; color:var(--gray); margin-bottom:4px; }}
  .srcchip {{ font-size:9px; font-weight:700; letter-spacing:.02em; padding:1px 6px; border-radius:8px; margin-right:5px; }}
  .srcchip.ok {{ background:var(--green-bg); color:#065f46; }}
  .srcchip.no {{ background:#e5e7eb; color:#374151; }}
  .evidence .reason {{ font-size:12px; color:var(--red-tx); }}
  details.judged {{ margin-top:6px; font-size:12px; }}
  details.judged summary {{ cursor:pointer; color:#6b7280; }}
  .judged-text {{ margin-top:4px; padding:6px 8px; background:#f9fafb; border-left:3px solid #d1d5db; color:#374151; line-height:1.5; }}

  /* named expanders */
  details.proofsx > summary {{ color:#047857; font-weight:600; }}
  .card.amber details.proofsx > summary {{ color:var(--amber-tx); }}
  details.x {{ margin:5px 0 2px; }}
  details.x summary {{ font-size:12.5px; color:#4b5563; cursor:pointer; padding:4px 0; user-select:none; }}
  details.x summary:hover {{ color:#111827; }}
  details.x[open] summary {{ color:#111827; font-weight:600; }}
  .xbody {{ font-size:12.5px; line-height:1.55; color:#4b5563; padding:4px 0 4px 14px;
           border-left:2px solid #f3f4f6; margin-left:2px; }}
  details.covspan {{ font-size:12px; margin:6px 0; border:1px solid #e5e7eb; border-radius:4px;
                     padding:3px 8px; background:#fff; }}
  details.covspan summary {{ cursor:pointer; color:#475569; }}

  /* notes reused from v1 (rendered inside key_html or expanders) */
  .own-note {{ font-size:12px; color:#4338ca; background:#eef2ff; padding:6px 8px; border-radius:4px; }}
  .leadin-note {{ font-size:12px; color:#4338ca; background:#eef2ff; padding:4px 8px; border-radius:4px; margin-top:6px; }}
  .combined-note {{ font-size:12px; color:#047857; background:var(--green-bg); padding:4px 8px; border-radius:4px; margin-top:6px; }}
  .multisource-note {{ font-size:12px; background:#f8fafc; border:1px solid #cbd5e1; color:#475569;
                       border-radius:6px; padding:6px 10px; margin:6px 0; }}
  .so-note {{ font-size:12px; background:#f8fafc; border:1px solid #cbd5e1; color:#475569;
              border-radius:6px; padding:6px 10px; margin:8px 0 0; }}
  .dc-note {{ font-size:12px; background:#f8fafc; border:1px solid #cbd5e1; color:#475569;
              border-radius:6px; padding:6px 10px; margin:8px 0 0; }}
  .dc-note.flag {{ color:#374151; }}
  .dc-better {{ margin-top:4px; font-style:italic; }}
  .ab-note {{ font-size:12px; background:#f8fafc; border:1px solid #cbd5e1; color:#475569;
              border-radius:6px; padding:6px 10px; margin:8px 0 0; }}
  .ab-note.ok {{ background:#f8fafc; border-color:#e2e8f0; color:#475569; }}
  .ab-note.conflict {{ color:#374151; }}
  .ab-quote {{ margin:4px 0 4px 10px; font-style:italic; }}
  .ab-rewrite {{ margin-top:4px; font-style:italic; }}
  .rescue-note {{ font-size:12px; background:#f0fdfa; border:1px solid #5eead4; color:#0f766e;
                  border-radius:6px; padding:6px 10px; margin:8px 0 0; }}
  .partial-note {{ font-size:12px; background:#fffbeb; border:1px solid #fbbf24; color:#b45309;
                   border-radius:6px; padding:6px 10px; margin:8px 0 0; }}
  .hunt-note {{ font-size:12px; background:#f8fafc; border:1px solid #cbd5e1; color:#475569;
                border-radius:6px; padding:6px 10px; margin:8px 0 0; }}
  .compcheck-missing {{ font-size:12px; background:#fffbeb; border:1px solid #fbbf24; color:#b45309;
                        border-radius:6px; padding:6px 10px; margin:6px 0 0; }}
  .compcheck-tail {{ color:#475569; margin-top:4px; font-size:12px; }}
  .overcite-note {{ font-size:12px; background:#f8fafc; border:1px solid #cbd5e1; color:#475569;
                    border-radius:6px; padding:6px 10px; margin:8px 0 0; }}
  .sh-note {{ font-size:12px; background:#f8fafc; border:1px solid #cbd5e1; color:#475569;
              border-radius:6px; padding:6px 10px; margin:8px 0 0; }}
  .disagree-note {{ font-size:12px; background:#fef2f2; border:1px solid #fca5a5; color:#991b1b;
                    border-radius:6px; padding:6px 10px; margin:8px 0 0; }}
  .borderline-note {{ font-size:12px; color:#854d0e; background:#fefce8; border:1px solid #fde047; padding:4px 8px; border-radius:4px; margin-top:6px; }}
  .changed-note {{ font-size:12px; color:#475569; background:#f8fafc; border:1px solid #cbd5e1;
                   padding:4px 8px; border-radius:4px; margin-top:6px; }}
  .prev-text {{ margin-top:4px; padding:6px 8px; background:#fff; border-left:3px solid #cbd5e1;
                color:#4b5563; font-style:italic; }}
  .fixbox {{ margin-top:8px; padding:8px 10px; background:#f0fdf4; border:1px solid #bbf7d0; border-radius:6px; }}
  .fixbox blockquote {{ margin:6px 0; }}
  .fix-head {{ font-size:12px; font-weight:600; color:#166534; }}
  .fixchip {{ font-size:11px; font-weight:400; padding:1px 6px; border-radius:8px; margin-left:6px; }}
  .fixchip.ok {{ background:#dcfce7; color:#166534; }}
  .fixchip.warn {{ background:#fef9c3; color:#854d0e; }}
  .fix-changes {{ font-size:12px; color:#4b5563; font-style:italic; margin-top:4px; }}

  /* visible chips — color budget (friend feedback #2): hue is reserved for
     verdict states (green / red / amber / indigo, + the owner-ruled teal ⛑
     rescue chip); every other chip is a neutral grey ghost (outline) chip. */
  .confchip {{ font-size:9px; font-weight:700; letter-spacing:.02em; padding:1px 6px;
               border-radius:8px; border:1px solid #d1d5db; background:#fff; color:#6b7280;
               white-space:nowrap; cursor:help; }}
  .confchip.low {{ color:#374151; border-color:#9ca3af; }}
  .rescuechip {{ font-size:9px; font-weight:700; letter-spacing:.02em; padding:1px 6px;
                 border-radius:8px; background:#ccfbf1; color:#0f766e; border:1px solid #5eead4; cursor:help; }}
  .citechip.loud {{ font-size:11px; font-weight:700; padding:2px 8px; border-radius:8px;
                    background:#334155; color:#fff; border:1px solid #334155; cursor:help; }}
  .citechip {{ font-size:9px; font-weight:700; padding:1px 6px; border-radius:8px;
               background:#fff; color:#6b7280; border:1px solid #d1d5db; cursor:help; }}
  .abchip {{ font-size:9px; font-weight:700; letter-spacing:.02em; padding:1px 6px;
             border-radius:8px; cursor:help; background:#fff; color:#6b7280; border:1px solid #d1d5db; }}
  .abchip.conflict {{ color:#374151; border-color:#9ca3af; }}
  .sochip {{ font-size:9px; font-weight:700; padding:1px 6px; border-radius:8px;
             background:#fff; color:#6b7280; border:1px solid #d1d5db; cursor:help; }}
  .jechip {{ font-size:9px; font-weight:700; padding:1px 6px; border-radius:8px; cursor:help;
             background:#fff; color:#374151; border:1px solid #9ca3af; }}
  .dcchip {{ font-size:9px; font-weight:700; padding:1px 6px; border-radius:8px; cursor:help;
             background:#fff; color:#6b7280; border:1px solid #d1d5db; }}
  .dcchip.flag {{ color:#374151; border-color:#9ca3af; }}
  .partialchip {{ font-size:9px; font-weight:700; padding:1px 6px; border-radius:8px;
                  background:#fef3c7; color:#b45309; border:1px solid #fbbf24; cursor:help; }}
  .overchip, .shchip, .datechip {{ font-size:9px; font-weight:700; padding:1px 6px; border-radius:8px;
               background:#fff; color:#6b7280; border:1px solid #d1d5db; cursor:help; }}
  .disagreechip {{ font-size:9px; font-weight:700; padding:1px 6px; border-radius:8px;
                   background:#fff; color:#374151; border:1px solid #9ca3af; cursor:help; }}
  .kindchip {{ font-size:9px; font-weight:600; padding:1px 6px; border-radius:8px;
               background:#fff; color:#6b7280; border:1px solid #d1d5db; cursor:help; }}
  .ownerchip {{ font-size:9px; font-weight:700; padding:1px 6px; border-radius:8px;
                background:#fff; color:#6b7280; border:1px solid #d1d5db; cursor:help; }}
  .changed-chip {{ font-size:9px; font-weight:700; padding:1px 6px; border-radius:8px;
                   background:#fff; color:#6b7280; border:1px solid #d1d5db; white-space:nowrap; }}
  .leadin-chip {{ font-size:9px; font-weight:700; padding:1px 6px; border-radius:8px;
                  background:#eef2ff; color:#6366f1; border:1px solid #c7d2fe; }}

  /* triage — always visible, slim (decision 3) */
  .triage {{ display:flex; align-items:center; gap:5px; flex-wrap:wrap;
            border-top:1px solid #f3f4f6; padding-top:8px; margin-top:6px; cursor:default; }}
  .tlabel {{ font-size:10px; text-transform:uppercase; letter-spacing:.04em; color:#c4c9d2; margin:0 2px; }}
  .tbtn {{ font-size:11px; padding:2px 9px; border:1px solid #e5e7eb; border-radius:999px;
           background:#fff; color:#6b7280; cursor:pointer; }}
  .tbtn.on {{ background:#334155; border-color:#334155; color:#fff; }}
  .cbtn {{ font-size:11px; padding:2px 10px; border:1px solid #cbd5e1; border-radius:999px;
           background:#fff; color:#334155; cursor:pointer; font-weight:600; margin-right:2px; }}
  .cbtn.on {{ background:#334155; border-color:#334155; color:#fff; }}
  .tnote {{ flex-basis:100%; font-family:inherit; font-size:12px; padding:5px 8px;
            border:1px solid #d1d5db; border-radius:5px; resize:vertical; color:#374151; }}

  /* review bar + filters */
  .reviewbar {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:10px;
                padding:8px 12px; background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; }}
  .rev-title {{ font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.04em; color:#475569; }}
  .rev-count {{ font-size:12px; font-weight:600; color:#334155; }}
  .rbtn {{ font-size:12px; padding:4px 10px; border:1px solid #334155; background:#334155;
           color:#fff; border-radius:5px; cursor:pointer; font-family:inherit; }}
  .rbtn:hover {{ background:#1f2937; }}
  .rbtn.sci {{ background:#fff; color:#334155; }}
  .rbtn.sci:hover {{ background:#f1f5f9; }}
  .rev-hint {{ font-size:11px; color:#6b7280; }}
  /* Slim review strip (friend feedback round 2): the bulky review bar + verdict
     ratio bar collapse behind "review tools"; what stays is a 4px two-color
     progress line — checked share vs claims left — moving on every ✓. */
  .revstrip {{ display:flex; align-items:center; gap:10px; margin-bottom:8px; }}
  .chkline {{ flex:1; height:4px; border-radius:2px; background:#e5e7eb; overflow:hidden; }}
  .chkfill {{ height:100%; width:0; background:#334155; border-radius:2px; transition:width .25s ease; }}
  .chklabel {{ font-size:11px; color:#6b7280; white-space:nowrap; }}
  .revtools.collapsed {{ display:none; }}
  .filterbar {{ display:flex; gap:6px; margin-bottom:10px; flex-wrap:wrap; }}
  .fbtn {{ font-size:12px; padding:3px 10px; border:1px solid #d1d5db; border-radius:999px; background:#fff; cursor:pointer; color:#374151; }}
  .fbtn.active {{ background:#1f2937; color:#fff; border-color:#1f2937; }}
  .fbtn .dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:5px; vertical-align:1px; }}
  .fbtn.partlyf {{ border-color:#fbbf24; color:#b45309; }}
  .fbtn.scopedf {{ border-color:#a5b4fc; color:#4338ca; }}
  .fbtn.hchk {{ border-color:#cbd5e1; color:#334155; }}
  .fbtn.hchk.active {{ background:#334155; border-color:#334155; color:#fff; }}
  body:not(.expert) .fbtn.conff {{ display:none; }}
  #omittedTail.collapsed {{ display:none; }}
  #omitToggle {{ margin:8px 0; }}

  /* ---- Focus view (default; friend feedback #1): the right pane shows ONLY
     the selected claim's card — the list-detail pattern. The all-cards list
     stays one toggle away ("show all cards", persisted like simple/expert).
     !important beats the filter's inline display so the selected card always
     shows; prev/next + arrow keys walk the cards the active filter matches. */
  body.detailview #claimList > .card {{ display:none !important; }}
  body.detailview #claimList > .card.active {{ display:block !important;
      box-shadow:0 1px 2px rgba(0,0,0,.05); border-color:#e5e7eb; }}
  body.detailview #omittedSec {{ display:none; }}
  .detailnav {{ display:none; }}
  body.detailview .detailnav {{ display:flex; align-items:center; gap:10px; margin-bottom:10px; }}
  .navbtn {{ font-size:12px; padding:3px 12px; border:1px solid #d1d5db; border-radius:999px;
             background:#fff; cursor:pointer; color:#374151; }}
  .navbtn:disabled {{ opacity:.35; cursor:default; }}
  .navpos {{ font-size:12px; color:#6b7280; }}
  .navhint {{ font-size:11px; color:#9ca3af; margin-left:auto; }}
  .detail-empty {{ display:none; }}
  body.detailview .detail-empty {{ display:block; border:1px dashed #d1d5db; border-radius:10px;
      padding:26px 18px; color:#6b7280; font-size:13px; background:#fff; text-align:center; }}
  body.detailview.hasactive .detail-empty {{ display:none; }}
  kbd {{ font-family:monospace; font-size:11px; background:#f3f4f6; border:1px solid #d1d5db;
         border-bottom-width:2px; border-radius:4px; padding:0 5px; }}
  .warnbanner {{ background:#fef3c7; color:#92400e; font-size:12px; padding:8px 24px; border-bottom:1px solid #fcd34d; }}
  .warnbanner ul {{ margin:4px 0 0; padding-left:20px; }}

  /* argument-structure panel (reused from v1) */
  .assess {{ background:#f8fafc; border-bottom:1px solid #e2e8f0; padding:10px 24px; }}
  .assess-head {{ display:flex; justify-content:space-between; align-items:center; }}
  .assess-title {{ font-weight:600; color:#334155; }}
  .am-note {{ font-weight:400; color:#9ca3af; font-size:11px; }}
  .assess-body {{ display:grid; grid-template-columns:1fr 1fr 1.2fr; gap:18px; margin-top:8px; }}
  .assess-body.collapsed {{ display:none; }}
  .assess-col h3 {{ font-size:13px; margin:0 0 6px; color:#334155; }}
  .assess-col h4 {{ font-size:12px; margin:8px 0 3px; color:#475569; }}
  .am-list {{ margin:0; padding-left:18px; font-size:12px; color:#374151; }}
  .am-list li {{ margin:3px 0; }}
  .am-edges {{ max-height:260px; overflow-y:auto; }}
  .am-score {{ display:inline-block; min-width:20px; font-weight:700; color:#334155; }}
  .am-why {{ color:#6b7280; font-size:11px; margin-left:2px; }}
  .am-frag {{ color:#374151; font-size:10px; border:1px solid #9ca3af; border-radius:3px; padding:0 4px; margin-left:4px; }}
  .am-strength {{ display:inline-block; font-size:10px; border-radius:3px; padding:0 5px; margin-right:4px; text-transform:uppercase; }}
  .am-strong {{ background:#e2e8f0; color:#1f2937; }}
  .am-weak {{ background:#f1f5f9; color:#64748b; }}
  .am-rels {{ color:#9ca3af; font-size:11px; }}
  .am-node {{ color:#374151; }}
  .am-edge {{ font-size:11px; padding:0 4px; }}
  .am-supports {{ color:#15803d; }}
  .am-attacks {{ color:#b91c1c; }}
  @media (max-width:900px) {{ .assess-body {{ grid-template-columns:1fr; }} }}
  .covratio {{ margin:6px 0 10px; }}
  .ccbar {{ display:flex; height:10px; border-radius:5px; overflow:hidden; background:#f3f4f6; }}
  .ccseg {{ height:100%; }}
  .cclegend {{ display:flex; flex-wrap:wrap; gap:10px; margin-top:5px; font-size:11px; color:#6b7280; }}
  .ccleg i {{ display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:3px; vertical-align:middle; }}
  .legend {{ margin:10px 24px 0; border:1px solid #e5e7eb; border-radius:8px; background:#fafafa; font-size:12px; }}
  .legend > summary {{ cursor:pointer; padding:8px 12px; font-weight:700; color:#374151; list-style:none; }}
  .legend > summary::-webkit-details-marker {{ display:none; }}
  .legend > summary::before {{ content:"▸ "; color:#9ca3af; }}
  .legend[open] > summary::before {{ content:"▾ "; }}
  .legend-body {{ padding:4px 14px 12px; display:flex; flex-wrap:wrap; gap:16px; }}
  .legend-grp {{ flex:1; min-width:260px; }}
  .legend-h {{ font-weight:700; color:#6b7280; text-transform:uppercase; letter-spacing:.04em; font-size:10px; margin:4px 0; }}
  .legrow {{ margin:5px 0; line-height:1.5; color:#4b5563; }}
  .legrow .badge {{ margin-right:5px; vertical-align:middle; }}
</style></head>
<body class="detailview">
<header>
  <div class="head-row">
    <h1>{_esc(title)}<span class="v2tag">viewer v2</span></h1>
    <span class="head-controls">
      <span class="modeseg" title="simple: verdict, claim and review buttons, with proofs one click away — expert: every section opened and the internals chips shown. The highlighted side is the view you are in now.">
        <button id="modeSimple" class="active" onclick="applyMode(false)">simple view</button><button id="modeExpert" onclick="applyMode(true)">expert view</button>
      </span>
      <button class="toggle" id="topToggle" onclick="toggleTop()"
              title="collapse everything above the two columns, so the text and cards get the full page">hide header</button>
    </span>
  </div>
  <div class="totals">{totals.get('claims',0)} claims &nbsp;·&nbsp;
    <b class="s">{n_green} supported</b>{partly_total} &nbsp;·&nbsp;
    <b class="u">{n_uns - n_unverifiable} unsupported</b>{unverifiable_total} &nbsp;·&nbsp;{scoped_total}
    <b style="color:#a5b4fc">{totals.get('own',0)} your own</b>{omitted_total}{cite_total}{changed_total}</div>
  <div class="scopenote">&ldquo;supported&rdquo; means the cited document contains the statement —
    not that the source is strong or the claim is true; &ldquo;your own&rdquo; (uncited) claims were not checked.</div>
</header>
<div id="topPanel">
{legend_html}
{warn_html}
<div class="modebar" id="modebar"></div>
<div class="coverage">
  <div class="cov-head">
    <div class="cov-title"><b>Source coverage</b> — a bibliography health check ({n_sources} sources):
      which sources actually back your claims, which back nothing (wrong file? never actually used?),
      and whether everything leans on a single source. An empty bar is usually structural: the citing
      claims were judged unsupported, or a co-cited source supplied the evidence — the label on each
      row says which.</div>
    <button class="toggle" onclick="toggleCoverage(this)" title="show or hide the per-source coverage bars">{cov_btn_label}</button>
  </div>
  <div class="cov-bars{cov_collapsed}" id="covBars">{coverage_html}</div>
</div>
{assess_section}
</div>
<div class="layout">
  <div class="doc-wrap">
    <div class="doc-head"><h2>Your text</h2><button class="toggle" onclick="toggleDoc(this)" title="hide your text so the cards get the full width">collapse</button></div>
    <div class="doc" id="doc">{text_html}</div>
  </div>
  <div class="cards">
    <div class="doc-head"><h2>Your claims — in text order</h2>
      <button class="toggle" id="viewToggle" onclick="toggleView()"
              title="focus view shows only the claim you clicked; all cards shows every card in one scrolling list">show all cards</button>
    </div>
    <div class="cards-body">
      <div class="revstrip">
        <button class="toggle" id="revToolsBtn" onclick="toggleRevTools()"
                title="when you're done marking cards: copy the repair brief, download/load the review file, or build a research request — the verdict-mix bar lives here too">&#9656; export review</button>
        <div class="chkline" title="your review progress — the filled part is the claims you marked &#10003; checked">
          <div class="chkfill" id="chkFill"></div></div>
        <span class="chklabel" id="chkLabel"></span>
        <button class="toggle" id="lastChkBtn" onclick="gotoLastChecked()" style="display:none"
                title="jump to the claim you most recently marked &#10003; checked — resume where you left off">&#8618; last checked</button>
      </div>
      <div id="revTools" class="revtools collapsed">
      {review_bar}
      {coverage_ratio}
      </div>
      {filter_bar}
      <div class="detailnav" id="detailNav">
        <button class="navbtn" id="prevBtn" onclick="navDetail(-1)">&lsaquo; previous</button>
        <span class="navpos" id="detailPos"></span>
        <button class="navbtn" id="nextBtn" onclick="navDetail(1)">next &rsaquo;</button>
        <span class="navhint"><kbd>&larr;</kbd> <kbd>&rarr;</kbd> step through claims</span>
      </div>
      <div class="detail-empty" id="detailEmpty">
        <p><b>Click any sentence in your text</b> to inspect its claim here.</p>
        <p class="meta">Or press <kbd>&rarr;</kbd> to start at the first claim —
          the filter chips above narrow what prev/next steps through.</p>
      </div>
      <div id="claimList">{claim_cards or '<p class="meta">none</p>'}</div>
      <div id="omittedSec">{omitted_section}</div>
    </div>
  </div>
</div>
<script>
const HAS_PDF = {str(has_pdf).lower()};
const HAS_TEXT = {str(has_text).lower()};
const SOURCE_TEXTS = {st_json};
(function(){{
  const bar = document.getElementById('modebar');
  const dotG = '<span class="dot" style="background:#cbd5e1"></span>';
  const dotGray = '<span class="dot" style="background:#6b7280"></span>';
  if (HAS_PDF || HAS_TEXT) bar.innerHTML = dotG + 'On each proof sentence: <b>↗</b> opens the cited source in a new tab; <b>⊞ side window</b> opens it in one reused window you can dock on the right (next click swaps its content). PDFs jump to the page, text is highlighted. No server needed.';
  else bar.innerHTML = dotGray + 'No viewable sources — the supporting sentence for each claim is quoted on the card (with a Copy button).';
}})();

let activeId = null;
// Scoped to claim spans + cards: a bare '.active' would also strip the active
// FILTER button (and v2's mode segments), silently resetting them on every click.
function clearActive() {{ document.querySelectorAll('.claim.active, .card.active').forEach(e => e.classList.remove('active')); }}
function brush(id, from) {{
  clearActive();
  // A claim may render as several spans (tail-rescue: indigo lead-in with no id +
  // a verdict-colored tail carrying text-<id>). Activate EVERY span of the claim.
  const spans = document.querySelectorAll('.claim[data-card="card-' + id + '"]');
  spans.forEach(function(s) {{ s.classList.add('active'); }});
  const t = spans[0] || document.getElementById('text-' + id);
  const c = document.getElementById('card-' + id);
  if (c) {{
    if (c.style.display === 'none') {{
      const all = document.querySelector('.fbtn[data-f="all"]');
      if (all) all.click();
    }}
    c.classList.add('active');
    if (from !== 'card') {{
      if (document.body.classList.contains('detailview')) {{
        // Focus view: the selected card is the only one shown — scroll the
        // detail pane to its top (Gmail/GitHub convention), not into center.
        const cb = document.querySelector('.cards-body');
        if (cb) cb.scrollTop = 0;
      }} else {{
        c.scrollIntoView({{behavior:'smooth', block:'center'}});
      }}
    }}
  }}
  if (t && from === 'card') t.scrollIntoView({{behavior:'smooth', block:'center'}});
  updateDetailPane();
}}
function copyText(btn, ev) {{
  if (ev) ev.stopPropagation();
  cgCopy(btn.getAttribute('data-quote'), btn);
}}
function toggleDoc(btn) {{
  // Collapsing the text must actually hand its width to the cards column
  // (owner 7/15): shrink the wrapper, don't just hide the text.
  const d = document.getElementById('doc');
  const w = d.closest('.doc-wrap');
  const collapsed = d.classList.toggle('collapsed');
  if (w) w.classList.toggle('collapsed', collapsed);
  btn.textContent = collapsed ? 'expand' : 'collapse';
}}
function toggleCoverage(btn) {{
  const b = document.getElementById('covBars');
  b.classList.toggle('collapsed');
  btn.textContent = b.classList.contains('collapsed') ? 'show' : 'hide';
}}
function setTop(hidden) {{
  const p = document.getElementById('topPanel');
  const note = document.querySelector('.scopenote');
  const btn = document.getElementById('topToggle');
  if (p) p.style.display = hidden ? 'none' : '';
  if (note) note.style.display = hidden ? 'none' : '';
  if (btn) btn.textContent = hidden ? 'show header' : 'hide header';
  try {{ localStorage.setItem('ptui:tophidden', hidden ? '1' : ''); }} catch (e) {{}}
}}
function toggleTop() {{
  const p = document.getElementById('topPanel');
  setTop(p && p.style.display !== 'none');
}}
try {{ if (localStorage.getItem('ptui:tophidden') === '1') setTop(true); }} catch (e) {{}}
// Simple vs expert (v2): expert shows internals chips and opens every expander.
// The segmented control SHOWS the current state (owner 7/15 #7).
// Shares the 'ptui:expert' preference with v1.
function applyMode(expert) {{
  document.body.classList.toggle('expert', expert);
  const s = document.getElementById('modeSimple'), e = document.getElementById('modeExpert');
  if (s) s.classList.toggle('active', !expert);
  if (e) e.classList.toggle('active', expert);
  document.querySelectorAll('#claimList details.x').forEach(d => d.open = expert);
  try {{ localStorage.setItem('ptui:expert', expert ? '1' : ''); }} catch (err) {{}}
}}
try {{ if (localStorage.getItem('ptui:expert') === '1') applyMode(true); }} catch (e) {{}}
// ---- Focus (single-card) vs all-cards view: friend feedback #1. Focus is the
// default; the choice is a UI preference (persists across runs, shared with v1),
// never run state.
function visibleFilterCards() {{
  const b = document.querySelector('.fbtn.active') || document.querySelector('.fbtn[data-f="all"]');
  const f = b ? b.dataset.f : 'all';
  return Array.from(document.querySelectorAll('#claimList > .card'))
              .filter(function(c) {{ return _cardMatchesFilter(c, f); }});
}}
function updateDetailPane() {{
  const act = document.querySelector('#claimList > .card.active');
  document.body.classList.toggle('hasactive', !!act);
  if (!document.body.classList.contains('detailview')) return;
  const cards = visibleFilterCards();
  const i = act ? cards.indexOf(act) : -1;
  const pos = document.getElementById('detailPos');
  if (pos) pos.textContent = (i >= 0 ? (i + 1) : '—') + ' / ' + cards.length;
  const prev = document.getElementById('prevBtn'), next = document.getElementById('nextBtn');
  if (prev) prev.disabled = (i <= 0);
  if (next) next.disabled = (i >= 0 && i >= cards.length - 1) || !cards.length;
}}
function navDetail(d) {{
  const cards = visibleFilterCards();
  if (!cards.length) return;
  const act = document.querySelector('#claimList > .card.active');
  let i = act ? cards.indexOf(act) : -1;
  i = (i === -1) ? (d > 0 ? 0 : cards.length - 1)
                 : Math.min(Math.max(i + d, 0), cards.length - 1);
  // from='card': the action lives in the card pane, so the TEXT scrolls to the
  // claim (opposite-panel rule) and the detail pane just swaps its card.
  brush(cards[i].id.replace(/^card-/, ''), 'card');
}}
function applyView(list) {{
  document.body.classList.toggle('detailview', !list);
  const b = document.getElementById('viewToggle');
  if (b) b.textContent = list ? 'focus view' : 'show all cards';
  try {{ localStorage.setItem('ptui:listview', list ? '1' : ''); }} catch (e) {{}}
  updateDetailPane();
}}
function toggleView() {{ applyView(document.body.classList.contains('detailview')); }}
try {{ if (localStorage.getItem('ptui:listview') === '1') applyView(true); }} catch (e) {{}}
updateDetailPane();   // initial nav state ("— / N", prev disabled) on load
// Review tools (export buttons + verdict-mix bar) collapse behind one small
// toggle — end-of-session tools, not reading chrome. Preference persists.
function setRevTools(open) {{
  const t = document.getElementById('revTools'), b = document.getElementById('revToolsBtn');
  if (t) t.classList.toggle('collapsed', !open);
  if (b) b.innerHTML = (open ? '&#9662;' : '&#9656;') + ' export review';
  try {{ localStorage.setItem('ptui:revtools', open ? '1' : ''); }} catch (e) {{}}
}}
function toggleRevTools() {{
  const t = document.getElementById('revTools');
  setRevTools(t && t.classList.contains('collapsed'));
}}
try {{ if (localStorage.getItem('ptui:revtools') === '1') setRevTools(true); }} catch (e) {{}}
// "↦ last checked": resume a review — jump to the most recently ✓-checked
// claim (recorded by REVIEW_JS, persisted per run); falls back to the last
// checked card in document order if that record is stale or missing.
function gotoLastChecked() {{
  let id = null;
  try {{ id = localStorage.getItem(REVIEW_KEY + ':last'); }} catch (e) {{}}
  if (!(id && review[id] && review[id].checked && document.getElementById('card-' + id))) {{
    const cards = document.querySelectorAll('#claimList > .card.hchecked');
    id = cards.length ? cards[cards.length - 1].id.replace(/^card-/, '') : null;
  }}
  if (!id) return;
  brush(id, 'card');   // scrolls the TEXT to the claim; focus view shows its card
  if (!document.body.classList.contains('detailview')) {{
    const c = document.getElementById('card-' + id);   // list view: bring the card over too
    if (c) c.scrollIntoView({{behavior:'smooth', block:'center'}});
  }}
}}
// Left/right arrows step through claims in focus view. Up/down stay untouched
// (they scroll the text column); typing fields are never hijacked.
document.addEventListener('keydown', function(ev) {{
  if (!document.body.classList.contains('detailview')) return;
  const t = ev.target;
  if (t && (t.tagName === 'TEXTAREA' || t.tagName === 'INPUT' || t.isContentEditable)) return;
  if (ev.key === 'ArrowRight') {{ ev.preventDefault(); navDetail(1); }}
  else if (ev.key === 'ArrowLeft') {{ ev.preventDefault(); navDetail(-1); }}
}});
(function() {{
  const ab = document.getElementById('assessBody'), at = document.getElementById('assessToggle');
  if (ab && at && !document.body.classList.contains('expert') && !ab.classList.contains('collapsed')) {{
    ab.classList.add('collapsed'); at.textContent = 'expand';
  }}
}})();
function toggleAssess() {{
  const b = document.getElementById('assessBody');
  b.classList.toggle('collapsed');
  const t = document.getElementById('assessToggle');
  if (t) t.textContent = b.classList.contains('collapsed') ? 'expand' : 'collapse';
}}
function toggleOmitted() {{
  const t = document.getElementById('omittedTail');
  const open = t.classList.toggle('collapsed') === false;
  const n = t.querySelectorAll('.card').length;
  const ot = document.getElementById('omitToggle');
  if (ot) ot.textContent = open ? 'Hide less-relevant' : ('Show ' + n + ' more (less relevant)');
}}
document.querySelectorAll('#claimList details.x').forEach(function(d) {{
  d.addEventListener('click', function(ev) {{ ev.stopPropagation(); }});
}});

function escapeHtml(s) {{
  return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}}

const SIDE_WIN = 'pt_source_side';
const SIDE_FEATURES = 'width=900,height=1024';

function findInText(text, needle) {{
  if (!needle) return null;
  const i = text.indexOf(needle);
  if (i !== -1) return {{start: i, len: needle.length}};
  try {{
    const pat = needle.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&').replace(/ +/g, '\\\\s+');
    const m = text.match(new RegExp(pat));
    if (m) return {{start: m.index, len: m[0].length}};
  }} catch (e) {{}}
  return null;
}}

function buildTextDoc(pid, sentence, snippet) {{
  const text = SOURCE_TEXTS[pid] || '';
  const hit = findInText(text, sentence) || findInText(text, snippet);

  let body, note = '';
  if (hit) {{
    body = escapeHtml(text.slice(0, hit.start)) + '<mark id="hl">'
         + escapeHtml(text.slice(hit.start, hit.start + hit.len))
         + '</mark>' + escapeHtml(text.slice(hit.start + hit.len));
  }} else {{
    body = escapeHtml(text);
    if (sentence || snippet)   // opened WITHOUT a sentence (no-passage rows): no warning needed
      note = "<p style=\\"color:#b45309;font-style:italic\\">Couldn't locate the exact sentence — showing the full source.</p>";
  }}
  return '<!DOCTYPE html><html><head><meta charset="utf-8"><title>Source text</title>'
    + '<style>body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#1f2937;'
    + 'background:#fff;margin:0;padding:24px 28px;line-height:1.7;font-size:14px;'
    + 'white-space:pre-wrap;word-wrap:break-word;}}mark{{background:#fde047;padding:1px 0;border-radius:2px;}}</style>'
    + '</head><body onload="var h=document.getElementById(\\'hl\\');if(h)h.scrollIntoView({{block:\\'center\\'}});">'
    + note + body + '</body></html>';
}}

function openTextSource(pid, sentence, snippet, target, features) {{
  const doc = buildTextDoc(pid, sentence, snippet);
  let w = window.open('', target, features || '');
  if (!w) {{ alert('Please allow pop-ups to open the source.'); return; }}
  try {{
    w.document.open(); w.document.write(doc); w.document.close();
  }} catch (e) {{
    const url = URL.createObjectURL(new Blob([doc], {{type:'text/html'}}));
    w = window.open(url, target, features || '');
  }}
  if (w) w.focus();
}}

function openPdfSide(href) {{
  const w = window.open(href, SIDE_WIN, SIDE_FEATURES);
  if (!w) {{ alert('Please allow pop-ups to open the PDF in the side window.'); return; }}
  w.focus();
}}

document.querySelectorAll('.opentext-btn').forEach(function(b) {{
  b.addEventListener('click', function(ev) {{
    ev.stopPropagation();
    openTextSource(b.dataset.pid, b.dataset.sentence || '', b.dataset.snippet || '', '_blank', '');
  }});
}});
document.querySelectorAll('.side-btn').forEach(function(b) {{
  b.addEventListener('click', function(ev) {{
    ev.stopPropagation();
    if (b.dataset.kind === 'pdf') openPdfSide(b.dataset.href);
    else openTextSource(b.dataset.pid, b.dataset.sentence || '', b.dataset.snippet || '', SIDE_WIN, SIDE_FEATURES);
  }});
}});
function _cardMatchesFilter(c, f) {{
  return (f === 'all') ? true
       : (f === 'hunchecked') ? !c.classList.contains('hchecked')
       : c.classList.contains(f);
}}
function reapplyActiveFilter() {{
  const b = document.querySelector('.fbtn.active') || document.querySelector('.fbtn[data-f="all"]');
  const f = b ? b.dataset.f : 'all';
  document.querySelectorAll('#claimList > .card').forEach(function(c) {{
    c.classList.remove('leaving');
    c.style.display = _cardMatchesFilter(c, f) ? '' : 'none';
  }});
  updateDetailPane();
}}
// After a triage toggle (e.g. ✓ checked) a card that no longer matches the active
// filter (e.g. "Unchecked") leaves the list right away, with a short fade.
function refilterAfterToggle(card) {{
  const b = document.querySelector('.fbtn.active') || document.querySelector('.fbtn[data-f="all"]');
  const f = b ? b.dataset.f : 'all';
  if (card && card.style.display !== 'none' && !_cardMatchesFilter(card, f)) {{
    card.classList.add('leaving');
    setTimeout(function() {{
      // Focus view: the departing card was the one on screen — advance to the
      // next card the filter still matches (systematic-review flow); the empty
      // state shows when none is left.
      const wasActive = card.classList.contains('active');
      reapplyActiveFilter();
      if (wasActive && document.body.classList.contains('detailview')) {{
        let n = card.nextElementSibling;
        while (n && !(n.classList.contains('card') && _cardMatchesFilter(n, f))) n = n.nextElementSibling;
        if (n) brush(n.id.replace(/^card-/, ''), 'card');
        else {{ card.classList.remove('active'); clearActive(); updateDetailPane(); }}
      }}
    }}, 220);
  }} else {{
    reapplyActiveFilter();
  }}
}}
window.reapplyActiveFilter = reapplyActiveFilter;
window.refilterAfterToggle = refilterAfterToggle;
document.querySelectorAll('.fbtn').forEach(function(b) {{
  b.addEventListener('click', function() {{
    document.querySelectorAll('.fbtn').forEach(function(x) {{ x.classList.remove('active'); }});
    b.classList.add('active');
    reapplyActiveFilter();
  }});
}});
document.querySelectorAll('.deeplink, .paperlink').forEach(function(a) {{
  a.addEventListener('click', function(ev) {{ ev.stopPropagation(); }});
}});

// ---- Review loop (triage marks, repair-brief / review.json export) ----
const RUN_ID = '{run_id}';
const REVIEW_DATA = {rd_json};
{REVIEW_JS}
</script>
</body></html>"""

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(page)
    logger.info(f"Wrote viewer v2: {output_path}")
    return output_path


def regenerate_from_run(run_dir: str) -> str:
    """Rebuild viewer_v2.html from a finished run's analysis.json — no LLM,
    no network. Mirrors verify_my_text.py's viewer-refresh assembly."""
    with open(os.path.join(run_dir, "analysis.json"), encoding="utf-8") as f:
        analysis = json.load(f)
    source_texts = {}
    for s in analysis.get("sources", []):
        fn = s.get("filename")
        if fn and not fn.lower().endswith(".pdf"):
            path = os.path.join(run_dir, "sources", fn)
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8", errors="ignore") as sf:
                    source_texts[s["paper_id"]] = sf.read()
    assessment = {}
    for akey, fn in (("argument_map", "argument_map.json"),
                     ("independence", "independence.json"),
                     ("crux", "crux.json")):
        p = os.path.join(run_dir, fn)
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    assessment[akey] = json.load(f)
            except Exception as e:
                logger.warning(f"Could not reload {fn} for the viewer: {e}")
    text_file = analysis.get("metadata", {}).get("text_file", "")
    return generate(analysis, os.path.join(run_dir, "viewer_v2.html"),
                    title=f"Verification — {os.path.basename(text_file) or os.path.basename(run_dir)}",
                    source_texts=source_texts, assessment=assessment or None)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(sys.argv) != 2:
        print("usage: python3 -m modules.papertrail.viewer_v2 <run_dir>", file=sys.stderr)
        sys.exit(1)
    print(regenerate_from_run(sys.argv[1]))
