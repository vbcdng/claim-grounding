"""
PaperTrail-style self-contained HTML review interface.

Two columns: the user's text (collapsible) + claim cards (supported / unsupported /
omitted), each showing the verbatim supporting sentence. There is no embedded
preview pane and no server requirement — the HTML is fully persistent and works by
double-clicking it (file://).

Each card links to its cited source, opening in a new browser tab:
  • PDF sources — a deep-link (sources/<file>#page=N) that opens the browser's
    native PDF viewer jumped to the cited page.
  • Text sources — the full source text is embedded in the page; clicking opens it
    in a new tab with the supporting sentence highlighted and scrolled into view.
Both work anytime, including from a double-clicked file:// copy, with no server.
"""

import os
import re
import html
import json
import hashlib
import logging
import shlex
from urllib.parse import quote
from typing import Dict, Any, Optional

from modules.papertrail import text_decomposer

logger = logging.getLogger(__name__)

# How many omitted source-claims (ranked by relevance) to show before collapsing
# the rest behind a "show more" toggle.
OMITTED_SHOWN = 15
# Hard cap on omitted cards EMBEDDED in the HTML. Real runs can produce tens of
# thousands of omitted source claims (paper1: ~30k -> a 47 MB viewer that chokes
# browsers); they are relevance-ranked, so everything past the cap is noise —
# it stays in analysis.json for anyone who wants it.
OMITTED_EMBED_CAP = 200


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _norm_ws(s: str) -> str:
    return " ".join((s or "").split())


def _confidence(c: Dict[str, Any]) -> Optional[tuple]:
    """(level, why) — how sure the judge was, derived from signals the run
    already records (vote tallies, which pipeline stage decided, match
    strength). A proxy, deliberately: LLM self-reported confidence is
    miscalibrated and would need prompt changes + re-runs; this works
    retroactively on any existing analysis.json. Returns None when a tag
    would be meaningless (own claims, missing source files)."""
    verdict = c.get("verdict")
    if verdict not in ("supported", "unsupported"):
        return None
    if verdict == "unsupported" and str(c.get("reason", "")).startswith("source_file_missing"):
        return None                        # input problem, not a judgment
    if (c.get("second_opinion") or {}).get("agrees") is False:
        return ("low", "a second model read the same evidence and reached the opposite "
                       "verdict — read the evidence and decide yourself")
    if c.get("partial_support"):
        return ("low", "the cited sources back only part of this claim — a specific "
                       "component was found in none of them; read the evidence and "
                       "confirm that part")
    if verdict == "supported" and c.get("proof_state") == "partial":
        return ("low", "the shown sentences do not prove every component of this claim "
                       "— the amber line names the part with no evidence shown; read "
                       "the source and confirm it yourself")
    evidences = [e for e in (c.get("evidences") or []) if e]
    votes = [c.get("votes")] + [e.get("votes") for e in evidences]
    if "2-1" in votes:
        return ("low", "the judges split 2–1 — a borderline call, read the evidence yourself")
    method = c.get("method", "")
    if method in ("combined", "combined_fulltext", "tail_rescue", "component_rescue"):
        return ("medium", "an indirect verdict (sources combined, only the cited tail "
                          "re-judged, or components verified separately then re-judged "
                          "together) — solid, but worth a glance")
    if verdict == "unsupported" and not any(e.get("sentence") for e in evidences):
        return ("medium", "no relevant sentence was found at all — the claim may be "
                          "unsupported, or the search may simply have missed the passage")
    if verdict == "supported" and method == "llm_fulltext" and (c.get("cosine") or 0) < 0.75:
        return ("medium", "supported only via the deep full-text read, with a weak "
                          "similarity match — verify the quote")
    if verdict == "supported":
        return ("high", "the judge accepted a top-ranked sentence directly")
    return ("high", "unanimous rejection after reading the extracted evidence")


def _filename_map(analysis: Dict[str, Any]) -> Dict[str, str]:
    return {s.get("paper_id"): s.get("filename") for s in analysis.get("sources", []) if s.get("filename")}


