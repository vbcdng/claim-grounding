"""Self-contained HTML viewer for snowball search results (Stream B).

A sibling of viewer.py / origin_viewer.py (neither of which it touches): renders a
paper_search.snowball() result as a ranked list of candidate papers, each showing
its relevance to the target, the provenance PATH the walk took to reach it
(seed → … → this paper), why it was picked, and a link to open it. It carries the
same review-and-check discipline as the other viewers: per-candidate
"pursue / skip" marks in localStorage (keyed to the run) with a JSON export
(snowball-review.json) that Stream D can feed back into the "find new sources" loop.

Server-free and persistent: one HTML file, works from file://. Snowball is a
discovery nudge — this viewer only displays candidates and records the reviewer's
picks; it never fetches, downloads, or changes any verdict.

Entry point:  generate(result, output_path, title=..., run_id=...)
  result = the dict returned by paper_search.snowball()
"""

import os
import json
import html
import hashlib
from typing import Any, Dict, List, Optional

# Visual language borrowed from viewer.py so the tools feel like one.
STATUS_BANNER = {
    "search_failed": ("Seed search failed (rate-limited or offline) — this is a "
                      "transient failure, not an empty field. Re-run to retry.", "warn"),
    "no_seeds": ("The keyword search ran but matched no papers relevant to the "
                 "target. Try broader or different keywords.", "info"),
    "empty_query": ("No keywords were given, so no search was run.", "info"),
}


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


def _rel_band(rel: Optional[float]) -> str:
    r = rel or 0.0
    if r >= 0.6:
        return "high"
    if r >= 0.4:
        return "medium"
    return "low"


def _rel_chip(rel: Optional[float]) -> str:
    band = _rel_band(rel)
    val = f"{(rel or 0.0):.2f}"
    return (f'<span class="relchip {band}" title="SPECTER cosine to the target">'
            f'relevance {val}</span>')


def _found_via(path: List[str], self_id: Optional[str]) -> str:
    if not path:
        return ""
    steps = []
    for i, pid in enumerate(path):
        last = (i == len(path) - 1)
        cls = "via-self" if last else "via-hop"
        label = "this paper" if (last and pid == self_id) else _esc(pid)
        steps.append(f'<span class="{cls}">{label}</span>')
    return '<div class="foundvia"><span class="via-label">reached via</span>' \
           + '<span class="via-arrow">→</span>'.join(steps) + '</div>'


def _candidate_card(c: Dict[str, Any]) -> str:
    pid = c.get("paper_id")
    path = c.get("found_via") or []
    hops = max(0, len(path) - 1)
    is_seed = hops == 0
    rel = c.get("relevance")
    url = c.get("url")
    link = (f'<a class="paperlink" href="{_esc(url)}" target="_blank" rel="noopener">open ↗</a>'
            if url else "")
    year = c.get("year")
    yearchip = f'<span class="yearchip">{_esc(year)}</span>' if year else ""
    kindchip = ('<span class="kindchip seed">seed</span>' if is_seed
                else f'<span class="kindchip hop">{hops}-hop</span>')
    reason = c.get("reason") or ""
    reason_html = f'<div class="reason">{_esc(reason)}</div>' if reason else ""
    abstract = c.get("abstract") or ""
    abstract_html = ""
    if abstract:
        abstract_html = ('<details class="abs"><summary>abstract</summary>'
                         f'<blockquote>{_esc(abstract)}</blockquote></details>')
    return f"""
    <div class="card" data-band="{_rel_band(rel)}" data-seed="{str(is_seed).lower()}"
         data-hops="{hops}" id="card-{_esc(pid)}">
      <div class="card-head">
        <div class="head-left">
          {kindchip}{_rel_chip(rel)}{yearchip}
        </div>
        {link}
      </div>
      <div class="card-title">{_esc(c.get('title') or pid or '?')}</div>
      {reason_html}
      {_found_via(path, pid)}
      {abstract_html}
      <div class="review" data-pid="{_esc(pid)}">
        <span class="tlabel">Worth pursuing?</span>
        <button class="tbtn ok" onclick="mark('{_esc(pid)}','pursue')">pursue</button>
        <button class="tbtn wrong" onclick="mark('{_esc(pid)}','skip')">skip</button>
        <textarea class="tnote" placeholder="note (optional)"
                  oninput="setNote('{_esc(pid)}', this.value)"></textarea>
      </div>
    </div>"""


def generate(result: Dict[str, Any], output_path: str,
             title: str = "Snowball search", run_id: Optional[str] = None) -> str:
    """Render a snowball() result to a self-contained HTML file. Returns output_path."""
    candidates = result.get("candidates") or []
    seeds = result.get("seeds") or []
    edges = result.get("edges") or []
    status = result.get("status", "ok")
    target = result.get("target", "")
    max_depth = result.get("max_depth", 0)
    # Stable across processes (hash() isn't) so the review localStorage key —
    # and thus a reviewer's saved picks — survive regenerating the same run.
    run_id = run_id or ("snowball_"
                        + hashlib.sha1((target or "").encode("utf-8")).hexdigest()[:10])

    n = len(candidates)
    n_seed = sum(1 for c in candidates if len(c.get("found_via") or []) <= 1)
    n_followed = n - n_seed

    banner = ""
    if status in STATUS_BANNER:
        msg, kind = STATUS_BANNER[status]
        banner = f'<div class="banner {kind}">{_esc(msg)}</div>'

    cards = "".join(_candidate_card(c) for c in candidates)
    if not cards:
        cards = ('<p class="meta">No candidates. '
                 'Run paper_search.snowball(target, keywords) to populate this view.</p>')

    filters = "".join(
        f'<button class="fbtn{" active" if key=="all" else ""}" '
        f'data-filter="{key}" onclick="setFilter(this)">{label}</button>'
        for key, label in [
            ("all", "All"),
            ("seed", "Seeds"),
            ("followed", "Followed"),
            ("high", "High relevance"),
        ])

    html_doc = _PAGE.format(
        title=_esc(title),
        target=_esc(target),
        n=n, n_seed=n_seed, n_followed=n_followed,
        n_edges=len(edges), n_seeds=len(seeds), max_depth=_esc(max_depth),
        banner=banner, filters=filters, cards=cards,
        run_id_json=json.dumps(run_id),
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_doc)
    return output_path


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ --teal:#0f766e; --teal-bg:#ccfbf1; --amber:#b45309; --amber-bg:#fef3c7;
           --indigo:#6366f1; --indigo-bg:#eef2ff; --gray:#6b7280; --red:#b91c1c; }}
  * {{ box-sizing:border-box; }}
  body {{ font-family:-apple-system,Segoe UI,Roboto,sans-serif; margin:0; color:#1f2937;
         background:#f9fafb; }}
  header {{ background:#111827; color:#fff; padding:12px 24px; }}
  header h1 {{ margin:0 0 4px; font-size:17px; }}
  .target {{ font-size:13px; opacity:.92; margin:2px 0 6px; font-style:italic; }}
  .totals {{ font-size:13px; opacity:.9; }}
  .totals b.o {{ color:#5eead4; }} .totals b.w {{ color:#fcd34d; }}
  .scopenote {{ font-size:11px; opacity:.6; margin-top:4px; }}
  .wrap {{ max-width:900px; margin:0 auto; padding:16px 20px 60px; }}
  .banner {{ font-size:13px; padding:9px 12px; border-radius:6px; margin:12px 0; }}
  .banner.warn {{ background:var(--amber-bg); color:var(--amber); border:1px solid #fcd34d; }}
  .banner.info {{ background:var(--indigo-bg); color:#3730a3; border:1px solid #c7d2fe; }}
  .filterbar {{ display:flex; gap:6px; margin:8px 0 14px; flex-wrap:wrap; }}
  .fbtn {{ font-size:12px; padding:3px 10px; border:1px solid #d1d5db; border-radius:12px;
           background:#fff; cursor:pointer; color:#374151; }}
  .fbtn.active {{ background:#1f2937; color:#fff; border-color:#1f2937; }}
  .meta {{ font-size:12px; color:#9ca3af; }}

  .card {{ border:1px solid #e5e7eb; border-left-width:5px; border-left-color:#9ca3af;
           border-radius:8px; padding:12px 14px; margin:10px 0; background:#fff; }}
  .card[data-band="high"] {{ border-left-color:var(--teal); }}
  .card[data-band="medium"] {{ border-left-color:var(--amber); }}
  .card-head {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; }}
  .head-left {{ display:flex; align-items:center; gap:7px; flex-wrap:wrap; }}
  .kindchip {{ font-size:10px; font-weight:700; padding:2px 8px; border-radius:9px; }}
  .kindchip.seed {{ background:var(--indigo); color:#fff; }}
  .kindchip.hop {{ background:#f3f4f6; color:#6b7280; border:1px solid #e5e7eb; }}
  .relchip {{ font-size:10px; font-weight:700; padding:1px 7px; border-radius:9px; border:1px solid; }}
  .relchip.high {{ background:var(--teal-bg); color:var(--teal); border-color:#99f6e4; }}
  .relchip.medium {{ background:var(--amber-bg); color:var(--amber); border-color:#fcd34d; }}
  .relchip.low {{ background:#f3f4f6; color:#6b7280; border-color:#e5e7eb; }}
  .yearchip {{ font-size:10px; color:#6b7280; background:#f3f4f6; border:1px solid #e5e7eb;
               border-radius:9px; padding:1px 7px; }}
  .card-title {{ font-size:14px; font-weight:600; line-height:1.45; margin-bottom:6px; }}
  .reason {{ font-size:12px; color:#4b5563; font-style:italic; margin-bottom:6px; }}
  .foundvia {{ font-size:11px; color:#6b7280; display:flex; gap:5px; align-items:center;
               flex-wrap:wrap; margin-bottom:4px; }}
  .via-label {{ text-transform:uppercase; letter-spacing:.04em; font-size:9px; color:#9ca3af; }}
  .via-hop {{ font-family:monospace; background:#f3f4f6; border-radius:4px; padding:1px 5px; }}
  .via-self {{ font-family:monospace; background:var(--teal-bg); color:var(--teal);
               border-radius:4px; padding:1px 5px; font-weight:700; }}
  .via-arrow {{ color:#9ca3af; }}
  details.abs {{ margin-top:4px; font-size:12px; }}
  details.abs summary {{ cursor:pointer; color:#6b7280; }}
  blockquote {{ margin:6px 0 0; padding:7px 10px; background:#fafafa; border-left:3px solid #d1d5db;
                font-size:12px; color:#374151; line-height:1.5; }}
  .paperlink {{ font-size:11px; color:#2563eb; text-decoration:none; white-space:nowrap; }}
  .paperlink:hover {{ text-decoration:underline; }}

  .review {{ display:flex; align-items:center; gap:7px; flex-wrap:wrap; margin-top:12px;
             padding-top:9px; border-top:1px dashed #e5e7eb; }}
  .tlabel {{ font-size:10px; text-transform:uppercase; letter-spacing:.04em; color:#9ca3af; }}
  .tbtn {{ font-size:11px; padding:2px 10px; border:1px solid #d1d5db; border-radius:11px;
           background:#fff; color:#6b7280; cursor:pointer; }}
  .tbtn.ok.on {{ background:var(--teal); border-color:var(--teal); color:#fff; }}
  .tbtn.wrong.on {{ background:var(--red); border-color:var(--red); color:#fff; }}
  .tnote {{ flex-basis:100%; font-family:inherit; font-size:12px; padding:5px 8px;
            border:1px solid #d1d5db; border-radius:5px; resize:vertical; color:#374151; }}
  .exportbar {{ position:sticky; top:0; z-index:5; background:#f9fafb; padding:8px 0;
                display:flex; gap:10px; align-items:center; }}
  .rbtn {{ font-size:12px; padding:5px 12px; border:1px solid #7c3aed; background:#7c3aed;
           color:#fff; border-radius:5px; cursor:pointer; font-family:inherit; }}
  .rbtn:hover {{ background:#6d28d9; }}
  .rev-count {{ font-size:12px; color:#4c1d95; font-weight:600; }}
</style></head>
<body>
<header>
  <h1>{title}</h1>
  <div class="target">target: {target}</div>
  <div class="totals">{n} candidates &nbsp;·&nbsp;
    <b class="o">{n_seed} seeds</b> &nbsp;·&nbsp;
    <b class="w">{n_followed} followed</b> &nbsp;·&nbsp;
    {n_edges} citation edges &nbsp;·&nbsp; depth {max_depth}</div>
  <div class="scopenote">A snowball walk seeds on a keyword search, then follows the best
    leads' references and citations outward, ranking every paper by relevance to the target.
    These are <b>candidate sources to review</b> — a discovery nudge, never a verdict; nothing
    here has been fetched or grounded.</div>
</header>
<div class="wrap">
  {banner}
  <div class="exportbar">
    <button class="rbtn" onclick="downloadReview()">Download snowball-review.json</button>
    <span class="rev-count" id="revCount"></span>
  </div>
  <div class="filterbar">{filters}</div>
  <div id="candList">{cards}</div>
</div>
<script>
const RUN_ID = {run_id_json};
const REVIEW_KEY = 'snowball_review_' + RUN_ID;
let review = {{}};
try {{ review = JSON.parse(localStorage.getItem(REVIEW_KEY) || '{{}}'); }} catch (e) {{ review = {{}}; }}

function save() {{ localStorage.setItem(REVIEW_KEY, JSON.stringify(review)); updateCount(); }}
function updateCount() {{
  const n = Object.keys(review).filter(k => review[k] && review[k].status).length;
  document.getElementById('revCount').textContent = n ? (n + ' marked') : '';
}}
function mark(pid, status) {{
  review[pid] = review[pid] || {{}};
  review[pid].status = (review[pid].status === status) ? null : status;
  const card = document.getElementById('card-' + pid);
  card.querySelector('.tbtn.ok').classList.toggle('on', review[pid].status === 'pursue');
  card.querySelector('.tbtn.wrong').classList.toggle('on', review[pid].status === 'skip');
  save();
}}
function setNote(pid, text) {{ review[pid] = review[pid] || {{}}; review[pid].note = text; save(); }}
function downloadReview() {{
  const blob = new Blob([JSON.stringify(review, null, 2)], {{type: 'application/json'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = 'snowball-review.json'; a.click();
}}
function setFilter(btn) {{
  document.querySelectorAll('.fbtn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  const f = btn.dataset.filter;
  document.querySelectorAll('.card').forEach(card => {{
    let show = true;
    if (f === 'seed') show = card.dataset.seed === 'true';
    else if (f === 'followed') show = card.dataset.seed !== 'true';
    else if (f === 'high') show = card.dataset.band === 'high';
    card.style.display = show ? '' : 'none';
  }});
}}
// restore saved marks
(function() {{
  for (const pid in review) {{
    const card = document.getElementById('card-' + pid);
    if (!card || !review[pid]) continue;
    if (review[pid].status === 'pursue') card.querySelector('.tbtn.ok').classList.add('on');
    if (review[pid].status === 'skip') card.querySelector('.tbtn.wrong').classList.add('on');
    if (review[pid].note) card.querySelector('.tnote').value = review[pid].note;
  }}
  updateCount();
}})();
</script>
</body></html>"""