def _paper_meta(analysis: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """paper_id -> {title, url}. url is optional (used directly if present)."""
    return {s.get("paper_id"): {"title": s.get("title") or "", "url": s.get("url") or ""}
            for s in analysis.get("sources", [])}


def _paper_link(meta: Dict[str, Dict[str, str]], paper_id: str, fallback_title: str = "") -> str:
    """A link to the original paper's webpage for a supporting sentence.

    Uses the source's explicit `url` if the analysis provides one; otherwise falls
    back to a Google Scholar search built from the paper title (always works, no
    stored metadata needed). Returns '' if there's nothing to link.
    """
    info = meta.get(paper_id, {})
    url = (info.get("url") or "").strip()
    if url:
        return (f'<a class="paperlink" href="{_esc(url)}" target="_blank" rel="noopener" '
                f'title="Open the original paper\'s webpage">View paper ↗</a>')
    title = info.get("title") or fallback_title or ""
    # Titles are often the filename stem with a trailing _<id> hash (e.g. "_76e6e4dcfe")
    # and underscores for spaces — clean both so the search query matches the real paper.
    title = re.sub(r"[_\s]+[0-9a-f]{8,}$", "", title).replace("_", " ").strip()
    if not title:
        return ""
    q = quote(title)
    href = f"https://scholar.google.com/scholar?q={q}"
    return (f'<a class="paperlink" href="{href}" target="_blank" rel="noopener" '
            f'title="Find the original paper (Google Scholar search by title)">Find paper ↗</a>')


def _coverage_status(info: Dict[str, Any]) -> str:
    """Why a source's bar looks the way it does (owner walkthrough item 6:
    '0 used' must not read as 'source useless'). Falls back to '' for
    pre-2026-07-07 analyses without the citation stats."""
    if "cited_by" not in info:
        return ""
    backs, cited = info.get("supported", 0), info.get("cited_by", 0)
    if backs:
        return f"backs {backs} of your claim{'s' if backs != 1 else ''}"
    if not cited:
        return "not cited by any claim"
    if info.get("citing_supported", 0):
        return (f"cited by {cited} claim{'s' if cited != 1 else ''} — the "
                "supporting evidence came from co-cited sources")
    return (f"cited by {cited} claim{'s' if cited != 1 else ''}, none judged "
            "supported")


def _coverage_bars(coverage: Dict[str, Any]) -> str:
    rows = []
    # Sources that actually back claims first; the was-arbitrary set order made
    # the panel open on a wall of zeros (owner walkthrough item 6).
    ordered = sorted(coverage.get("per_source", {}).items(),
                     key=lambda kv: (-(kv[1].get("supported") or 0),
                                     -(kv[1].get("used") or 0),
                                     -(kv[1].get("cited_by") or 0),
                                     str(kv[1].get("title") or "")))
    for pid, info in ordered:
        total = info.get("total_source_claims", 0)
        used = info.get("used", 0)
        pct = (used / total * 100) if total else 0
        status = _coverage_status(info)
        status_html = f'<span class="cov-status">{_esc(status)}</span>' if status else ""
        # total==0 means source decomposition didn't run (retired from the CLI
        # 2026-07-16) — "0 / 0 source claims in evidence"
        # is meaningless noise then; the status label carries the row.
        count_str = f"{used} / {total} source claims in evidence" if total else ""
        rows.append(f"""
        <div class="cov-row">
          <div class="cov-label">{_esc(info.get('title') or pid)}</div>
          <div class="cov-track"><div class="cov-fill" style="width:{pct:.0f}%"></div></div>
          <div class="cov-num">{status_html}{count_str}</div>
        </div>""")
    return "\n".join(rows)


def _is_scoped(c: Dict[str, Any]) -> bool:
    """Scoped-citation class (§6.7): unsupported verdict, but the citation was
    classified a methods/concept/related pointer inside the authors' own text.
    Rendered as its OWN class everywhere (indigo, own count, own filter) so it
    is never confused with a real unsupported — the verdict field itself is
    untouched (owner ask 2026-07-12)."""
    return (c.get("verdict") == "unsupported"
            and (c.get("citation_scope") or {}).get("scope")
            in ("methods", "concept", "related"))


def _claim_span(c: Dict[str, Any]) -> str:
    verdict = "scopedcite" if _is_scoped(c) else c["verdict"]
    markers = "".join(f'<sup class="mark">[{_esc(m)}]</sup>' for m in c.get("markers", []))
    if (c.get("prev") or {}).get("changed"):
        markers += '<sup class="changedmark" title="edited since the last run">✎</sup>'
    rescue = c.get("tail_rescue") or {}
    if rescue.get("supported"):
        # The citation covers only the tail; the lead-in is the author's own
        # framing — indigo like an 'own' claim, never green as if it were checked.
        return (f'<span class="claim own" data-card="card-{c["id"]}" title="{_esc(c["id"])} — your own lead-in" '
                f'onclick="brush(\'{c["id"]}\', \'text\')">{_esc(rescue["lead_in"])}</span> '
                f'<span class="claim {verdict}" id="text-{c["id"]}" '
                f'data-card="card-{c["id"]}" title="{_esc(c["id"])}" onclick="brush(\'{c["id"]}\', \'text\')">'
                f'{_esc(rescue["tail"])}{markers}</span> ')
    return (f'<span class="claim {verdict}" id="text-{c["id"]}" '
            f'data-card="card-{c["id"]}" title="{_esc(c["id"])}" onclick="brush(\'{c["id"]}\', \'text\')">'
            f'{_esc(c["text"])}{markers}</span> ')


def _source_actions(fname_map: Dict[str, str], paper_id: str, page: Optional[int],
                    sentence: str, snippet: str, source_texts: Dict[str, str]) -> str:
    """The single 'open the cited source in a new tab' affordance for a card.

    PDF sources: a deep-link (sources/<file>#page=N) opening the browser's native PDF
    viewer at the cited page. Text (.txt) sources: a button that opens the embedded
    source text in a new tab with the supporting sentence highlighted. Both work from
    file:// with no server.
    """
    filename = fname_map.get(paper_id)
    if not filename:
        return ""

    if filename.lower().endswith(".pdf"):
        search_term = sentence or snippet or ""
        # Deep-link (works anytime, incl. file://). #page is honoured by Chrome+Firefox;
        # &search is a Firefox bonus and harmlessly ignored elsewhere.
        href = "sources/" + quote(filename)
        frag = []
        if page:
            frag.append(f"page={int(page)}")
        if search_term:
            frag.append("search=" + quote(search_term))
        if frag:
            href += "#" + "&".join(frag)
        return f"""
      <div class="src-actions">
        <a class="deeplink" href="{_esc(href)}" target="_blank" rel="noopener">Open PDF{f' · p.{int(page)}' if page else ''} ↗</a>
        <button class="side-btn" data-kind="pdf" data-href="{_esc(href)}"
                title="Open in one reused side window — keep it docked on the right">⊞ side window</button>
      </div>"""

    if paper_id in source_texts:
        return f"""
      <div class="src-actions">
        <button class="opentext-btn" data-pid="{_esc(paper_id)}"
                data-sentence="{_esc(sentence)}" data-snippet="{_esc(snippet)}"
                title="Opens the source text in a new tab with the sentence highlighted">Open source text ↗</button>
        <button class="side-btn" data-kind="text" data-pid="{_esc(paper_id)}"
                data-sentence="{_esc(sentence)}" data-snippet="{_esc(snippet)}"
                title="Open the source text in one reused side window — keep it docked on the right">⊞ side window</button>
      </div>"""

    return ""


def _fix_section(c: Dict[str, Any], fix_cmd_base: str) -> str:
    """For a judged-unsupported claim: the verified rewrite suggestion when one
    exists, otherwise the copyable --fix-claim command that generates it (the
    viewer is static — a live LLM call needs the CLI, per the ROADMAP decision)."""
    if c.get("verdict") != "unsupported" or not c.get("paper_ids"):
        return ""
    fs = c.get("fix_suggestion")
    if fs:
        ok = bool(fs.get("verified_supported"))
        chip = ("✓ re-checked: supported by the sources" if ok
                else "⚠ re-check inconclusive — review manually")
        changes = (f'<div class="fix-changes">{_esc(fs.get("changes", ""))}</div>'
                   if fs.get("changes") else "")
        return f"""
          <div class="fixbox">
            <div class="fix-head">Suggested fix <span class="fixchip {'ok' if ok else 'warn'}">{chip}</span></div>
            <blockquote>{_esc(fs.get("text", ""))}</blockquote>
            <button class="copy" onclick="copyText(this)" data-quote="{_esc(fs.get('text', ''))}">Copy fixed text</button>
            {changes}
          </div>"""
    if fix_cmd_base:
        cmd = fix_cmd_base + c["id"]
        return f"""
          <div class="fixcmd">To get a rewrite supported by the sources, run:
            <code>{_esc(cmd)}</code>
            <button class="copy" onclick="copyText(this)" data-quote="{_esc(cmd)}">Copy command</button>
          </div>"""
    return ""


# Secondhand-evidence detection (owner walkthrough item 12, t9): the SUPPORTING
# sentence itself cites another work — the author may be citing a middleman.
# Render-time regex on the evidence sentence; a grey nudge, never a verdict.
_SECONDHAND_RE = re.compile(
    r"\(\s*(?:e\.g\.,?\s*)?[A-Z][\w'’-]+(?:\s+(?:and|&)\s+[A-Z][\w'’-]+)*"
    r"(?:\s+et\s+al\.?)?,?\s+(?:19|20)\d{2}[a-z]?(?:\s*[;,]\s*[^()]{0,60})?\)"  # (Baumol, 1967) / (Aghion et al. 2017; …)
    r"|\[\d{1,3}(?:\s*[,–-]\s*\d{1,3})*\]"                                     # [12] / [3, 4]
    r"|\b[A-Z][\w'’-]+(?:\s+(?:and|&)\s+[A-Z][\w'’-]+)?"
    r"(?:\s+et\s+al\.?)?\s+\(\s*(?:19|20)\d{2}[a-z]?\s*\)")                    # Aghion et al. (2017)

# Cited-sources disagreement (owner walkthrough item 14, t8): a co-cited source's
# best passage was judged to CONTRADICT the claim (not merely miss it).
_DISAGREE_RE = re.compile(r"(?i)\bcontradict|\bopposite\b|\bcontrary to\b|\brefute|"
                          r"\bdisagree|\bargues? against\b")

_EVIDENCE_CLAMP_CHARS = 700   # boilerplate-length "sentences" collapse behind a toggle


def _clamped_quote(sentence: str) -> str:
    """Blockquote with a show-all toggle for absurdly long 'sentences' (nav dumps,
    glued captions — owner walkthrough item 8's display side)."""
    if len(sentence) <= _EVIDENCE_CLAMP_CHARS:
        return f'<blockquote>{_esc(sentence)}</blockquote>'
    head = sentence[:_EVIDENCE_CLAMP_CHARS].rsplit(" ", 1)[0]
    return (f'<blockquote>{_esc(head)}…</blockquote>'
            f'<details class="judged"><summary>show the full passage '
            f'({len(sentence.split())} words)</summary>'
            f'<div class="judged-text">{_esc(sentence)}</div></details>')


def _claim_card(c: Dict[str, Any], fname_map: Dict[str, str], source_texts: Dict[str, str],
                paper_meta: Dict[str, Dict[str, str]], fix_cmd_base: str = "") -> str:
    verdict = c["verdict"]
    ev = c.get("evidence")
    badge = {"supported": "SUPPORTED", "unsupported": "UNSUPPORTED",
             "own": "YOUR OWN CLAIM"}.get(verdict, verdict.upper())
    badge_cls = verdict
    # Proof-state badge. Round-4 introduced "SUPPORTED — PARTLY PROVEN";
    # round-8 fix B (owner ruled the class 4×: r5 t0, r6 t1, first-check
    # t1/t4 — "the system KNOWS the data are missing but says supported")
    # removes the word "supported" from the badge entirely when the
    # post-audit amber holds real (non-common-knowledge) gaps. DISPLAY ONLY:
    # the verdict field is unchanged (gate-compatible shape, the only one the
    # 3-paper gate tolerates — t8/t28 trap).
    if verdict == "supported" and c.get("proof_state") == "partial":
        badge = "NOT PROVEN AS WRITTEN"
        badge_cls = "supported partly"
    # Citation-scope re-badge (owner ask 2026-07-12): an unsupported passage
    # whose citation is only a methods/concept/related pointer inside the
    # authors' own text gets an indigo badge instead of red — the red card
    # answers "does the source prove the passage?", a question the author
    # never asked. DISPLAY ONLY: the verdict field is unchanged and the card
    # still appears under the Unsupported filter.
    cscope = c.get("citation_scope") or {}
    if verdict == "unsupported" and cscope.get("scope") in ("methods", "concept", "related"):
        badge = f"SCOPED CITATION ({cscope['scope'].upper()})"
        badge_cls = "unsupported scoped"
    method = c.get("method", "")
    cosine = c.get("cosine")
    meta = []
    if cosine is not None:
        meta.append(f"cosine {cosine}")
    if method:
        meta.append(method)
    meta_str = " · ".join(meta)

    # One block per cited paper (evidences); fall back to the single evidence for old analyses.
    evidences = c.get("evidences")
    if evidences is None:
        evidences = [ev] if ev else []

    blocks = ""
    secondhand_hits = []      # (source_title, matched citation string) on SUPPORTING rows
    disagree_rows = []        # (source_title, reason) — co-cited evidence judged contradicting
    for e in evidences:
        sentence = e.get("sentence") or ""
        fulltext = e.get("via") == "llm_fulltext"   # sentence found by LLM full-text read, not cosine
        if e.get("supported"):
            chip = ('<span class="srcchip ok" title="this source\'s own passage was '
                    'judged to support the claim">supports</span>')
            m = _SECONDHAND_RE.search(sentence)
            if m:
                secondhand_hits.append((e.get("source_title") or "", m.group(0).strip()))
        elif not sentence:
            chip = ('<span class="srcchip no" title="nothing in this source was even '
                    'close — possibly the wrong source for this claim">no relevant '
                    'passage found</span>')
        elif fulltext:
            chip = ('<span class="srcchip no" title="the best passage a full-text read '
                    'found — judged NOT to support the claim on its own; shown so you '
                    'can see what was checked">relevant — not enough alone</span>')
        else:
            chip = ('<span class="srcchip no" title="the closest passage by similarity — '
                    'judged NOT to support the claim; shown so you can see what was '
                    'checked">closest — not supporting</span>')
        if not e.get("supported") and _DISAGREE_RE.search(e.get("reason") or ""):
            disagree_rows.append((e.get("source_title") or "", e.get("reason") or ""))
        if sentence:
            actions = _source_actions(fname_map, e.get("paper_id"), e.get("page"),
                                      sentence, e.get("snippet", ""), source_texts)
            body = (f'{_clamped_quote(sentence)}'
                    f'<button class="copy" onclick="copyText(this)" data-quote="{_esc(sentence)}">Copy</button>'
                    f'{actions}')
            # Context on demand for EVERY row (owner walkthrough item 11: a bare
            # 7-word quote is meaningless without its surroundings): the judged
            # window — the quoted sentence ±1, or every extracted sentence.
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
    # Missing cited sources (item 16, t14): a multi-citation claim keeps a row per
    # cited file that isn't in the sources folder, so the reader sees the source
    # was NOT checked (rather than the component silently looking unfound).
    for mm in (c.get("missing_markers") or []):
        blocks += f"""
          <div class="evidence">
            <div class="ev-label"><span class="srcchip no" title="the file for this citation is not in the sources folder, so it could not be checked">source file missing</span> [[{_esc(mm.get('key') or '')}]]</div>
            <div class="reason">The cited file <code>{_esc(mm.get('filename') or '')}</code> is not in the sources folder — this citation was not verified. Add the file and re-run to check it.</div>
          </div>"""

    if not blocks:
        if verdict == "own":
            blocks = ('<div class="evidence own-note">No citation — the tool treats this '
                      'as your own idea, argument, or transition. Nothing was checked; '
                      'add a [[key]] marker if it should be grounded in a source.</div>')
        else:
            blocks = f'<div class="evidence reason">⚠ {_esc(c.get("reason",""))}</div>'
            # Even with no evidence rows at all, the cited sources must stay
            # openable from the card (friend feedback 2026-07-19).
            for pid in (c.get("paper_ids") or []):
                acts = _source_actions(fname_map, pid, None, "", "", source_texts)
                if acts:
                    title = (paper_meta.get(pid) or {}).get("title") or fname_map.get(pid, pid or "")
                    blocks += (f'<div class="evidence"><div class="ev-label">{_esc(title)} '
                               f'{_paper_link(paper_meta, pid, title)}</div>{acts}</div>')

    # Simple/expert split: `key_note` renders ALWAYS (even in simple mode) —
    # the unsupported reason is the evaluation itself, never clutter.
    # Everything appended to `note` is advanced detail, hidden in simple mode
    # behind the card's "details" button (and fully visible in expert mode).
    # The amber covering-gap line lives in `note` (owner ruling 2026-07-14:
    # in simple mode the NOT PROVEN AS WRITTEN badge carries the signal; the
    # line naming the unproven part is one click away) — the class is mixed
    # by measurement (retrieval misses vs real gaps, e.g. the Eskelson
    # literacy amber where the proof existed verbatim), so the always-on
    # line overstated certainty.
    key_note = ""
    note = ""
    if verdict == "supported" and method == "tail_rescue":
        # rescued via the combined judge: no single source sufficed for the tail
        together = (" by the cited sources <b>together</b>"
                    if not any(e.get("supported") for e in evidences) else "")
        note = (f'<div class="leadin-note">✓ The <b>cited assertion</b> is supported{together}. '
                'The sentences before it are your own lead-in — not covered by the citation, '
                'nothing was checked.</div>')
    elif verdict == "supported" and method in ("combined", "combined_fulltext"):
        note = ('<div class="combined-note">✓ Supported by the cited sources <b>together</b> — '
                'no single source states it alone.</div>')
    elif verdict == "supported" and method == "component_rescue":
        note = ('<div class="combined-note">✓ Supported <b>piece by piece</b>: no single '
                'passage states everything, so each part of the claim was verified '
                'separately in the source and the combination re-judged. The quoted '
                'evidence below is per-part.</div>')
    elif verdict == "unsupported" and c.get("reason"):
        key_note = f'<div class="unsupp-note">✗ Not supported: {_esc(c["reason"])}</div>'

    # Multi-citation "supported" explainer (owner walkthrough item 10, t8/t14/t22:
    # 'N sources cited, one supports, the others show cryptic non-supporting rows
    # — why is this supported?'): state the OR semantics right on the card.
    if verdict == "supported" and method not in ("combined", "combined_fulltext",
                                                 "component_rescue"):
        winners = [e for e in evidences if e.get("supported")]
        losers = [e for e in evidences if not e.get("supported")]
        if winners and losers:
            wt = ", ".join(_esc(w.get("source_title") or "?") for w in winners)
            note += (f'<div class="multisource-note">ℹ Supported via <b>{wt}</b>. '
                     f'A claim counts as supported when at least ONE cited source backs '
                     f'it; the other cited source{"s" if len(losers) != 1 else ""} did not '
                     f'independently support it — their best-found passage is shown below '
                     f'for reference.</div>')
    # Covering-set evidence (round-1 loop fix, 2026-07-10): which shown sentence
    # proves which part of the claim, and — always visible, amber — the parts NO
    # shown sentence proves. Display-only; the verdict above is never touched.
    cov = c.get("covering") or {}
    if verdict == "supported" and (cov.get("covered") or cov.get("uncovered")):
        common_set = set(cov.get("common_knowledge") or [])
        unc_all = [u for u in (cov.get("uncovered") or []) if u]
        common = [u for u in unc_all if u in common_set]
        unc = [u for u in unc_all if u not in common_set]
        if unc:
            part_word = "this part" if len(unc) == 1 else "these parts"
            note += ('<div class="covset-miss">⚠ No evidence shown for: <b>'
                     + '</b> · <b>'.join(_esc(u) for u in unc)
                     + f'</b>. The judge accepted the claim, but the displayed '
                       f'sentences don\'t prove {part_word} — open the source '
                       f'and check it yourself.</div>')
        if common:
            # Owner ruling (round 4, t1): everyday commonplaces need no
            # citation — grey and quiet, but never hidden.
            note += ('<div class="covset-common">◦ Not checked — commonly known: <b>'
                     + '</b> · <b>'.join(_esc(u) for u in common)
                     + '</b>. The tool judged this an everyday fact that needs '
                       'no citation; if it matters to your argument, cite it anyway.</div>')
        rows = ""
        parts_seen = []
        # Group ADJACENT parts proven by the same sentence (owner, r2 t5): the
        # parts are listed together and the sentence quoted ONCE beneath the
        # group. Non-adjacent repeats stay separate rows (document order wins),
        # and every (part, sentence) pick renders — a part proven by several
        # sentences shows them all.
        groups = []
        for ce in (cov.get("covered") or []):
            sentence = ce.get("sentence") or ""
            if not sentence:
                continue
            part = ce.get("component") or ""
            if part not in parts_seen:
                parts_seen.append(part)
            if groups and groups[-1]["sentence"] == sentence:
                if part not in groups[-1]["parts"]:
                    groups[-1]["parts"].append(part)
            else:
                groups.append({"sentence": sentence, "parts": [part],
                               "paper_id": ce.get("paper_id"), "page": ce.get("page"),
                               "snippet": ce.get("snippet", ""),
                               "source_title": ce.get("source_title") or ""})
        for g in groups:
            actions = _source_actions(fname_map, g["paper_id"], g["page"],
                                      g["sentence"], g["snippet"], source_texts)
            parts_html = " · ".join(f'✓ {_esc(p)}' for p in g["parts"])
            rows += (f'<div class="covset-row"><div class="covset-part">'
                     f'{parts_html} '
                     f'<span class="covset-src">{_esc(g["source_title"])}</span></div>'
                     f'{_clamped_quote(g["sentence"])}'
                     f'<button class="copy" onclick="copyText(this)" '
                     f'data-quote="{_esc(g["sentence"])}">Copy</button>{actions}</div>')
        if rows:
            # "Read it in context" (owner request 2026-07-11): the used
            # sentences plus all original text between them, per source — so
            # the reader sees how the quoted pieces fit together.
            for sp in (cov.get("spans") or []):
                span_text = sp.get("text") or ""
                if not span_text:
                    continue
                rows += (f'<details class="covspan"><summary>Read it in context — '
                         f'the {sp.get("n_used", "?")} used sentence'
                         f'{"s" if sp.get("n_used") != 1 else ""} with the original '
                         f'text between them ({_esc(sp.get("source_title") or "")})'
                         f'</summary><div class="judged-text">{_esc(span_text)}</div>'
                         f'</details>')
            n_parts = len(parts_seen)
            rows += ('<div class="covset-foot">Assembled after the verdict: the tool '
                     're-read the source\'s best passages and mapped each part of '
                     'the claim to the sentence that proves it.</div>')
            note += (f'<details class="covering"><summary>Evidence coverage — which '
                     f'sentence proves which part ({n_parts} part'
                     f'{"s" if n_parts != 1 else ""} with shown proof'
                     + (f', {len(unc)} without' if unc else '')
                     + f')</summary>{rows}</details>')

    # A split judge vote (2-1) is a borderline call, not a confident rejection —
    # say so, so the human reviews it instead of trusting the verdict blindly.
    split = ([c.get("votes")] + [e.get("votes") for e in evidences]).count("2-1") > 0
    if verdict == "unsupported" and split:
        note += ('<div class="borderline-note">⚖ Borderline: the judges split 2–1 on this '
                 'one — read the evidence and decide yourself.</div>')

    rescue = c.get("tail_rescue") or {}
    if verdict == "supported" and rescue.get("supported"):
        chip = '<span class="leadin-chip">lead-in</span>'
        claim_html = (f'<span class="leadin">{_esc(rescue["lead_in"])}</span> '
                      f'{_esc(rescue["tail"])}')
    else:
        chip = ""
        claim_html = _esc(c["text"])

    conf = _confidence(c)
    conf_cls = ""
    if conf:
        conf_cls = f" conf-{conf[0]}"      # feeds the confidence filter chips
        chip += (f'<span class="confchip {conf[0]}" title="{_esc(conf[1])}">'
                 f'{conf[0]} confidence</span>')

    # Second opinion (--second-opinion): a different model re-read the same
    # evidence. Disagreement is a FLAG, never a veto — the verdict stands, the
    # chip + note send the human to the evidence.
    so = c.get("second_opinion") or {}
    if so.get("agrees") is False:
        so_dir = ("would call this SUPPORTED — the judge may have been too strict"
                  if so.get("verdict") == "supported"
                  else "would call this UNSUPPORTED — a false-positive risk")
        chip += (f'<span class="sochip" title="{_esc(so.get("model") or "second model")} '
                 f'read the same evidence and disagrees">⚠ 2nd opinion disagrees</span>')
        note += (f'<div class="so-note">⚠ Second opinion ({_esc(so.get("model") or "")}): '
                 f'{so_dir}. &ldquo;{_esc(so.get("reason") or "")}&rdquo; '
                 f'The verdict above is unchanged — read the evidence and decide.</div>')
    # Deep check (deep_check.py, TESTING aid): a stronger model re-read the
    # claim WITH source context and always comments — agree or disagree. The
    # commentary + its anchoring quote render on the card so a human can
    # review fast. NEVER a veto; the verdict above stands.
    dc = c.get("deep_check") or {}
    if dc:
        agrees = bool(dc.get("agrees"))
        chip += (f'<span class="dcchip {"agree" if agrees else "flag"}" '
                 f'title="deep check: {_esc(dc.get("model") or "a stronger model")} re-read '
                 f'the claim with source context ({_esc(dc.get("confidence") or "?")} confidence)">'
                 f'{"✓ deep check agrees" if agrees else "⚠ deep check disagrees"}</span>')
        q = (f' Anchoring quote: &ldquo;{_esc(dc.get("quote"))}&rdquo;'
             if dc.get("quote") else "")
        better = (f'<div class="dc-better">Suggested better evidence: '
                  f'&ldquo;{_esc(dc.get("better_sentence"))}&rdquo;</div>'
                  if dc.get("better_sentence") else "")
        note += (f'<div class="dc-note{"" if agrees else " flag"}">🔎 Deep check '
                 f'({_esc(dc.get("model") or "")}, {_esc(dc.get("confidence") or "?")} '
                 f'confidence; testing aid, never a veto): '
                 f'{_esc(dc.get("commentary") or "")}{q}{better}</div>')
    # Arbiter (--arbiter): a strong model re-read ONLY this flagged claim with
    # large source context. NEVER a veto — every quote shown here passed the
    # deterministic verbatim gate against the cited sources' text.
    ab = c.get("arbiter") or {}
    if ab.get("action"):
        ab_model = _esc(ab.get("model") or "arbiter")
        quotes = "".join(f'<div class="ab-quote">&ldquo;{_esc(q)}&rdquo;</div>'
                         for q in (ab.get("proofs") or []))
        resd = (c.get("covering") or {}).get("arbiter_resolution")
        if verdict == "supported" and c.get("proof_state") == "arbiter_resolved" and resd:
            # Amber resolution (owner 2026-07-14): the badge reverted to plain
            # SUPPORTED because the arbiter produced verbatim-verified proof
            # for the flagged gap (action "supported" OR
            # "wrong_or_insufficient_evidence" — both mean the source proves
            # it; t5/Eskelson validated the latter). The card tells its history.
            res_quotes = "".join(f'<div class="ab-quote">&ldquo;{_esc(q)}&rdquo;</div>'
                                 for q in (resd.get("proofs") or []))
            chip += ('<span class="rescuechip" title="this card was flagged '
                     '&quot;not proven as written&quot;; the arbiter re-read the '
                     'full source and found verbatim-verified proof, so the flag '
                     'was cleared — the verdict itself never changed">'
                     '⛑ gap closed by arbiter</span>')
            note += (f'<div class="rescue-note">⛑ This card was flagged '
                     f'&ldquo;not proven as written&rdquo; because the displayed '
                     f'sentences did not prove every part. The arbiter '
                     f'({ab_model}) re-read the full source and found proof, '
                     f'verified verbatim:{res_quotes}'
                     f'{_esc(ab.get("why") or "")} The flag was cleared; the '
                     f'verdict was always supported.</div>')
        elif verdict == "unsupported" and ab["action"] == "wrong_or_insufficient_evidence" \
                and ab.get("proofs"):
            chip += ('<span class="abchip fetch" title="the arbiter found verbatim '
                     'sentences in the cited source that may prove this claim">'
                     '🔷 proof may exist</span>')
            note += (f'<div class="ab-note">🔷 Arbiter ({ab_model}): the cited source '
                     f'may contain the proof the judge never saw — these sentences are '
                     f'verified verbatim from the source:{quotes}'
                     f'{_esc(ab.get("why") or "")} The verdict above is unchanged — '
                     f'read them and decide.</div>')
        elif ab["action"] == "add_citation_or_rewrite":
            miss = _esc(ab.get("missing_subclaim") or "a component")
            rewrite = (f'<div class="ab-rewrite">Suggested rewrite: '
                       f'&ldquo;{_esc(ab.get("rewrite_suggestion"))}&rdquo;</div>'
                       if ab.get("rewrite_suggestion") else "")
            chip += ('<span class="abchip authorfix" title="the arbiter read the '
                     'source and says a component needs a new citation or a rewrite">'
                     '✍ arbiter: author fix?</span>')
            note += (f'<div class="ab-note">✍ Arbiter ({ab_model}): not provable from '
                     f'the cited source(s) — &ldquo;{miss}&rdquo;. '
                     f'{_esc(ab.get("why") or "")}{rewrite}'
                     + (quotes and f'<div>Provable parts, verified verbatim:</div>{quotes}' or "")
                     + '</div>')
        elif ab["action"] == "wrong_or_insufficient_evidence":
            chip += ('<span class="abchip fetch" title="the arbiter says better '
                     'evidence exists in the source than what is shown">'
                     '🔷 better proof exists</span>')
            note += (f'<div class="ab-note">🔷 Arbiter ({ab_model}): the shown '
                     f'sentences don\'t fully prove the claim, but these verified '
                     f'source sentences would:{quotes}{_esc(ab.get("why") or "")}</div>')
        elif verdict == "unsupported":  # action == supported on an unsupported claim
            chip += ('<span class="abchip fetch" title="the arbiter read the source '
                     'and thinks the shown evidence already proves the claim">'
                     '🔷 arbiter disagrees: looks proven</span>')
            note += (f'<div class="ab-note">🔷 Arbiter ({ab_model}): the shown evidence '
                     f'already appears to prove this claim — the judge may have been '
                     f'too strict. {_esc(ab.get("why") or "")}{quotes} '
                     f'The verdict above is unchanged — read and decide.</div>')
        else:  # action == supported on a flagged supported claim (not resolved)
            note += (f'<div class="ab-note ok">Arbiter ({ab_model}): read the flagged '
                     f'gaps against the source — they look minor; the shown evidence '
                     f'holds. {_esc(ab.get("why") or "")}</div>')
        if ab.get("conflict"):
            cf = ab["conflict"]
            chip += ('<span class="abchip conflict" title="a source sentence may '
                     'CONTRADICT this claim — read it">⚡ conflicting evidence?</span>')
            note += (f'<div class="ab-note conflict">⚡ Possible conflicting evidence '
                     f'(verified verbatim): &ldquo;{_esc(cf.get("sentence") or "")}&rdquo; '
                     f'— {_esc(cf.get("why") or "")}</div>')

    # Partial support (multi-citation claims): the verdict is supported, but the
    # component-complete combined judge found a specific component in none of the
    # cited sources. A FLAG, never a veto — the verdict stands; the chip + note
    # send the human to check which part is missing.
    # Proof-state card class feeds the "Partly proven" filter chip; the badge
    # variant itself is computed above (badge_cls).
    partly_cls = (" partlyproven" if verdict == "supported"
                  and c.get("proof_state") == "partial" else "")
    # Citation-scope card class feeds the "Scoped citation" filter chip; the
    # badge/chip/note themselves are computed above.
    scoped_cls = (" scoped" if verdict == "unsupported"
                  and cscope.get("scope") in ("methods", "concept", "related") else "")
    ps = c.get("partial_support") or {}
    partial_cls = ""
    if ps:
        partial_cls = " partial"
        esc_note = (" (Checked against the sources' full decomposed claims, not just "
                    "the matched passages.)" if ps.get("escalated") else "")
        chip += ('<span class="partialchip" title="the cited sources back only part of '
                 'this claim — a component was not found">partial support?</span>')
        note += (f'<div class="partial-note">◑ Partial support: the sources back the claim '
                 f'in general, but a specific component appears in none of them — '
                 f'&ldquo;{_esc(ps.get("reason") or "")}&rdquo;{esc_note} '
                 f'The verdict above is unchanged — read the evidence and confirm that part.</div>')
        # Missing-component hunt (item 9): where ELSE the missing part might be.
        for h in (ps.get("component_hunt") or []):
            comp = _esc(h.get("component") or "")
            found = h.get("found_in") or []
            if found:
                ft = ", ".join(f'<b>{_esc(f.get("source_title") or f.get("key") or "?")}</b>'
                               for f in found)
                note += (f'<div class="hunt-note">🔎 The missing part (&ldquo;{comp}&rdquo;) '
                         f'may be covered by {ft} — a full-text probe of your other '
                         f'downloaded sources found it there. Consider citing that source '
                         f'for this part.</div>')
            else:
                note += (f'<div class="hunt-note">🔎 A full-text search of your other '
                         f'downloaded sources did not find &ldquo;{comp}&rdquo; either — '
                         f'source that part elsewhere, or it may simply be wrong.</div>')

    # Date inference (P3, owner ruling 2026-07-11): the judge resolved a relative
    # time reference ("this year") against the article's publication date. The
    # ruling requires a VISIBLE caveat — a grey chip, never a verdict change.
    if c.get("date_inferred"):
        chip += ('<span class="datechip" title="a relative time reference in the '
                 'evidence (e.g. “this year”) was resolved against the '
                 'article’s publication date — check the DATE-INFERRED reason '
                 'on the evidence">date inferred from article date</span>')

    if c.get("byline_inferred"):
        chip += ('<span class="datechip" title="an attribution in this claim ('
                 'who wrote/reviewed the piece) was resolved against the '
                 'article’s author byline — check the BYLINE-INFERRED reason '
                 'on the evidence">attribution from article byline</span>')

    # Over-citation (ALCE precision): the union of the OTHER cited sources fully
    # covers the claim and this source doesn't back it on its own — the mildest
    # nudge: the citation may belong on a different sentence, or can be trimmed.
    oc = c.get("over_citation") or {}
    overcite_cls = ""
    if oc.get("sources"):
        overcite_cls = " overcite"
        oc_titles = ", ".join(_esc(s.get("source_title") or s.get("paper_id") or "?")
                              for s in oc["sources"])
        chip += ('<span class="overchip" title="the other cited sources already cover '
                 'this claim — this citation adds nothing detectable">over-cited?</span>')
        note += (f'<div class="overcite-note">◔ Possible over-citation: {oc_titles} — '
                 f'the remaining cited sources already cover the claim, and this one '
                 f'does not back it on its own. Check whether the citation belongs on a '
                 f'different sentence, or trim it. Not a verdict — nothing is wrong '
                 f'with the claim itself.</div>')

    # Component check on an UNSUPPORTED claim (item 13's no-flip case): which
    # parts ARE individually in the sources, with the found evidence quoted.
    # Symmetric component display on unsupported cards (owner ruling
    # 2026-07-11, P2): a compound claim that fails must still SHOW which
    # parts WERE proven (with their sentences) and list every part that was
    # not — the found evidence must not vanish into a flat "unsupported"
    # (WiCE t13: the 1988-Wales-cap proof was found, then hidden). Display
    # only; the verdict is untouched.
    cc = c.get("component_check") or {}
    if verdict == "unsupported" and cc:
        found, missing = cc.get("found") or [], cc.get("missing") or []
        ev_by_comp = {}
        for x in (cc.get("evidence") or []):
            if x.get("sentence") and x.get("component") not in ev_by_comp:
                ev_by_comp[x.get("component")] = x
        if found:
            rows = ""
            for p in found:
                x = ev_by_comp.get(p)
                proof = (f' — &ldquo;{_esc(x["sentence"])}&rdquo; '
                         f'<i>({_esc(x.get("source_title") or "")})</i>'
                         if x else "")
                rows += f'<div class="judged-text">✓ <b>{_esc(p)}</b>{proof}</div>'
            note += (f'<div class="compcheck-note">◐ Partly proven despite the '
                     f'verdict: these parts of the claim WERE found in the cited '
                     f'source(s):{rows}'
                     + ("" if missing else
                        '<div class="compcheck-tail">But the judges did not accept '
                        'that these pieces together prove the whole claim — read '
                        'the evidence and decide yourself.</div>')
                     + '</div>')
        if missing:
            ml = "; ".join(f'&ldquo;{_esc(x)}&rdquo;' for x in missing)
            note += (f'<div class="compcheck-missing">✗ Not found in the cited '
                     f'sources: {ml} — support these parts elsewhere, or they may '
                     f'be wrong.</div>')

    # Secondhand evidence (item 12, t9): the supporting sentence itself cites
    # another work — the author may be citing a middleman.
    if verdict == "supported" and secondhand_hits:
        cites = "; ".join(f'&ldquo;{_esc(m)}&rdquo; in {_esc(t or "?")}'
                          for t, m in secondhand_hits[:2])
        chip += ('<span class="shchip" title="the supporting sentence itself cites '
                 'another work — consider citing the original">secondhand evidence?</span>')
        note += (f'<div class="sh-note">↩ The supporting sentence itself carries a '
                 f'citation ({cites}) — the cited source may be relaying someone '
                 f'else\'s finding. Consider citing the original work directly.</div>')

    # Cited-sources disagreement (item 14, t8): a co-cited source's best passage
    # was judged to CONTRADICT the claim, not merely miss it.
    if verdict == "supported" and disagree_rows:
        dt = "; ".join(f'<b>{_esc(t or "?")}</b>: &ldquo;{_esc(r)}&rdquo;'
                       for t, r in disagree_rows[:2])
        chip += ('<span class="disagreechip" title="a co-cited source\'s evidence was '
                 'judged to contradict this claim">sources may disagree?</span>')
        note += (f'<div class="disagree-note">⇄ A co-cited source\'s best passage argues '
                 f'the other way — {dt}. The verdict rests on the supporting source; '
                 f'read both and decide whether your claim should acknowledge the '
                 f'disagreement.</div>')

    of = c.get("owner_flag") or {}
    if of:
        of_note = f' — “{_esc(of["note"])}”' if of.get("note") else ""
        chip += (f'<span class="ownerchip" title="you marked this verdict wrong '
                 f'({_esc(of.get("timestamp") or "")}){of_note}">author disputed</span>')

    # Citation-scope note: explain WHY the card isn't red — the passage is the
    # authors' own text and the citation backs only the named fragment.
    if verdict == "unsupported" and cscope.get("scope") in ("methods", "concept", "related"):
        sa = cscope.get("scoped_assertion") or ""
        chip += (f'<span class="scopechip" title="{_esc(cscope.get("reason") or "")}">'
                 f'✎ {_esc(cscope["scope"])} citation</span>')
        note += ('<div class="scope-note">✎ This passage mainly describes the '
                 'authors’ own work; the citation was read as a '
                 f'<b>{_esc(cscope["scope"])}</b> pointer'
                 + (f' backing only: &ldquo;{_esc(sa)}&rdquo;' if sa else "")
                 + '. The cited source was never asserted to prove the whole '
                   'passage — the verdict below is kept for the record, '
                   'but read it as “not applicable” rather than an '
                   'authoring error.</div>')

    # Arbiter rescue (§6.6): the arbiter located verbatim-verified proof and
    # the PRIMARY judge unanimously confirmed — say so, the card's history
    # matters (it was red before this run).
    if verdict == "supported" and method == "arbiter_rescue":
        chip += ('<span class="rescuechip" title="originally judged unsupported; the '
                 'arbiter found the proof sentences (verified verbatim against the '
                 'source) and the primary judge re-judged them unanimously positive">'
                 '⛑ arbiter rescue</span>')
        note += ('<div class="rescue-note">⛑ This claim was first judged unsupported '
                 'because the shown evidence missed the proving sentences. The arbiter '
                 'located them (each verified verbatim against the source) and the '
                 'primary judge confirmed unanimously — the sentences above are the '
                 'arbiter-fetched proof.</div>')

    # Own-claim kind (own-split pass): "fact" = an uncited checkable assertion —
    # an amber nudge to add a citation, NOT a verdict (nothing was checked).
    ok = c.get("own_kind") or {}
    cite_cls = ""
    if verdict == "own" and ok.get("kind"):
        if ok["kind"] == "fact":
            cite_cls = " citeneeded"
            # Prominent on purpose (owner, r3 t6: the pale chip was missed) —
            # an own+fact card's whole point is this nudge.
            chip += ('<span class="citechip loud" title="this uncited passage asserts a '
                     'checkable fact">📎 citation needed?</span>')
            note += (f'<div class="cite-note">📎 This uncited passage asserts a checkable '
                     f'fact — consider adding a [[key]] citation. '
                     f'&ldquo;{_esc(ok.get("reason") or "")}&rdquo; '
                     f'Nothing was checked against any source; this is a prompt, not a verdict.</div>')
        else:
            chip += (f'<span class="kindchip" title="{_esc(ok.get("reason") or "")}">'
                     f'{_esc(ok["kind"])}</span>')

    # Diff vs the previous run (incremental re-verification): flag edited/new
    # claims so the reader can jump straight to what they changed.
    prev = c.get("prev") or {}
    changed_cls = ""
    if prev.get("changed"):
        changed_cls = " changed"
        chip += '<span class="changed-chip" title="edited since the last run">✎ changed</span>'
        if prev.get("text"):
            was = f'was {_esc(prev.get("verdict") or "?")}' if prev.get("verdict") else "previous version"
            note += (f'<details class="changed-note"><summary>✎ Edited since the last run '
                     f'({was})</summary><div class="prev-text">{_esc(prev["text"])}</div></details>')
        else:
            note += '<div class="changed-note">✎ New since the last run.</div>'

    # Review triage: the owner marks WHY a card needs work (wrong source /
    # rewrite / verdict wrong) + a note; state lives in localStorage (the viewer
    # is server-free) and exports via the review bar. Wired up by initTriage().
    triage = (f'<div class="triage" data-id="{c["id"]}" onclick="event.stopPropagation()">'
              f'<button class="cbtn" title="mark this card as reviewed by you — checked with no repair marks means \'looked, it is fine\'">✓ checked</button>'
              f'<span class="tlabel">mark for repair:</span>'
              f'<button class="tbtn" data-mark="wrong_source" title="the claim is fine but the cited source does not back it — cite a different one">wrong source</button>'
              f'<button class="tbtn" data-mark="rewrite" title="the text overclaims — rewrite it to match the evidence">rewrite text</button>'
              f'<button class="tbtn" data-mark="more_support" title="the claim may be right but the shown evidence doesn\'t prove it — first hunt the source(s) for stronger supporting sentences; only rewrite if no proof exists">find proof / rewrite</button>'
              f'<button class="tbtn" data-mark="verdict_wrong" title="I disagree with the tool\'s verdict — feedback, no text change">verdict wrong</button>'
              f'<button class="tbtn" data-mark="needs_citation" title="this passage should cite a source — find one and add a [[key]] citation">needs citation</button>'
              f'<button class="tbtn" data-mark="other" title="anything else — write it in the note; the fixer surfaces it to you instead of acting on its own">other</button>'
              f'<textarea class="tnote" placeholder="optional note for the fixer" rows="2"></textarea>'
              f'</div>')

    return f"""
      <div class="card {'scopedcite' if scoped_cls else verdict}{changed_cls}{cite_cls}{partial_cls}{partly_cls}{scoped_cls}{overcite_cls}{conf_cls}" id="card-{c['id']}" data-text="text-{c['id']}" onclick="brush('{c['id']}', 'card')">
        <div class="card-head"><span class="head-left"><span class="badge {badge_cls}">{badge}</span><span class="claimno">{_esc(c['id'])}</span>{chip}</span><span class="meta">{_esc(meta_str)}</span></div>
        <div class="card-claim">{claim_html}</div>
        {key_note}
        <div class="adv">{note}</div>
        {blocks}
        <div class="adv">{_fix_section(c, fix_cmd_base)}{triage}</div>
        <button class="morebtn" onclick="toggleMore(event, this)">▸ details &amp; review</button>
      </div>"""


def _omitted_card(o: Dict[str, Any], fname_map: Dict[str, str], source_texts: Dict[str, str],
                  paper_meta: Dict[str, Dict[str, str]]) -> str:
    ev = (o.get("evidence") or [])
    quote_html = f'<blockquote>{_esc(ev[0])}</blockquote>' if ev else ""
    sentence = ev[0] if ev else o.get("text", "")
    actions_html = _source_actions(fname_map, o.get("paper_id"), o.get("page"),
                                    sentence, o.get("snippet", ""), source_texts)
    plink = _paper_link(paper_meta, o.get("paper_id"), o.get("source_title") or "")
    rel = o.get("relevance")
    rel_str = f" · relevance {rel:.2f}" if rel is not None else ""
    meta = _esc(o.get('source_title') or '') + rel_str
    return f"""
      <div class="card omitted">
        <div class="card-head"><span class="badge omitted">OMITTED</span><span class="meta">{meta} {plink}</span></div>
        <div class="card-claim">{_esc(o['text'])}</div>
        {quote_html}
        {actions_html}
      </div>"""


def _review_data(analysis: Dict[str, Any], claims: list, out_dir: str) -> Dict[str, Any]:
    """The compact per-claim record embedded for the review/export JS — everything
    an external fixing agent (Claude Code, any chat LLM) needs, self-contained."""
    meta = analysis.get("metadata", {})

    def slim(c):
        conf = _confidence(c)
        so = c.get("second_opinion") or {}
        ok = c.get("own_kind") or {}
        cs = c.get("citation_scope") or {}
        return {"id": c["id"], "text": c.get("text"), "markers": c.get("markers", []),
                "own_kind": ({"kind": ok.get("kind"), "reason": ok.get("reason")}
                             if ok.get("kind") else None),
                "citation_scope": ({"scope": cs.get("scope"),
                                    "scoped_assertion": cs.get("scoped_assertion"),
                                    "reason": cs.get("reason")}
                                   if cs.get("scope") in ("methods", "concept", "related")
                                   else None),
                "verdict": c.get("verdict"), "method": c.get("method"),
                "proof_state": c.get("proof_state"),
                "reason": c.get("reason"),
                "confidence": conf[0] if conf else None,
                "second_opinion": ({"model": so.get("model"), "verdict": so.get("verdict"),
                                    "reason": so.get("reason")}
                                   if so.get("agrees") is False else None),
                "partial_support": ({"reason": (c.get("partial_support") or {}).get("reason"),
                                     "component_hunt": (c.get("partial_support") or {}).get("component_hunt")}
                                    if c.get("partial_support") else None),
                "arbiter": ({"model": (c.get("arbiter") or {}).get("model"),
                             "trigger": (c.get("arbiter") or {}).get("trigger"),
                             "action": (c.get("arbiter") or {}).get("action"),
                             "missing_subclaim": (c.get("arbiter") or {}).get("missing_subclaim"),
                             "proofs": (c.get("arbiter") or {}).get("proofs") or [],
                             "conflict": (c.get("arbiter") or {}).get("conflict")}
                            if (c.get("arbiter") or {}).get("action") else None),
                "component_check": c.get("component_check") or None,
                "covering": ({"uncovered": (c.get("covering") or {}).get("uncovered") or [],
                              "common_knowledge": (c.get("covering") or {}).get("common_knowledge") or []}
                             if (c.get("covering") or {}).get("uncovered") else None),
                "over_citation": ({"sources": [s.get("source_title") or s.get("paper_id")
                                               for s in (c.get("over_citation") or {}).get("sources", [])]}
                                  if c.get("over_citation") else None),
                "evidences": [{"source_title": e.get("source_title"),
                               "sentence": e.get("sentence"),
                               "supported": bool(e.get("supported"))}
                              for e in (c.get("evidences") or []) if e],
                "alternatives": c.get("alternatives") or []}

    run_dir = meta.get("output_dir") or out_dir
    # Filesystem-safe run name for the review filename (owner walkthrough item 3:
    # Downloads full of identical `review.json`s). The ARTICLE names the file
    # (owner, r3): the text's frontmatter `title:`, else the text file's stem,
    # else the old output-dir basename — loop rounds all share basename "app".
    raw_name = ""
    text_file = meta.get("text_file", "")
    if text_file:
        try:
            with open(text_file, encoding="utf-8") as f:
                raw_name, _ = text_decomposer.strip_frontmatter(f.read(8192))
        except OSError:
            pass
        stem = os.path.splitext(os.path.basename(text_file))[0]
        raw_name = raw_name or stem
    raw_name = raw_name or os.path.basename(os.path.normpath(run_dir)) or "run"
    run_name = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_name).strip("-")[:60].strip("-")
    return {"run": {"text_file": meta.get("text_file", ""),
                    "run_name": run_name,
                    "sources_dir": meta.get("sources_dir", ""),
                    "output_dir": meta.get("output_dir") or out_dir,
                    # the project dir (refs + sources_manifest.json) — where a
                    # Claude Science export gets merged back in
                    "project_dir": os.path.dirname(meta.get("text_file", "")) or "",
                    "model": meta.get("model", ""),
                    "timestamp": meta.get("timestamp", "")},
            "claims": [slim(c) for c in claims]}


# The review-loop client script. A plain (non-f) string so none of its braces
# need doubling inside the page template; RUN_ID and REVIEW_DATA are emitted as
# constants just above it. Marks persist in localStorage per run; export is a
# Blob download / clipboard copy — no server, works from file://.
REVIEW_JS = r"""
const REVIEW_KEY = 'ptreview:' + RUN_ID;
// Clipboard that also works from file:// and in Firefox (no reliance on the
// global `event`, with an execCommand fallback when navigator.clipboard is
// unavailable or blocked — the documented open-as-file:// path). 2026-07-20.
function cgCopy(text, btn, label) {
  function flip() {
    if (!btn) return;
    var o = btn.textContent; btn.textContent = label || 'Copied';
    setTimeout(function(){ btn.textContent = o; }, 1300);
  }
  function fallback() {
    try {
      var ta = document.createElement('textarea');
      ta.value = text; ta.style.position = 'fixed'; ta.style.top = '-1000px';
      document.body.appendChild(ta); ta.focus(); ta.select();
      document.execCommand('copy'); document.body.removeChild(ta); flip();
    } catch (e) { window.prompt('Copy manually (Ctrl/Cmd+C):', text); }
  }
  try {
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(flip, fallback);
    } else { fallback(); }
  } catch (e) { fallback(); }
}
const MARK_NAMES = {wrong_source: 'needs a different source',
                    rewrite: 'text needs rewriting',
                    more_support: 'find more proof, else rewrite',
                    verdict_wrong: 'verdict is wrong',
                    needs_citation: 'needs a citation',
                    other: 'other (see note)'};
let review = {};
try { review = JSON.parse(localStorage.getItem(REVIEW_KEY) || '{}'); } catch (e) { review = {}; }

function isMarked(r) { return r && ((r.marks && r.marks.length) || (r.note && r.note.trim())); }
function markCount() { return Object.values(review).filter(isMarked).length; }
function saveReview() {
  localStorage.setItem(REVIEW_KEY, JSON.stringify(review));
  updateReviewBar();
}
function checkedCount() { return Object.values(review).filter(function(r) { return r && r.checked; }).length; }
function updateReviewBar() {
  const n = markCount();
  const el = document.getElementById('revCount');
  const total = document.querySelectorAll('.triage').length;
  const nc = checkedCount();
  if (el) el.textContent = n + ' claim' + (n === 1 ? '' : 's') + ' marked · ' + nc + '/' + total + ' checked';
  const cN = document.getElementById('chkN'), uN = document.getElementById('unchkN');
  if (cN) cN.textContent = nc;
  if (uN) uN.textContent = total - nc;
  // Slim always-visible progress line (friend feedback round 2): two colors,
  // fill = checked share, label = how many are left. Neutral hues — review
  // progress is not a verdict.
  const fill = document.getElementById('chkFill'), lab = document.getElementById('chkLabel');
  if (fill && total) fill.style.width = (nc / total * 100) + '%';
  if (lab) lab.textContent = (nc >= total && total) ? '✓ all ' + total + ' checked'
                                                    : (total - nc) + ' left to check';
  const lb = document.getElementById('lastChkBtn');
  if (lb) lb.style.display = nc ? '' : 'none';   // nothing checked yet -> nothing to resume
}
function syncNote(t, r) {
  const ta = t.querySelector('.tnote');
  if (ta) ta.style.display = isMarked(r) ? '' : 'none';
}
function initTriage() {
  document.querySelectorAll('.triage').forEach(function(t) {
    const id = t.dataset.id;
    const st = review[id] || {marks: [], note: ''};
    review[id] = st;
    t.querySelectorAll('.tbtn').forEach(function(b) {
      if ((st.marks || []).indexOf(b.dataset.mark) !== -1) b.classList.add('on');
      b.addEventListener('click', function(ev) {
        ev.stopPropagation();
        const r = review[id];
        r.marks = r.marks || [];
        const i = r.marks.indexOf(b.dataset.mark);
        if (i === -1) r.marks.push(b.dataset.mark); else r.marks.splice(i, 1);
        b.classList.toggle('on');
        syncNote(t, r);
        saveReview();
      });
    });
    const ta = t.querySelector('.tnote');
    if (ta) {
      ta.value = st.note || '';
      ta.addEventListener('click', function(ev) { ev.stopPropagation(); });
      ta.addEventListener('input', function() { st.note = ta.value; saveReview(); });
    }
    // "✓ checked" = the human LOOKED at this card (coverage tracking, blind-
    // review loop). Checked with no repair marks = human-confirmed good row.
    const cb = t.querySelector('.cbtn');
    if (cb) {
      const card = document.getElementById('card-' + id);
      if (st.checked) { cb.classList.add('on'); if (card) card.classList.add('hchecked'); }
      cb.addEventListener('click', function(ev) {
        ev.stopPropagation();
        st.checked = !st.checked;
        cb.classList.toggle('on', st.checked);
        if (card) card.classList.toggle('hchecked', st.checked);
        // Remember the most recent ✓ so "last checked" can resume a review
        // (persisted per run, like the marks themselves).
        if (st.checked) { try { localStorage.setItem(REVIEW_KEY + ':last', id); } catch (e) {} }
        saveReview();
        // Re-run the active verdict/status filter: a card that no longer matches
        // (e.g. just-checked under the "Unchecked" filter) leaves the list now.
        if (window.refilterAfterToggle) window.refilterAfterToggle(card);
      });
    }
    syncNote(t, st);
  });
  updateReviewBar();
}
function reviewedClaims() {
  return REVIEW_DATA.claims.filter(function(c) { return isMarked(review[c.id]); })
    .map(function(c) {
      const r = review[c.id];
      return Object.assign({}, c, {marks: (r.marks || []).slice(), note: (r.note || '').trim()});
    });
}
function reviewFileName() {
  // Distinguishable name (owner walkthrough item 3): consumers accept any
  // review*.json, so run name + date never break the /apply-review flow.
  const d = new Date().toISOString().slice(0, 10);
  return 'review_' + (REVIEW_DATA.run.run_name || 'run') + '_' + d + '.json';
}

// Remembered save location (owner walkthrough item 4). Chromium's File System
// Access API works on file:// — the picked directory handle persists in
// IndexedDB, so "set once, saves there forever". Firefox has no such API; the
// plain <a download> (browser Downloads dir) stays as the universal fallback.
const UI_DB = 'ptui';
function idbOpen() {
  return new Promise(function(res, rej) {
    const r = indexedDB.open(UI_DB, 1);
    r.onupgradeneeded = function() { r.result.createObjectStore('handles'); };
    r.onsuccess = function() { res(r.result); };
    r.onerror = function() { rej(r.error); };
  });
}
function idbGet(key) {
  return idbOpen().then(function(db) {
    return new Promise(function(res, rej) {
      const r = db.transaction('handles').objectStore('handles').get(key);
      r.onsuccess = function() { res(r.result); };
      r.onerror = function() { rej(r.error); };
    });
  });
}
function idbSet(key, val) {
  return idbOpen().then(function(db) {
    return new Promise(function(res, rej) {
      const r = db.transaction('handles', 'readwrite').objectStore('handles').put(val, key);
      r.onsuccess = function() { res(); };
      r.onerror = function() { rej(r.error); };
    });
  });
}
async function chooseSaveDir() {
  try {
    const dir = await window.showDirectoryPicker({mode: 'readwrite'});
    await idbSet('reviewDir', dir);
    flashSaveLoc('Reviews will save to “' + dir.name + '”');
  } catch (e) { /* user cancelled */ }
}
async function savedDir() {
  try {
    const dir = await idbGet('reviewDir');
    if (!dir) return null;
    let p = await dir.queryPermission({mode: 'readwrite'});
    if (p !== 'granted') p = await dir.requestPermission({mode: 'readwrite'});
    return p === 'granted' ? dir : null;
  } catch (e) { return null; }
}
function flashSaveLoc(msg) {
  const el = document.getElementById('revCount');
  if (!el) return;
  const o = el.textContent; el.textContent = msg;
  setTimeout(function() { updateReviewBar(); }, 2500);
}
async function downloadReview() {
  if (!markCount() && !checkedCount()) { alert('Mark or ✓-check at least one claim first (buttons at the bottom of each card).'); return; }
  const checkedIds = Object.keys(review).filter(function(k) { return review[k] && review[k].checked; });
  const payload = {run: REVIEW_DATA.run, exported: new Date().toISOString(),
                   marks: reviewedClaims(), checked: checkedIds};
  const json = JSON.stringify(payload, null, 2);
  const name = reviewFileName();
  if (window.showSaveFilePicker) {                 // Chromium: remembered folder
    const dir = await savedDir();
    if (dir) {
      try {
        const fh = await dir.getFileHandle(name, {create: true});
        const w = await fh.createWritable();
        await w.write(json); await w.close();
        flashSaveLoc('Saved ' + name + ' to “' + dir.name + '”');
        return;
      } catch (e) { /* fall through to plain download */ }
    }
  }
  const blob = new Blob([json], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
}
(function() {   // the picker button only makes sense where the API exists
  const b = document.getElementById('saveLocBtn');
  if (b && !window.showDirectoryPicker) b.style.display = 'none';
})();
// Re-apply the current `review` state to the cards WITHOUT re-adding listeners
// (initTriage already wired them). Used after importing a review file.
function refreshTriageDOM() {
  document.querySelectorAll('.triage').forEach(function(t) {
    const id = t.dataset.id;
    const st = review[id] || {marks: [], note: ''};
    review[id] = st;
    t.querySelectorAll('.tbtn').forEach(function(b) {
      b.classList.toggle('on', (st.marks || []).indexOf(b.dataset.mark) !== -1);
    });
    const ta = t.querySelector('.tnote');
    if (ta) ta.value = st.note || '';
    const cb = t.querySelector('.cbtn');
    const card = document.getElementById('card-' + id);
    if (cb) { cb.classList.toggle('on', !!st.checked); if (card) card.classList.toggle('hchecked', !!st.checked); }
    syncNote(t, st);
  });
  updateReviewBar();
}
// Load a review*.json (this viewer's own export, OR an analysis-shaped review
// with id + verdict + reason, e.g. a walkthrough file) and show its comments on
// the cards. Merges into the browser's marks for THIS run; matched by claim id.
function importReview(file) {
  const rd = new FileReader();
  rd.onload = function() {
    let data;
    try { data = JSON.parse(rd.result); } catch (e) { alert('That file is not valid JSON.'); return; }
    const marks = Array.isArray(data) ? data : (data.marks || []);
    if (!marks.length) { alert('No marks found in that review file.'); return; }
    const known = {}; document.querySelectorAll('.triage').forEach(function(t) { known[t.dataset.id] = true; });
    let n = 0, skipped = 0;
    marks.forEach(function(m) {
      if (!m || !m.id) return;
      if (!known[m.id]) { skipped++; return; }          // claim id not in THIS run
      const cur = review[m.id] || {marks: [], note: ''};
      if (Array.isArray(m.marks)) cur.marks = m.marks.slice();     // native export
      else if (m.reason || m.note) { if (!(cur.marks || []).length) cur.marks = ['verdict_wrong']; }
      if (m.note !== undefined && m.note !== '') cur.note = m.note;       // native note
      else if (m.reason) cur.note = (m.verdict ? '[' + m.verdict + '] ' : '') + m.reason;  // analysis/walkthrough
      cur.checked = (m.checked !== undefined) ? !!m.checked : true;
      review[m.id] = cur; n++;
    });
    saveReview(); refreshTriageDOM();
    alert('Loaded ' + n + ' comment(s)' + (skipped ? ' (' + skipped + ' skipped — not in this run)' : '') + '.');
  };
  rd.readAsText(file);
}
function buildBrief() {
  const run = REVIEW_DATA.run;
  const items = reviewedClaims();
  const L = [];
  L.push('# Repair brief — grounding review');
  L.push('');
  L.push('Article file: ' + run.text_file);
  L.push('Sources folder: ' + run.sources_dir);
  L.push('Run folder: ' + run.output_dir + '  (analysis.json and review.json live here)');
  L.push('Verified with ' + run.model + ' on ' + run.timestamp + '.');
  L.push('');
  L.push('## Ground rules for the fixing assistant');
  L.push('- Edit the article file; keep every [[key]] citation marker intact (keys map to source files via the references list).');
  L.push('- Minor wording fixes (hedging, precision, dropping an unsupported qualifier): apply directly.');
  L.push('- Conceptual changes (the argument itself shifts): show old -> new and ask the author first.');
  L.push('- Never invent citations, sources, or evidence.');
  L.push('- Items marked "verdict is wrong" are feedback about the verification tool: do NOT change their text; append them to <run folder>/verdict_feedback.json instead.');
  L.push('- New sources found via Claude Science come back by merging the export: python3 import_claude_research.py --input <export.md or .bib> --merge-into ' + (run.project_dir || '<project folder>') + '  — then python3 download_sources.py --manifest <project folder>/sources_manifest.json and cite the new [[key]]s.');
  L.push('- When done, re-verify: python3 verify_my_text.py --text <article> --sources <sources folder> --output-dir <run folder>  (unchanged claims are not re-judged).');
  L.push('');
  L.push('## Claims (' + items.length + ' marked)');
  items.forEach(function(c) {
    const names = (c.marks || []).map(function(m) { return MARK_NAMES[m] || m; });
    L.push('');
    L.push('### ' + c.id + ' — ' + (c.verdict || '').toUpperCase()
           + (c.confidence ? ' (judge confidence: ' + c.confidence + ')' : '')
           + ' — marked: ' + (names.join(', ') || 'note only'));
    L.push('');
    L.push('Text: "' + c.text + '"');
    if (c.markers && c.markers.length)
      L.push('Cites: ' + c.markers.map(function(m) { return '[[' + m + ']]'; }).join(' '));
    if (c.reason) L.push('Judge: ' + c.reason);
    if (c.second_opinion)
      L.push('Second opinion (' + (c.second_opinion.model || 'other model') + ') DISAGREES — would say '
             + c.second_opinion.verdict + ': ' + (c.second_opinion.reason || ''));
    if (c.partial_support)
      L.push('Partial support: the cited sources back the claim in general, but a specific '
             + 'component is in none of them: ' + (c.partial_support.reason || '')
             + ' — verify that part or trim it. The verdict stays supported.');
    if (c.arbiter && c.arbiter.action) {
      L.push('Arbiter (' + (c.arbiter.model || 'strong model') + ', flagged: '
             + (c.arbiter.trigger || '?') + '): ' + c.arbiter.action
             + (c.arbiter.missing_subclaim ? ' — missing: ' + c.arbiter.missing_subclaim : '')
             + (c.arbiter.proofs && c.arbiter.proofs.length
                ? ' — verified proof: ' + c.arbiter.proofs.map(function(q) { return '"' + q + '"'; }).join(' / ')
                : ''));
      if (c.arbiter.conflict)
        L.push('Conflicting evidence (verified): "' + c.arbiter.conflict.sentence + '" — '
               + (c.arbiter.conflict.why || ''));
    }
    if (c.over_citation && c.over_citation.sources && c.over_citation.sources.length)
      L.push('Possible over-citation: ' + c.over_citation.sources.join(', ')
             + ' — the other cited sources already cover the claim and this one does not back '
             + 'it alone. Consider moving or trimming the citation. Not a correctness problem.');
    if (c.own_kind && c.own_kind.kind === 'fact')
      L.push('Citation needed? This uncited passage asserts a checkable fact: '
             + (c.own_kind.reason || '') + ' — find and cite a source for it (or the author confirms it as their own).');
    if (c.citation_scope && c.citation_scope.scope)
      L.push('Scoped citation (' + c.citation_scope.scope + '): the passage mainly describes the '
             + 'authors\' own work; the citation backs only: '
             + (c.citation_scope.scoped_assertion || '(see reason)') + '. '
             + (c.citation_scope.reason || '')
             + ' The unsupported verdict answers a question the author never asked — '
             + 'no text fix needed unless the scoped part itself is wrong.');
    if (c.covering && c.covering.uncovered && c.covering.uncovered.length) {
      const commonSet = c.covering.common_knowledge || [];
      const gaps = c.covering.uncovered.filter(function(u) { return commonSet.indexOf(u) === -1; });
      if (gaps.length)
        L.push('Coverage gap: no displayed sentence proves: ' + gaps.join('; ')
               + ' — find the proving sentence in the cited source(s), or add a citation / rewrite for that part.');
      if (commonSet.length)
        L.push('Common knowledge (no action needed unless the author disagrees): ' + commonSet.join('; '));
    }
    (c.evidences || []).forEach(function(e) {
      if (e.sentence)
        L.push('- Evidence (' + (e.supported ? 'supports' : 'does not support') + ') from '
               + (e.source_title || 'unknown source') + ': "' + e.sentence + '"');
    });
    if (c.note) L.push('Author note: ' + c.note);
    if ((c.marks || []).indexOf('wrong_source') !== -1) {
      if (c.alternatives && c.alternatives.length) {
        L.push('Closest passages in the OTHER sources of this run (re-citation candidates):');
        c.alternatives.forEach(function(a) {
          L.push('- ' + (a.source_title || a.paper_id) + ' (relevance ' + a.relevance + '): "'
                 + (a.evidence || a.text) + '"');
        });
      }
      L.push('If none of the run\'s sources fits, research request (paste into Claude Science / any deep-research tool):');
      L.push('> Find published sources that support this claim: "' + c.text + '". Return a short report with full citations (title, authors, year, DOI or URL) and the exact supporting passage from each.');
    }
    if ((c.marks || []).indexOf('rewrite') !== -1)
      L.push('Task: rewrite this claim minimally so the quoted evidence from its cited source(s) fully supports it.');
    if ((c.marks || []).indexOf('more_support') !== -1) {
      L.push('Task: the shown evidence does not prove the claim as written — evidence first, edit last:');
      L.push('1. Search the FULL TEXT of the cited source(s) (and this run\'s other sources) for sentences that actually prove the unproven part(s). Quote every candidate VERBATIM with where it came from — never paraphrase a proof.');
      L.push('2. If real proof exists: report it to the author; the text needs NO change (the tool missed evidence, which the next run\'s arbiter/rerun can pick up).');
      L.push('3. Only if no proof exists anywhere: rewrite the claim minimally so the evidence that DOES exist fully supports it (hedge, narrow, or drop the unproven part).');
    }
    if ((c.marks || []).indexOf('needs_citation') !== -1) {
      L.push('Task: this passage should cite a source — find one and add a [[key]] citation (register the source in the project first if it is new).');
      L.push('Research request (paste into Claude Science / any deep-research tool):');
      L.push('> Find published sources that support this claim: "' + c.text + '". Return a short report with full citations (title, authors, year, DOI or URL) and the exact supporting passage from each.');
    }
    if ((c.marks || []).indexOf('verdict_wrong') !== -1)
      L.push('Task: tool feedback only — record {claim id, text, tool verdict, author disagreement, note} in verdict_feedback.json. Do not edit the article text for this item.');
    if ((c.marks || []).indexOf('other') !== -1)
      L.push('Task: free-form — read the author note above and decide the right action; if the intent is unclear, ASK the author (surface it) instead of guessing an edit.');
  });
  return L.join('\n');
}
function copyBrief() {
  if (!markCount()) { alert('Mark at least one claim first (buttons at the bottom of each card).'); return; }
  cgCopy(buildBrief(), document.getElementById('briefBtn'));
}
function buildScienceRequest() {
  const run = REVIEW_DATA.run;
  const items = reviewedClaims().filter(function(c) {
    return (c.marks || []).indexOf('wrong_source') !== -1;
  });
  const L = [];
  L.push('Research request: I wrote an article and a verification tool checked every cited claim against its source. The claims below are the ones whose CURRENT source does not support them — I need better published sources for each.');
  L.push('');
  L.push('For EACH claim:');
  L.push('- find 1-3 published sources (papers, reports, reputable data) that DIRECTLY support it, each with full citation (title, authors, year, DOI or URL) and the exact supporting passage quoted;');
  L.push('- if no credible source supports it, say so plainly — do not stretch a near-miss;');
  L.push('- write the final report as a cited document with a bibliography, so I can export it with citations and merge the sources back into my project programmatically.');
  L.push('');
  L.push('The claims:');
  items.forEach(function(c, i) {
    L.push('');
    L.push((i + 1) + '. "' + c.text + '"');
    if (c.note) L.push('   My note: ' + c.note);
    if (c.reason) L.push('   Why the current source failed: ' + c.reason);
  });
  return L.join('\n');
}
function copyScience() {
  const n = reviewedClaims().filter(function(c) {
    return (c.marks || []).indexOf('wrong_source') !== -1;
  }).length;
  if (!n) { alert('Mark at least one claim as "wrong source" first.'); return; }
  cgCopy(buildScienceRequest(), document.getElementById('sciBtn'), 'Copied (' + n + ' claims)');
}
initTriage();
"""


def _coverage_ratio_bar(n_sup: int, n_uns: int, n_unverifiable: int,
                        n_own: int) -> str:
    """A one-glance 'Claim Coverage' strip — the whole document's
    supported / unsupported / unverifiable / your-own mix as a stacked bar.
    (Prior-art idea from CHI PaperTrail; see docs/PRIOR_ART_REUSE.md #6.)"""
    judged_uns = max(n_uns - n_unverifiable, 0)
    total = n_sup + judged_uns + n_unverifiable + n_own
    if total == 0:
        return ""
    # Segment hues match the verdict palette exactly (one hue per meaning —
    # the cards use teal/red/indigo, so the bar does too; friend feedback #2).
    segs = [("supported", n_sup, "#0f766e"),
            ("unsupported", judged_uns, "#b91c1c"),
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
    return (f'<div class="covratio"><div class="ccbar">{"".join(bar)}</div>'
            f'<div class="cclegend">{"".join(legend)}</div></div>')


def _assessment_panel(assessment: Optional[Dict[str, Any]]) -> str:
    """Full-width 'Argument structure' panel from the --argument-map passes
    (argument_map / crux / evidence_independence). Returns "" when the run was
    not invoked with --argument-map (backward-compatible: no panel, no CSS need)."""
    if not assessment:
        return ""
    argmap = assessment.get("argument_map") or {}
    crux = assessment.get("crux") or {}
    indep = assessment.get("independence") or {}

    # Cruxes — claims the argument most depends on (topology + fragility).
    cx_rows = []
    for c in (crux.get("cruxes") or []):
        frag = c.get("fragility")
        frag_badge = (f'<span class="am-frag" title="evidence fragility">fragile · {_esc(str(frag))}</span>'
                      if frag else "")
        cx_rows.append(
            f'<li><span class="am-score">{_esc(str(c.get("score","")))}</span> '
            f'<span class="am-ctext">{_esc(c.get("text",""))}</span> {frag_badge}'
            f'<div class="am-why">{_esc(c.get("why",""))}</div></li>')
    crux_html = ("<ol class='am-list'>" + "".join(cx_rows) + "</ol>") if cx_rows \
                else "<p class='meta'>no cruxes identified</p>"

    # Evidence independence — cited-source pairs flagged as correlated.
    src_title = {s.get("key"): (s.get("title") or s.get("key"))
                 for s in (indep.get("sources") or [])}
    flagged = [p for p in (indep.get("pairs") or []) if p.get("strength")]
    ind_rows = []
    for p in flagged:
        rels = ", ".join(p.get("relations") or [])
        ind_rows.append(
            f'<li><span class="am-strength am-{_esc(p.get("strength",""))}">{_esc(p.get("strength",""))}</span> '
            f'<b>{_esc(src_title.get(p.get("a"), p.get("a")))}</b> ↔ '
            f'<b>{_esc(src_title.get(p.get("b"), p.get("b")))}</b> '
            f'<span class="am-rels">{_esc(rels)}</span>'
            f'<div class="am-why">{_esc(p.get("why") or "")}</div></li>')
    summ = indep.get("summary") or {}
    n_src = summ.get("n_sources", len(src_title))
    n_clusters = summ.get("n_clusters")
    indep_head = (f"{len(flagged)} flagged pair(s) / {n_src} sources"
                  + (f" → {n_clusters} independent cluster(s)" if n_clusters is not None else ""))
    indep_html = ("<ul class='am-list'>" + "".join(ind_rows) + "</ul>") if ind_rows \
        else "<p class='meta'>no correlated-source pairs flagged — cited sources look independent</p>"

    # Argument map — argdown-style inference edge list (textual, not a graph render).
    nodes = {n.get("id"): n for n in (argmap.get("nodes") or [])}
    thesis_ids = list(argmap.get("thesis_ids") or [])

    def _short(cid):
        n = nodes.get(cid) or {}
        t = n.get("text") or (cid or "")
        return _esc(t if len(t) <= 90 else t[:87] + "…")

    edge_rows = []
    for e in (argmap.get("edges") or []):
        et = e.get("type", "relates")
        arrow = {"supports": "supports", "attacks": "attacks"}.get(et, et)
        edge_rows.append(
            f'<li><span class="am-node">{_short(e.get("from"))}</span> '
            f'<span class="am-edge am-{_esc(et)}">→ {_esc(arrow)}</span> '
            f'<span class="am-node">{_short(e.get("to"))}</span></li>')
    thesis_html = ""
    if thesis_ids:
        trows = "".join(f"<li>{_short(t)}</li>" for t in thesis_ids)
        thesis_html = f"<div class='am-sub'><h4>Thesis</h4><ul class='am-list'>{trows}</ul></div>"
    map_html = thesis_html + (
        f"<div class='am-sub'><h4>Inference edges ({len(edge_rows)})</h4>"
        f"<ul class='am-list am-edges'>{''.join(edge_rows)}</ul></div>"
        if edge_rows else "<p class='meta'>no edges inferred</p>")

    method = _esc(argmap.get("method") or crux.get("method") or "")
    model = _esc(str(argmap.get("model") or crux.get("model") or indep.get("model") or ""))
    note = " · ".join(x for x in [f"method: {method}" if method else "",
                                       f"model: {model}" if model else ""] if x)
    return f"""
<div class="assess" id="assess">
  <div class="assess-head">
    <div class="assess-title">Argument structure <span class="am-note">experimental{(' · ' + note) if note else ''}</span></div>
    <button class="toggle" id="assessToggle" onclick="toggleAssess()">collapse</button>
  </div>
  <div class="assess-body" id="assessBody">
    <div class="assess-col">
      <h3>Cruxes <span class="am-note">the argument leans hardest on these</span></h3>
      {crux_html}
    </div>
    <div class="assess-col">
      <h3>Evidence independence <span class="am-note">{_esc(indep_head)}</span></h3>
      {indep_html}
    </div>
    <div class="assess-col am-mapcol">
      <h3>Argument map</h3>
      {map_html}
    </div>
  </div>
</div>"""


def generate(analysis: Dict[str, Any], output_path: str, title: str = "Claim Verification",
             source_texts: Optional[Dict[str, str]] = None,
             assessment: Optional[Dict[str, Any]] = None) -> str:
    claims = analysis["text_claims"]
    omitted = analysis.get("omitted", [])
    totals = analysis.get("coverage", {}).get("totals", {})
    fname_map = _filename_map(analysis)
    paper_meta = _paper_meta(analysis)
    source_texts = source_texts or {}

    text_html = "".join(_claim_span(c) for c in claims)
    # The copyable --fix-claim command (static viewer can't call an LLM itself).
    meta = analysis.get("metadata", {})
    out_dir = meta.get("output_dir") or os.path.dirname(os.path.abspath(output_path))
    model_flag = f" --model {shlex.quote(meta['model'])}" if meta.get("model") else ""
    fix_cmd_base = (f"python3 verify_my_text.py --output-dir {shlex.quote(out_dir)}"
                    f"{model_flag} --fix-claim ")
    # Cards in DOCUMENT ORDER (owner requirement 2026-07-03) — the reader follows
    # their text top-to-bottom; verdict grouping is a filter, not the layout.
    claim_cards = "".join(_claim_card(c, fname_map, source_texts, paper_meta, fix_cmd_base)
                          for c in claims)
    n_sup = sum(1 for c in claims if c["verdict"] == "supported")
    n_scoped_cite = sum(1 for c in claims if _is_scoped(c))
    # Scoped-citation cards are their OWN class (owner 2026-07-12): everywhere
    # the viewer counts "unsupported" they are excluded — analysis.json still
    # carries verdict=unsupported (gate contract), the separation is display.
    n_uns = sum(1 for c in claims if c["verdict"] == "unsupported") - n_scoped_cite
    n_own = sum(1 for c in claims if c["verdict"] == "own")
    scoped_total = (f'\n    <b style="color:#818cf8">{n_scoped_cite} scoped citation</b> &nbsp;·&nbsp;'
                    if n_scoped_cite else "")
    # Honest coverage: "unsupported" that really means "the source file was never
    # checked" must not be lumped in with judged failures, or the header implies
    # more (and worse) verification than actually happened.
    n_unverifiable = sum(1 for c in claims if c["verdict"] == "unsupported"
                         and str(c.get("reason", "")).startswith("source_file_missing"))
    unverifiable_total = (f' &nbsp;·&nbsp; <b style="color:#9ca3af">{n_unverifiable} unverifiable '
                          f'(source file missing)</b>' if n_unverifiable else "")
    n_changed = sum(1 for c in claims if (c.get("prev") or {}).get("changed"))
    changed_btn = (f'<button class="fbtn" data-f="changed">Changed ({n_changed})</button>'
                   if n_changed else "")
    n_cite = sum(1 for c in claims if c["verdict"] == "own"
                 and (c.get("own_kind") or {}).get("kind") == "fact")
    cite_btn = (f'<button class="fbtn cite" data-f="citeneeded" title="uncited passages that '
                f'assert a checkable fact — consider citing a source">Citation needed ({n_cite})</button>'
                if n_cite else "")
    n_partly = sum(1 for c in claims if c["verdict"] == "supported"
                   and c.get("proof_state") == "partial")
    partly_btn = (f'<button class="fbtn partlyf" data-f="partlyproven" title="claims judged '
                  f'supported whose shown sentences do not prove every component — the amber '
                  f'line on the card names the unproven part">Not proven as written ({n_partly})</button>'
                  if n_partly else "")
    n_partial = sum(1 for c in claims if c.get("partial_support"))
    partial_btn = (f'<button class="fbtn partial" data-f="partial" title="multi-citation claims '
                   f'judged supported, but a specific component was in none of the cited '
                   f'sources — verify that part">Partial support ({n_partial})</button>'
                   if n_partial else "")
    n_scoped = sum(1 for c in claims if c["verdict"] == "unsupported"
                   and (c.get("citation_scope") or {}).get("scope")
                   in ("methods", "concept", "related"))
    scoped_btn = (f'<button class="fbtn scopedf" data-f="scoped" title="unsupported passages '
                  f'that mainly describe the authors\' own work — the citation backs only a '
                  f'method/concept/related pointer, so the red question never applied">'
                  f'Scoped citation ({n_scoped})</button>'
                  if n_scoped else "")
    n_overcite = sum(1 for c in claims if (c.get("over_citation") or {}).get("sources"))
    overcite_btn = (f'<button class="fbtn overcitef" data-f="overcite" title="claims where one '
                    f'cited source adds nothing the others don\'t already cover — the citation '
                    f'may belong elsewhere">Over-cited ({n_overcite})</button>'
                    if n_overcite else "")
    assess_section = _assessment_panel(assessment)
    # Scoped-citation cards ride the "own" segment of the ratio bar — they are
    # the authors' own text by classification.
    coverage_ratio = _coverage_ratio_bar(n_sup, n_uns, n_unverifiable,
                                         n_own + n_scoped_cite)
    # Confidence filters (owner walkthrough item 1): the chip already exists per
    # card; give low + medium their own filter buttons (high stays chip-only —
    # it's the uninteresting bulk).
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
        <button class="fbtn active" data-f="all">All ({len(claims)})</button>
        <button class="fbtn" data-f="supported">Supported ({n_sup})</button>
        {partly_btn}
        <button class="fbtn" data-f="unsupported">Unsupported ({n_uns})</button>
        {scoped_btn}
        <button class="fbtn" data-f="own">Your own ({n_own})</button>
        {conf_btns}
        {partial_btn}
        {overcite_btn}
        {cite_btn}
        {changed_btn}
        <button class="fbtn hchk" data-f="hchecked" title="cards you marked ✓ checked">✓ Checked (<span id="chkN">0</span>)</button>
        <button class="fbtn hunchk" data-f="hunchecked" title="cards you have not marked ✓ checked yet">Unchecked (<span id="unchkN">0</span>)</button>
      </div>"""

    # Review bar: mark cards below, then export — a self-contained markdown brief
    # for any LLM (clipboard) or review.json for the /apply-review Claude Code
    # command (see docs/REPAIR_PLAYBOOK.md). Marks persist in this browser only.
    review_bar = """
      <div class="reviewbar">
        <span class="rev-title">Review</span>
        <span id="revCount" class="rev-count">0 claims marked</span>
        <button id="briefBtn" class="rbtn" onclick="copyBrief()">Copy repair brief</button>
        <button class="rbtn" onclick="downloadReview()" title="saves as review_&lt;run&gt;_&lt;date&gt;.json — to your chosen folder if set, else the browser's Downloads">Download review file</button>
        <input type="file" id="revfile" accept="application/json,.json" style="display:none" onchange="if(this.files[0]){importReview(this.files[0]); this.value='';}">
        <button class="rbtn" onclick="document.getElementById('revfile').click()" title="load a review_&lt;run&gt;.json you saved earlier (or a walkthrough review file) — its comments appear on the matching cards">Load review file</button>
        <button id="saveLocBtn" class="rbtn" onclick="chooseSaveDir()"
                title="pick a folder once (e.g. the run folder) — every future review file saves there without asking. Chromium only; elsewhere the file goes to Downloads.">Save location…</button>
        <button id="sciBtn" class="rbtn sci" onclick="copyScience()"
                title="Bundle every claim marked 'wrong source' into ONE research request to paste into any deep-research tool (Claude Science, Elicit, Perplexity, a plain LLM…). A Claude Science export merges back automatically via import_claude_research.py --merge-into; any other tool's results you add manually.">Copy research request</button>
        <span class="rev-hint">open a card&rsquo;s <b>▸ details &amp; review</b> to mark it (wrong source / rewrite / verdict wrong / other), then export — paste the brief into the LLM that wrote your text</span>
      </div>"""

    # "How to read this" key — collapsed by default, native <details> (no JS,
    # server-free). Reuses the real badge/chip CSS classes so the swatches match
    # the cards exactly. Every chip is labelled "nudge, never a verdict" because
    # the #3 walkthrough finding was that first-time readers can't tell a verdict
    # from a dismissible hint.
    legend_html = """
      <details class="legend">
        <summary>How to read this</summary>
        <div class="legend-body">
          <div class="legend-grp">
            <div class="legend-h">Verdicts</div>
            <div class="legrow"><span class="badge supported">SUPPORTED</span> the cited source contains the statement — not that the source is strong or the claim is true</div>
            <div class="legrow"><span class="badge supported partly">NOT PROVEN AS WRITTEN</span> judged supported by the sources overall, but the shown sentences don't prove every component — the amber line on the card names the unproven part; the underlying verdict is unchanged</div>
            <div class="legrow"><span style="color:#6b7280">◦ commonly known</span> a component with no shown proof that the tool judged an everyday fact needing no citation — grey and quiet, never counted against the claim</div>
            <div class="legrow"><span class="badge unsupported">UNSUPPORTED</span> no cited source backs it (or the source file is missing)</div>
            <div class="legrow"><span class="badge scoped">SCOPED CITATION</span> the passage is the authors&rsquo; own work; the citation backs only a method/concept/related pointer inside it — not an authoring error</div>
            <div class="legrow"><span class="badge own">YOUR OWN CLAIM</span> your uncited claim — thesis, argument, transition; nothing was checked</div>
            <div class="legrow"><span class="badge omitted">UNUSED</span> a point one of your sources makes that your text didn't cite — a menu, not an error</div>
          </div>
          <div class="legend-grp">
            <div class="legend-h">Chips — nudges, never a verdict</div>
            <div class="legrow"><span class="confchip high">conf</span><span class="confchip medium">conf</span><span class="confchip low">conf</span> how sure the judge is (derived from votes / method / cosine — no extra LLM call)</div>
            <div class="legrow"><span class="citechip">citation needed?</span> an uncited passage that asserts a checkable fact — a nudge to cite, not a verdict</div>
            <div class="legrow"><span class="partialchip">partial support?</span> the cited source(s) back only part of the claim; the verdict stays supported</div>
            <div class="legrow"><span class="overchip">over-cited?</span> one cited source adds nothing the others already cover</div>
            <div class="legrow"><span class="shchip">secondhand evidence?</span> the supporting sentence itself cites another work — consider citing the original</div>
            <div class="legrow"><span class="disagreechip">sources may disagree?</span> a co-cited source's evidence was judged to argue the opposite</div>
            <div class="legrow"><span class="sochip">2nd opinion</span> a second model disagreed — lowers confidence, read the evidence yourself; never a veto</div>
            <div class="legrow"><span class="dcchip flag">deep check</span> a stronger model re-read the claim with source context and commented (testing aid) — its commentary is on the card; never a veto</div>
            <div class="legrow"><span class="abchip fetch">🔷 proof may exist</span> the arbiter (--arbiter) re-read a FLAGGED claim with the source and found verbatim-verified sentences the judge never saw — read them and decide; never a veto</div>
            <div class="legrow"><span class="abchip conflict">⚡ conflicting evidence?</span> the arbiter found a source sentence that may CONTRADICT the claim (verified verbatim) — read it on the card</div>
            <div class="legrow"><span class="rescuechip">⛑ arbiter rescue</span> first judged unsupported; the arbiter located verbatim-verified proof and the PRIMARY judge re-judged it unanimously supported — the verdict flip is the primary judge's, never the arbiter's</div>
            <div class="legrow"><span class="rescuechip">⛑ gap closed by arbiter</span> was flagged &ldquo;not proven as written&rdquo;; the arbiter re-read the full source and found verbatim-verified proof for the gap, so the amber flag was cleared (the verdict itself never moved). An amber that SURVIVES the arbiter means a second model with the whole source also found no proof</div>
            <div class="legrow"><span class="changed-chip">✎ changed</span> edited since the last run (incremental re-runs only)</div>
          </div>
          <div class="legend-grp">
            <div class="legend-h">Reviewing — the workflow</div>
            <div class="legrow"><b>1 · Read.</b> Click any sentence on the left (or press <kbd>&rarr;</kbd>) — its card shows here, one at a time. <kbd>&larr;</kbd>/<kbd>&rarr;</kbd> step through claims; the filter chips narrow what you step through. <b>show all cards</b> (top of this column) switches back to the full scrolling list.</div>
            <div class="legrow"><b>2 · Check off.</b> Press <b>✓ checked</b> on each card you've looked at — the thin progress line above the cards fills and counts what's left; <b>&#8618; last checked</b> jumps back to where you stopped, even after reopening the file.</div>
            <div class="legrow"><b>3 · Mark problems.</b> On the card: <b>wrong source</b> / <b>rewrite text</b> / <b>find proof / rewrite</b> (= hunt the source for stronger sentences first, rewrite only if none exist) / <b>verdict wrong</b> / <b>needs citation</b> / <b>other</b>, plus a note.</div>
            <div class="legrow"><b>4 · Export.</b> Open <b>&#9656; export review</b> — the small button next to the progress line — then <b>Copy repair brief</b> (paste into any LLM) or <b>Download review file</b> (for the <code>/apply-review</code> command). Marks live in this browser only until you export.</div>
          </div>
        </div>
      </details>"""

    # Omitted are pre-sorted by relevance (matcher). Show the most-relevant slice by
    # default and collapse the long, mostly-irrelevant tail behind a toggle.
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
                         + f'<button class="toggle" id="omitToggle" onclick="toggleOmitted()">Show {len(tail_omitted)} more (less relevant)</button>'
                         + f'<div id="omittedTail" class="collapsed">{tail_html}</div>')
    else:
        omitted_cards = shown_html
    # Always show "top N of M" so the total (which can be tens of thousands of
    # decomposed source points) never reads as an error count, and the viewer's
    # slice reconciles with the terminal total. "Unused source points" = a menu.
    omitted_sec_label = (
        f"Unused source points — most relevant first (top {len(shown_omitted)} of {len(omitted)})"
        if len(omitted) > len(shown_omitted) else
        f"Unused source points — most relevant first ({len(omitted)})"
        if omitted else
        # honest empty state: decomposition off = not analyzed, not "nothing found"
        "Unused source points — not analyzed in this run"
        if analysis.get("metadata", {}).get("decompose") is False else
        "Unused source points (things your sources say that your text didn't cite)")
    coverage_html = _coverage_bars(analysis.get("coverage", {}))
    n_sources = len(analysis.get("coverage", {}).get("per_source", {}))
    cov_start_collapsed = n_sources > 4          # many sources -> hide by default
    cov_collapsed = " collapsed" if cov_start_collapsed else ""
    cov_btn_label = "show" if cov_start_collapsed else "hide"
    has_pdf = any((fn or "").lower().endswith(".pdf") for fn in fname_map.values())
    has_text = bool(source_texts)

    # Input problems (missing files, unmapped markers) must be visible in the result,
    # not only in the run's terminal log — they change how verdicts should be read.
    marker_errors = analysis.get("metadata", {}).get("marker_errors", []) or []
    warn_html = ""
    if marker_errors:
        items = "".join(f"<li>{_esc(w)}</li>" for w in marker_errors)
        warn_html = (f'<div class="warnbanner"><b>⚠ {len(marker_errors)} input warning(s)</b>'
                     f' — cited sources that could not be used (their claims show as'
                     f' unsupported):<ul>{items}</ul></div>')
    # Embed text sources so "Open source text" can build the new tab with no server.
    st_json = json.dumps(source_texts, ensure_ascii=False).replace("</", "<\\/")
    # Review-loop embeds: RUN_ID keys the localStorage marks to THIS run (a re-run
    # is a fresh review); REVIEW_DATA is the self-contained export payload.
    run_id = hashlib.sha1(
        f"{meta.get('text_file', '')}|{meta.get('timestamp', '')}".encode("utf-8")
    ).hexdigest()[:12]
    rd_json = json.dumps(_review_data(analysis, claims, out_dir),
                         ensure_ascii=False).replace("</", "<\\/")
    # Header counters: verdict counts keep their hues; everything else is a
    # neutral light grey (color budget, friend feedback #2). "not proven as
    # written" stays amber — it is verdict-adjacent state.
    changed_total = (f'&nbsp;·&nbsp; <b style="color:#e5e7eb">{n_changed} changed since last run</b>'
                     if n_changed else "")
    n_so_flags = sum(1 for c in claims
                     if (c.get("second_opinion") or {}).get("agrees") is False)
    so_total = (f'&nbsp;·&nbsp; <b style="color:#e5e7eb">⚠ {n_so_flags} second-opinion '
                f'flag{"s" if n_so_flags != 1 else ""}</b>' if n_so_flags else "")
    cite_total = (f'&nbsp;·&nbsp; <b style="color:#e5e7eb">📎 {n_cite} citation '
                  f'suggestion{"s" if n_cite != 1 else ""}</b>' if n_cite else "")
    partly_total = (f' <b style="color:#fbbf24">(of which {n_partly} not proven as written)</b>'
                    if n_partly else "")

    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{_esc(title)}</title>
<style>
  :root {{ --teal:#0f766e; --teal-bg:#ccfbf1; --red:#b91c1c; --red-bg:#fee2e2; --amber:#b45309; --gray:#6b7280; }}
  * {{ box-sizing:border-box; }}
  html,body {{ height:100%; }}
  body {{ font-family:-apple-system,Segoe UI,Roboto,sans-serif; margin:0; color:#1f2937; background:#f9fafb;
         display:flex; flex-direction:column; }}
  header {{ background:#111827; color:#fff; padding:12px 24px; }}
  header h1 {{ margin:0 0 4px; font-size:17px; }}
  .head-row {{ display:flex; justify-content:space-between; align-items:center; gap:12px; }}
  .totals {{ font-size:13px; opacity:.85; }}
  .totals b.s {{ color:#5eead4; }} .totals b.u {{ color:#fca5a5; }} .totals b.o {{ color:#fdba74; }}
  .scopenote {{ font-size:11px; opacity:.55; margin-top:3px; }}
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
  .cov-fill {{ height:100%; background:var(--teal); }}
  .cov-num {{ width:340px; color:var(--gray); }}
  .cov-status {{ display:inline-block; margin-right:8px; padding:0 6px; border-radius:8px;
                 background:var(--chip-bg, #eef1f4); font-size:11px; }}

  /* Two side-by-side columns: Your text | Your claims */
  .layout {{ display:flex; flex:1; min-height:0; }}
  .doc-wrap {{ flex:1; display:flex; flex-direction:column; min-width:0; background:#fff; border-right:1px solid #e5e7eb; }}
  .cards {{ flex:1; display:flex; flex-direction:column; min-width:0; }}
  .doc-head {{ display:flex; justify-content:space-between; align-items:center; padding:6px 16px; background:#f3f4f6; }}
  .doc-head h2 {{ font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--gray); margin:0; }}
  .toggle {{ font-size:11px; padding:2px 8px; border:1px solid #d1d5db; background:#fff; border-radius:4px; cursor:pointer; }}
  .doc {{ flex:1; padding:14px 18px; line-height:1.9; overflow:auto; }}
  .doc.collapsed {{ display:none; }}
  .cards-body {{ flex:1; overflow:auto; padding:12px 18px; }}

  h2.sec {{ font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--gray); margin:14px 0 6px; }}
  .claim {{ cursor:pointer; padding:1px 2px; border-radius:3px; border-bottom:2px solid transparent; }}
  .claim.supported {{ background:var(--teal-bg); border-bottom-color:var(--teal); }}
  .claim.unsupported {{ background:var(--red-bg); border-bottom-color:var(--red); }}
  .claim.own {{ background:#eef2ff; border-bottom-color:#818cf8; }}
  .claim.scopedcite {{ background:#eef2ff; border-bottom-color:#6366f1; }}
  /* Selected state = Material-style state layer: a translucent darkening of the
     element's OWN background (friend feedback #3) — never a separate accent hue.
     Inset shadow paints above the background but below the text. */
  .claim.active {{ box-shadow:inset 0 0 0 999px rgba(15,23,42,.16); }}
  sup.mark {{ color:#475569; font-weight:700; }}
  .card {{ border:1px solid #e5e7eb; border-left-width:5px; border-radius:9px; padding:12px 14px; margin:10px 0; background:#fff; cursor:pointer; box-shadow:0 1px 2px rgba(0,0,0,.05); transition:opacity .2s ease; }}
  .card.leaving {{ opacity:0; }}
  .card.supported {{ border-left-color:var(--teal); }}
  .card.unsupported {{ border-left-color:var(--red); }}
  .card.omitted {{ border-left-color:var(--amber); }}
  .card.active {{ box-shadow:inset 0 0 0 999px rgba(15,23,42,.05), 0 1px 2px rgba(0,0,0,.05); border-color:#94a3b8; }}
  .card-head {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; }}
  .head-left {{ display:flex; align-items:center; gap:6px; }}
  .claimno {{ font-size:11px; color:#6b7280; font-family:monospace; }}
  .badge {{ font-size:11px; font-weight:700; padding:2px 7px; border-radius:10px; color:#fff; }}
  .badge.supported {{ background:var(--teal); }} .badge.unsupported {{ background:var(--red); }} .badge.omitted {{ background:var(--amber); }}
  .badge.own {{ background:#6366f1; }}
  .card.own {{ border-left:3px solid #818cf8; }}
  .card.scopedcite {{ border-left:3px solid #6366f1; }}
  .own-note {{ font-size:12px; color:#4338ca; background:#eef2ff; padding:6px 8px; border-radius:4px; }}
  .card-claim .leadin {{ background:#eef2ff; color:#4b5563; border-bottom:2px solid #818cf8; }}
  .leadin-chip {{ font-size:9px; font-weight:700; letter-spacing:.02em; padding:1px 6px;
                  border-radius:8px; background:#eef2ff; color:#6366f1; border:1px solid #c7d2fe; }}
  .leadin-note {{ font-size:12px; color:#4338ca; background:#eef2ff; padding:4px 8px; border-radius:4px; margin-top:6px; }}
  /* Color budget (friend feedback #2): hue is reserved for verdict states —
     teal/green = supported, red = unsupported, amber = partial / not-proven,
     indigo = own + scoped-cite. Every OTHER chip and note is a neutral grey
     "ghost" (outline) chip: filled color = status, outline = metadata. */
  .confchip {{ font-size:9px; font-weight:700; letter-spacing:.02em; padding:1px 6px;
               border-radius:8px; border:1px solid #d1d5db; background:#fff; color:#6b7280;
               white-space:nowrap; }}
  .confchip.low {{ color:#374151; border-color:#9ca3af; }}
  .sochip {{ font-size:9px; font-weight:700; letter-spacing:.02em; padding:1px 6px;
             border-radius:8px; margin-left:6px; vertical-align:middle;
             background:#fff; color:#6b7280; border:1px solid #d1d5db; cursor:help; }}
  .ownerchip {{ font-size:9px; font-weight:700; letter-spacing:.02em; padding:1px 6px;
                border-radius:8px; margin-left:6px; vertical-align:middle;
                background:#fff; color:#6b7280; border:1px solid #d1d5db; cursor:help; }}
  .so-note {{ font-size:12px; background:#f8fafc; border:1px solid #cbd5e1; color:#475569;
              border-radius:6px; padding:6px 10px; margin:8px 0 0; }}
  .dcchip {{ font-size:9px; font-weight:700; letter-spacing:.02em; padding:1px 6px;
             border-radius:8px; margin-left:6px; vertical-align:middle; cursor:help;
             background:#fff; color:#6b7280; border:1px solid #d1d5db; }}
  .dcchip.flag {{ color:#374151; border-color:#9ca3af; }}
  .dc-note {{ font-size:12px; background:#f8fafc; border:1px solid #cbd5e1; color:#475569;
              border-radius:6px; padding:6px 10px; margin:8px 0 0; }}
  .dc-note.flag {{ color:#374151; }}
  .dc-better {{ margin-top:4px; font-style:italic; }}
  .abchip {{ font-size:9px; font-weight:700; letter-spacing:.02em; padding:1px 6px;
             border-radius:8px; margin-left:6px; vertical-align:middle; cursor:help;
             background:#fff; color:#6b7280; border:1px solid #d1d5db; }}
  .abchip.conflict {{ color:#374151; border-color:#9ca3af; }}
  .ab-note {{ font-size:12px; background:#f8fafc; border:1px solid #cbd5e1; color:#475569;
              border-radius:6px; padding:6px 10px; margin:8px 0 0; }}
  .ab-note.ok {{ background:#f8fafc; border-color:#e2e8f0; color:#475569; }}
  .ab-note.conflict {{ color:#374151; }}
  .ab-quote {{ margin:4px 0 4px 10px; font-style:italic; }}
  .ab-rewrite {{ margin-top:4px; font-style:italic; }}
  .citechip {{ font-size:9px; font-weight:700; letter-spacing:.02em; padding:1px 6px;
               border-radius:8px; margin-left:6px; vertical-align:middle;
               background:#fff; color:#6b7280; border:1px solid #d1d5db; cursor:help; }}
  .citechip.loud {{ font-size:11px; padding:2px 8px; background:#334155; color:#fff;
                    border-color:#334155; }}
  .kindchip {{ font-size:9px; font-weight:600; letter-spacing:.02em; padding:1px 6px;
               border-radius:8px; margin-left:6px; vertical-align:middle;
               background:#fff; color:#6b7280; border:1px solid #d1d5db; cursor:help; }}
  .cite-note {{ font-size:12px; background:#f8fafc; border:1px solid #cbd5e1; color:#475569;
                border-radius:6px; padding:6px 10px; margin:8px 0 0; }}
  .partialchip {{ font-size:9px; font-weight:700; letter-spacing:.02em; padding:1px 6px;
                  border-radius:8px; margin-left:6px; vertical-align:middle;
                  background:#fef3c7; color:#b45309; border:1px solid #fbbf24; cursor:help; }}
  .partial-note {{ font-size:12px; background:#fffbeb; border:1px solid #fbbf24; color:#b45309;
                   border-radius:6px; padding:6px 10px; margin:8px 0 0; }}
  .fbtn.partial {{ border-color:#fbbf24; color:#b45309; }}
  .badge.partly {{ background:#d97706; }}
  .fbtn.partlyf {{ border-color:#fbbf24; color:#b45309; }}
  .badge.scoped {{ background:#6366f1; }}
  .scopechip {{ font-size:9px; font-weight:700; letter-spacing:.02em; padding:1px 6px;
                border-radius:8px; margin-left:6px; vertical-align:middle;
                background:#eef2ff; color:#4338ca; border:1px solid #a5b4fc; cursor:help; }}
  .scope-note {{ font-size:12px; background:#eef2ff; border:1px solid #a5b4fc; color:#4338ca;
                 border-radius:6px; padding:6px 10px; margin:8px 0 0; }}
  .fbtn.scopedf {{ border-color:#a5b4fc; color:#4338ca; }}
  .rescuechip {{ font-size:9px; font-weight:700; letter-spacing:.02em; padding:1px 6px;
                 border-radius:8px; margin-left:6px; vertical-align:middle;
                 background:#ccfbf1; color:#0f766e; border:1px solid #5eead4; cursor:help; }}
  .rescue-note {{ font-size:12px; background:#f0fdfa; border:1px solid #5eead4; color:#0f766e;
                  border-radius:6px; padding:6px 10px; margin:8px 0 0; }}
  .overchip {{ font-size:9px; font-weight:700; letter-spacing:.02em; padding:1px 6px;
               border-radius:8px; margin-left:6px; vertical-align:middle;
               background:#fff; color:#6b7280; border:1px solid #d1d5db; cursor:help; }}
  .overcite-note {{ font-size:12px; background:#f8fafc; border:1px solid #cbd5e1; color:#475569;
                    border-radius:6px; padding:6px 10px; margin:8px 0 0; }}
  .datechip {{ font-size:9px; font-weight:700; letter-spacing:.02em; padding:1px 6px;
               border-radius:8px; margin-left:6px; vertical-align:middle;
               background:#fff; color:#6b7280; border:1px solid #d1d5db; cursor:help; }}
  .fbtn.overcitef {{ border-color:#cbd5e1; color:#475569; }}
  .multisource-note {{ font-size:12px; background:#f8fafc; border:1px solid #cbd5e1; color:#475569;
                       border-radius:6px; padding:6px 10px; margin:8px 0 0; }}
  .covset-miss {{ font-size:12px; background:#fffbeb; border:1px solid #fbbf24; color:#b45309;
                  border-radius:6px; padding:6px 10px; margin:8px 0 0; }}
  details.covering {{ font-size:12px; margin:8px 0 0; border:1px solid #e5e7eb;
                      border-radius:6px; padding:4px 8px; background:#fafafa; }}
  details.covering summary {{ cursor:pointer; color:#374151; font-weight:600; }}
  .covset-common {{ font-size:12px; background:#f9fafb; border:1px solid #e5e7eb; color:#6b7280;
                    border-radius:6px; padding:6px 9px; margin:8px 0 0; }}
  .covset-row {{ border-top:1px solid #f3f4f6; padding:6px 0; }}
  .covset-row blockquote {{ margin:4px 0; }}
  .covset-part {{ font-weight:600; color:#166534; }}
  .covset-src {{ font-weight:400; color:#6b7280; font-size:11px; }}
  .covset-foot {{ color:#6b7280; font-size:11px; border-top:1px solid #f3f4f6; padding-top:5px; }}
  details.covspan {{ font-size:12px; margin:6px 0; border:1px solid #e5e7eb; border-radius:4px;
                     padding:3px 8px; background:#fff; }}
  details.covspan summary {{ cursor:pointer; color:#475569; }}
  .hunt-note {{ font-size:12px; background:#f8fafc; border:1px solid #cbd5e1; color:#475569;
                border-radius:6px; padding:6px 10px; margin:8px 0 0; }}
  .compcheck-note {{ font-size:12px; background:#f8fafc; border:1px solid #cbd5e1; color:#475569;
                     border-radius:6px; padding:6px 10px; margin:8px 0 0; }}
  .compcheck-note .judged-text {{ margin:4px 0 0; }}
  .compcheck-tail {{ color:#475569; margin-top:4px; }}
  .compcheck-missing {{ font-size:12px; background:#fffbeb; border:1px solid #fbbf24; color:#b45309;
                        border-radius:6px; padding:6px 10px; margin:6px 0 0; }}
  .shchip {{ font-size:9px; font-weight:700; letter-spacing:.02em; padding:1px 6px;
             border-radius:8px; margin-left:6px; vertical-align:middle;
             background:#fff; color:#6b7280; border:1px solid #d1d5db; cursor:help; }}
  .sh-note {{ font-size:12px; background:#f8fafc; border:1px solid #cbd5e1; color:#475569;
              border-radius:6px; padding:6px 10px; margin:8px 0 0; }}
  .disagreechip {{ font-size:9px; font-weight:700; letter-spacing:.02em; padding:1px 6px;
                   border-radius:8px; margin-left:6px; vertical-align:middle;
                   background:#fff; color:#374151; border:1px solid #9ca3af; cursor:help; }}
  .disagree-note {{ font-size:12px; background:#f8fafc; border:1px solid #9ca3af; color:#374151;
                    border-radius:6px; padding:6px 10px; margin:8px 0 0; }}
  .changed-chip {{ font-size:9px; font-weight:700; padding:1px 6px; border-radius:8px;
                   background:#fff; color:#6b7280; border:1px solid #d1d5db; white-space:nowrap; }}
  sup.changedmark {{ color:#6b7280; font-weight:700; cursor:default; }}
  details.changed-note {{ font-size:12px; color:#475569; background:#f8fafc; border:1px solid #cbd5e1;
                          padding:4px 8px; border-radius:4px; margin-top:6px; }}
  details.changed-note summary {{ cursor:pointer; }}
  .changed-note {{ font-size:12px; color:#475569; background:#f8fafc; border:1px solid #cbd5e1;
                   padding:4px 8px; border-radius:4px; margin-top:6px; }}
  .prev-text {{ margin-top:4px; padding:6px 8px; background:#fff; border-left:3px solid #cbd5e1;
                color:#4b5563; font-style:italic; }}
  .triage {{ display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin-top:10px;
             padding-top:8px; border-top:1px dashed #e5e7eb; cursor:default; }}
  .tlabel {{ font-size:10px; text-transform:uppercase; letter-spacing:.04em; color:#9ca3af; }}
  .tbtn {{ font-size:11px; padding:2px 9px; border:1px solid #d1d5db; border-radius:11px;
           background:#fff; color:#6b7280; cursor:pointer; }}
  .tbtn.on {{ background:#334155; border-color:#334155; color:#fff; }}
  .cbtn {{ font-size:11px; padding:2px 10px; border:1px solid #cbd5e1; border-radius:11px;
           background:#fff; color:#334155; cursor:pointer; font-weight:600; margin-right:4px; }}
  .cbtn.on {{ background:#334155; border-color:#334155; color:#fff; }}
  .fbtn.hchk {{ border-color:#cbd5e1; color:#334155; }}
  .fbtn.hchk.active {{ background:#334155; border-color:#334155; color:#fff; }}
  .tnote {{ flex-basis:100%; font-family:inherit; font-size:12px; padding:5px 8px;
            border:1px solid #d1d5db; border-radius:5px; resize:vertical; color:#374151; }}
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
  .fbtn {{ font-size:12px; padding:3px 10px; border:1px solid #d1d5db; border-radius:12px; background:#fff; cursor:pointer; color:#374151; }}
  .fbtn.active {{ background:#1f2937; color:#fff; border-color:#1f2937; }}
  .meta {{ font-size:11px; color:#9ca3af; }}
  .card-claim {{ font-size:14px; line-height:1.55; }}

  /* ---- Focus view (default; friend feedback #1): the right pane shows ONLY
     the selected claim's card — the list-detail pattern. The all-cards list
     stays one toggle away ("show all cards", persisted like simple/expert).
     !important beats the filter's inline display so the selected card always
     shows; prev/next + arrow keys walk the cards the active filter matches. */
  body.detailview #claimList > .card {{ display:none !important; }}
  body.detailview #claimList > .card.active {{ display:block !important;
      box-shadow:0 1px 2px rgba(0,0,0,.05); border-color:#e5e7eb; cursor:default; }}
  body.detailview #omittedSec {{ display:none; }}
  .detailnav {{ display:none; }}
  body.detailview .detailnav {{ display:flex; align-items:center; gap:10px; margin-bottom:10px; }}
  .navbtn {{ font-size:12px; padding:3px 12px; border:1px solid #d1d5db; border-radius:12px;
             background:#fff; cursor:pointer; color:#374151; }}
  .navbtn:disabled {{ opacity:.35; cursor:default; }}
  .navpos {{ font-size:12px; color:#6b7280; }}
  .navhint {{ font-size:11px; color:#9ca3af; margin-left:auto; }}
  .detail-empty {{ display:none; }}
  body.detailview .detail-empty {{ display:block; border:1px dashed #d1d5db; border-radius:9px;
      padding:26px 18px; color:#6b7280; font-size:13px; background:#fff; text-align:center; }}
  body.detailview.hasactive .detail-empty {{ display:none; }}
  kbd {{ font-family:monospace; font-size:11px; background:#f3f4f6; border:1px solid #d1d5db;
         border-bottom-width:2px; border-radius:4px; padding:0 5px; }}

  /* ---- Simple mode (default): verdict + claim + proof sentences + confidence.
     Everything else — advanced chips, nudge notes, fix box, review triage,
     cosine/method meta — hides until the card's "details & review" button or
     the header's expert-view toggle. Progressive disclosure, nothing removed:
     the full view is one click away and nothing here touches verdicts. */
  .morebtn {{ display:none; font-size:11px; margin-top:10px; padding:2px 10px; border:1px solid #e5e7eb;
              background:#fff; color:#6b7280; border-radius:10px; cursor:pointer; }}
  .morebtn:hover {{ background:#f3f4f6; }}
  body.simple .card .morebtn {{ display:inline-block; }}
  body.simple .card .adv {{ display:none; }}
  body.simple .card.open .adv {{ display:block; }}
  body.simple .card-head .meta {{ display:none; }}
  body.simple .card .sochip, body.simple .card .dcchip, body.simple .card .abchip,
  body.simple .card .partialchip, body.simple .card .overchip, body.simple .card .shchip,
  body.simple .card .disagreechip, body.simple .card .scopechip, body.simple .card .rescuechip,
  body.simple .card .datechip, body.simple .card .kindchip, body.simple .card .changed-chip,
  body.simple .card .ownerchip, body.simple .card .leadin-chip {{ display:none; }}
  body.simple .card.open .sochip, body.simple .card.open .dcchip, body.simple .card.open .abchip,
  body.simple .card.open .partialchip, body.simple .card.open .overchip, body.simple .card.open .shchip,
  body.simple .card.open .disagreechip, body.simple .card.open .scopechip, body.simple .card.open .rescuechip,
  body.simple .card.open .datechip, body.simple .card.open .kindchip, body.simple .card.open .changed-chip,
  body.simple .card.open .ownerchip, body.simple .card.open .leadin-chip {{ display:inline-block; }}
  body.simple .fbtn.partial, body.simple .fbtn.overcitef {{ display:none; }}
  body.simple #saveLocBtn, body.simple #sciBtn {{ display:none; }}
  /* Own cards: the identical "no citation" explainer on every uncited claim is
     the single biggest repetition on real texts (27 of eggs' 54 cards) — in
     simple mode the OWN badge + legend carry it; details restores the text. */
  body.simple .card .evidence.own-note {{ display:none; }}
  body.simple .card.open .evidence.own-note {{ display:block; }}
  .evidence {{ margin-top:10px; padding-top:10px; border-top:1px dashed #e5e7eb; }}
  .ev-label {{ font-size:11px; color:var(--gray); margin-bottom:4px; }}
  .paperlink {{ font-size:11px; color:#2563eb; text-decoration:none; white-space:nowrap; }}
  .paperlink:hover {{ text-decoration:underline; }}
  .srcchip {{ font-size:9px; font-weight:700; letter-spacing:.02em; padding:1px 6px; border-radius:8px; margin-right:5px; }}
  .srcchip.ok {{ background:var(--teal-bg); color:var(--teal); }}
  .srcchip.no {{ background:#e5e7eb; color:#374151; }}
  .combined-note {{ font-size:12px; color:var(--teal); background:var(--teal-bg); padding:4px 8px; border-radius:4px; margin-top:6px; }}
  .unsupp-note {{ font-size:12px; color:#b91c1c; background:#fef2f2; padding:4px 8px; border-radius:4px; margin-top:6px; }}
  .fixbox {{ margin-top:8px; padding:8px 10px; background:#f0fdf4; border:1px solid #bbf7d0; border-radius:6px; }}
  .fixbox blockquote {{ margin:6px 0; }}
  .fix-head {{ font-size:12px; font-weight:600; color:#166534; }}
  .fixchip {{ font-size:11px; font-weight:400; padding:1px 6px; border-radius:8px; margin-left:6px; }}
  .fixchip.ok {{ background:#dcfce7; color:#166534; }}
  .fixchip.warn {{ background:#fef9c3; color:#854d0e; }}
  .fix-changes {{ font-size:12px; color:#4b5563; font-style:italic; margin-top:4px; }}
  .fixcmd {{ margin-top:8px; font-size:12px; color:#6b7280; }}
  .fixcmd code {{ background:#f3f4f6; padding:2px 6px; border-radius:4px; font-size:11px; word-break:break-all; }}
  .borderline-note {{ font-size:12px; color:#854d0e; background:#fefce8; border:1px solid #fde047; padding:4px 8px; border-radius:4px; margin-top:6px; }}
  details.judged {{ margin-top:6px; font-size:12px; }}
  details.judged summary {{ cursor:pointer; color:#6b7280; }}
  .judged-text {{ margin-top:4px; padding:6px 8px; background:#f9fafb; border-left:3px solid #d1d5db; color:#374151; line-height:1.5; }}
  blockquote {{ margin:0; padding:8px 10px; background:#f3f4f6; border-left:3px solid var(--teal); font-size:13px; font-style:italic; }}
  .evidence.reason {{ font-size:12px; color:var(--red); }}
  .copy {{ margin-top:6px; font-size:11px; padding:3px 8px; border:1px solid #d1d5db; background:#fff; border-radius:4px; cursor:pointer; }}

  /* Open-the-source affordance: one link/button per card, opens a new tab */
  .src-actions {{ display:flex; gap:10px; margin-top:10px; flex-wrap:wrap; }}
  .deeplink, .opentext-btn {{ font-size:12px; padding:4px 10px; border:1px solid #334155; background:#334155; color:#fff; border-radius:5px; text-decoration:none; cursor:pointer; font-family:inherit; }}
  .deeplink:hover, .opentext-btn:hover {{ background:#1f2937; }}
  .side-btn {{ font-size:12px; padding:4px 10px; border:1px solid #94a3b8; background:#fff; color:#334155; border-radius:5px; cursor:pointer; font-family:inherit; }}
  .side-btn:hover {{ background:#f1f5f9; }}
  #omittedTail.collapsed {{ display:none; }}
  #omitToggle {{ margin:8px 0; }}
  .warnbanner {{ background:#fef3c7; color:#92400e; font-size:12px; padding:8px 24px; border-bottom:1px solid #fcd34d; }}
  .warnbanner ul {{ margin:4px 0 0; padding-left:20px; }}
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
<body class="simple detailview">
<header>
  <div class="head-row">
    <h1>{_esc(title)}</h1>
    <span>
      <button class="toggle" id="modeToggle" onclick="toggleMode()"
              title="simple view shows verdict + claim + proof sentences + confidence; expert view shows every chip, nudge note and review control on every card">expert view</button>
      <button class="toggle" id="topToggle" onclick="toggleTop()"
              title="collapse everything above the two columns, so the text and cards get the full page">hide header</button>
    </span>
  </div>
  <div class="totals">{totals.get('claims',0)} claims &nbsp;·&nbsp;
    <b class="s">{totals.get('supported',0)} supported</b>{partly_total} &nbsp;·&nbsp;
    <b class="u">{n_uns - n_unverifiable} unsupported</b>{unverifiable_total} &nbsp;·&nbsp;{scoped_total}
    <b style="color:#a5b4fc">{totals.get('own',0)} your own</b> &nbsp;·&nbsp;
    <b class="o">{totals.get('omitted',0)} unused source points</b>{cite_total}{changed_total}{so_total}</div>
  <div class="scopenote">&ldquo;supported&rdquo; means the cited document contains the statement —
    not that the source is strong or the claim is true; &ldquo;your own&rdquo; (uncited) claims were not checked.
    &ldquo;unused source points&rdquo; are things your sources say that your text did not cite — a menu, not errors.</div>
</header>
<div id="topPanel">
{legend_html}
{warn_html}
<div class="modebar" id="modebar"></div>
<div class="coverage">
  <div class="cov-head">
    <div class="cov-title"><b>Source coverage</b> — which sources ended up backing your claims ({n_sources} sources).
      An empty bar is usually structural: the citing claims were judged unsupported, or a co-cited source supplied the evidence — the label on each row says which.</div>
    <button class="toggle" onclick="toggleCoverage(this)">{cov_btn_label}</button>
  </div>
  <div class="cov-bars{cov_collapsed}" id="covBars">{coverage_html}</div>
</div>
{assess_section}
</div>
<div class="layout">
  <div class="doc-wrap">
    <div class="doc-head"><h2>Your text</h2><button class="toggle" onclick="toggleDoc(this)">collapse</button></div>
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
      <div id="omittedSec">
      <h2 class="sec">{_esc(omitted_sec_label)}</h2>{omitted_cards or '<p class="meta">none</p>'}
      </div>
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
  if (HAS_PDF || HAS_TEXT) bar.innerHTML = dotG + 'On each claim: <b>↗</b> opens the cited source in a new tab; <b>⊞ side window</b> opens it in one reused window you can dock on the right (next click swaps its content). PDFs jump to the page, text is highlighted. No server needed.';
  else bar.innerHTML = dotGray + 'No viewable sources — the supporting sentence for each claim is quoted on the left (with a Copy button).';
}})();

let activeId = null;
// Scoped to claim spans + cards: a bare '.active' would also strip the active
// FILTER button, silently resetting the filter to All on every sentence click.
function clearActive() {{ document.querySelectorAll('.claim.active, .card.active').forEach(e => e.classList.remove('active')); }}
function brush(id, from) {{
  // Two-way sync (owner walkthrough item 7): scroll the OPPOSITE panel from the
  // one that was clicked — never the panel under the user's cursor.
  clearActive();
  // A claim may be rendered as several spans (tail-rescue: an indigo lead-in with
  // no id + a verdict-colored tail carrying text-<id>). Light up EVERY span that
  // belongs to the claim, not just text-<id>, so the clicked sentence highlights.
  const spans = document.querySelectorAll('.claim[data-card="card-' + id + '"]');
  spans.forEach(function(s) {{ s.classList.add('active'); }});
  const t = spans[0] || document.getElementById('text-' + id);
  const c = document.getElementById('card-' + id);
  if (c) {{
    if (c.style.display === 'none') {{        // hidden by a verdict filter -> show All
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
function copyText(btn) {{
  cgCopy(btn.getAttribute('data-quote'), btn);
}}
function toggleDoc(btn) {{
  const d = document.getElementById('doc');
  d.classList.toggle('collapsed');
  btn.textContent = d.classList.contains('collapsed') ? 'expand' : 'collapse';
}}
function toggleCoverage(btn) {{
  const b = document.getElementById('covBars');
  b.classList.toggle('collapsed');
  btn.textContent = b.classList.contains('collapsed') ? 'show' : 'hide';
}}
// Master top-panel toggle (owner walkthrough item 5): collapse everything above
// the two columns; the choice persists across runs (a UI preference, not run state).
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
// Simple vs expert view: a UI preference (persists across runs), never run state.
function applyMode(expert) {{
  document.body.classList.toggle('simple', !expert);
  const b = document.getElementById('modeToggle');
  if (b) b.textContent = expert ? 'simple view' : 'expert view';
  try {{ localStorage.setItem('ptui:expert', expert ? '1' : ''); }} catch (e) {{}}
}}
function toggleMode() {{ applyMode(document.body.classList.contains('simple')); }}
try {{ if (localStorage.getItem('ptui:expert') === '1') applyMode(true); }} catch (e) {{}}
// ---- Focus (single-card) vs all-cards view: friend feedback #1. Focus is the
// default; the choice is a UI preference (persists across runs), never run state.
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
// The experimental argument-structure panel starts collapsed in simple view —
// it dominates the page otherwise. One click reopens it; expert users see it
// expanded as before.
(function() {{
  const ab = document.getElementById('assessBody'), at = document.getElementById('assessToggle');
  if (ab && at && document.body.classList.contains('simple') && !ab.classList.contains('collapsed')) {{
    ab.classList.add('collapsed'); at.textContent = 'expand';
  }}
}})();
function toggleMore(ev, btn) {{
  ev.stopPropagation();
  const c = btn.closest('.card');
  const open = c.classList.toggle('open');
  btn.innerHTML = open ? '▾ hide details' : '▸ details &amp; review';
}}
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

function escapeHtml(s) {{
  return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}}

// Sources can open either in a fresh new tab (target '_blank') or in ONE reused
// "side window" you dock on the right. The side window has a fixed name, so every
// side-window click navigates that same window instead of stacking tabs. Per the
// HTML spec, window features are ignored when a named window already exists, so it
// keeps the size/position you give it manually (no re-snapping on each click).
const SIDE_WIN = 'pt_source_side';
const SIDE_FEATURES = 'width=900,height=1024';   // forces a window (not a tab) on first open; ignored on reuse

// Build a self-contained, highlighted HTML document for a text source.
// Exact match first; then whitespace-tolerant (sentences may span the source's
// original newlines — table rows merged in segmentation, wrapped lines, etc.).
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

// Open a text source. target='_blank' -> new tab each time; target=SIDE_WIN -> reused window.
function openTextSource(pid, sentence, snippet, target, features) {{
  const doc = buildTextDoc(pid, sentence, snippet);
  let w = window.open('', target, features || '');
  if (!w) {{ alert('Please allow pop-ups to open the source.'); return; }}
  try {{
    w.document.open(); w.document.write(doc); w.document.close();
  }} catch (e) {{
    // The reused window currently holds a PDF (not script-writable) — navigate it via a blob instead.
    const url = URL.createObjectURL(new Blob([doc], {{type:'text/html'}}));
    w = window.open(url, target, features || '');
  }}
  if (w) w.focus();
}}

// Open a PDF deep-link in the reused side window (native PDF viewer, jumps to #page).
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
// Called after a review-triage toggle (e.g. ✓ checked) so a card that no longer
// matches the active filter (e.g. "Unchecked") leaves the list right away — with
// a short fade so the departure is perceived rather than a silent snap.
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
    logger.info(f"Wrote viewer: {output_path}")
    return output_path
