"""Self-contained HTML viewer for claim-origin traces (Stream B).

A sibling of viewer.py (which it never touches): renders origin_trace.json as a
set of claim cards, each showing the provenance CHAIN the tracer walked —
cited paper -> relay(s) -> origin — with the own/derivative attribution, a
confidence chip, the judged passage, and a link to open each paper. It carries
the same review-and-check discipline as the main viewer: per-claim "trace looks
right / wrong" marks in localStorage (keyed to the run) with a JSON export.

Server-free and persistent: one HTML file, works from file://. Never mutates any
verdict — origin tracing is a nudge, and this viewer only displays/records it.

Entry point:  generate(traces, analysis, output_path, title=..., run_id=...)
  traces   = {claim_id: <chain payload from origin_trace.trace_run>}
  analysis = the run's analysis.json (for each claim's text + markers)
"""

import os
import re
import json
import html
from typing import Any, Dict, Optional

# Visual language borrowed from viewer.py so the two feel like one tool.
TEAL = "#0f766e"
AMBER = "#b45309"
INDIGO = "#6366f1"
GRAY = "#6b7280"

ROLE_LABEL = {"cited": "cited", "relay": "relay", "origin": "origin"}
STOP_LABEL = {
    "primary": "origin reached",
    "max_depth": "stopped: depth limit",
    "low_conf": "stopped: low confidence",
    "unfetchable": "stopped: trail unfetchable",
}


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


def _conf_chip(conf: Optional[float]) -> str:
    if conf is None:
        return ""
    if conf >= 0.85:
        cls, lab = "high", "high"
    elif conf >= 0.6:
        cls, lab = "medium", "medium"
    else:
        cls, lab = "low", "low"
    pct = int(round(conf * 100))
    return f'<span class="confchip {cls}" title="judge confidence">{lab} · {pct}%</span>'


def _node_link(node: Dict[str, Any]) -> str:
    url = node.get("url")
    if not url:
        return ""
    return (f'<a class="paperlink" href="{_esc(url)}" target="_blank" '
            f'rel="noopener">open ↗</a>')


def _chain_node(node: Dict[str, Any], is_last: bool) -> str:
    role = node.get("role", "relay")
    role_cls = role if role in ROLE_LABEL else "relay"
    attribution = node.get("attribution", "")
    if attribution == "own":
        attr_html = '<span class="attrchip own">own assertion</span>'
    elif str(attribution).startswith("cites:"):
        ref = attribution.split("cites:", 1)[1]
        attr_html = f'<span class="attrchip derivative">attributes to {_esc(ref)}</span>'
    else:
        attr_html = f'<span class="attrchip unknown">{_esc(attribution) or "unclear"}</span>'
    passage = node.get("passage") or ""
    reason = node.get("reason") or ""
    passage_html = ""
    if passage:
        passage_html = (
            '<details class="passage"><summary>judged passage</summary>'
            f'<blockquote>{_esc(passage)}</blockquote></details>')
    reason_html = f'<div class="node-reason">{_esc(reason)}</div>' if reason else ""
    connector = "" if is_last else '<div class="connector">↓</div>'
    return f"""
      <div class="node {role_cls}">
        <div class="node-head">
          <span class="rolebadge {role_cls}">{_esc(ROLE_LABEL.get(role, role))}</span>
          <span class="node-title">{_esc(node.get('title') or node.get('paper_id') or '?')}</span>
          {_node_link(node)}
        </div>
        <div class="node-meta">{attr_html}{_conf_chip(node.get('confidence'))}</div>
        {reason_html}
        {passage_html}
      </div>
      {connector}"""


def _claim_card(cid: str, claim: Dict[str, Any], payload: Dict[str, Any]) -> str:
    chain = payload.get("chain", [])
    origin_found = payload.get("origin_found", False)
    stopped = payload.get("stopped_because")
    depth = payload.get("depth", 0)

    if origin_found:
        outbadge = '<span class="badge origin">origin found</span>'
    else:
        outbadge = '<span class="badge noorigin">no origin</span>'
    stop_html = (f'<span class="stopchip {_esc(stopped)}">{_esc(STOP_LABEL.get(stopped, stopped))}</span>'
                 if stopped else "")

    nodes_html = "".join(_chain_node(n, i == len(chain) - 1)
                         for i, n in enumerate(chain))
    claim_text = claim.get("text", "") if claim else ""

    return f"""
    <div class="card" data-origin="{str(origin_found).lower()}" data-stopped="{_esc(stopped)}"
         id="card-{_esc(cid)}">
      <div class="card-head">
        <div class="head-left">
          <span class="claimno">{_esc(cid)}</span>
          {outbadge}{stop_html}
          <span class="depthchip" title="hops walked">depth {_esc(depth)}</span>
        </div>
      </div>
      <div class="card-claim">{_esc(claim_text)}</div>
      <div class="chain">{nodes_html}</div>
      <div class="review" data-cid="{_esc(cid)}">
        <span class="tlabel">Check this trace:</span>
        <button class="tbtn ok" onclick="mark('{_esc(cid)}','ok')">trace looks right</button>
        <button class="tbtn wrong" onclick="mark('{_esc(cid)}','wrong')">trace wrong</button>
        <textarea class="tnote" placeholder="note (optional)"
                  oninput="setNote('{_esc(cid)}', this.value)"></textarea>
      </div>
    </div>"""


def generate(traces: Dict[str, Any], analysis: Dict[str, Any], output_path: str,
             title: str = "Claim origin trace", run_id: Optional[str] = None) -> str:
    """Render traces to a self-contained HTML file. Returns output_path."""
    claims_by_id = {c.get("id"): c for c in (analysis.get("text_claims") or [])}
    run_id = run_id or (analysis.get("metadata", {}) or {}).get("timestamp", "run")

    n = len(traces)
    n_origin = sum(1 for p in traces.values() if p.get("origin_found"))
    n_unfetch = sum(1 for p in traces.values() if p.get("stopped_because") == "unfetchable")
    n_lowconf = sum(1 for p in traces.values() if p.get("stopped_because") == "low_conf")

    cards = "".join(_claim_card(cid, claims_by_id.get(cid), payload)
                    for cid, payload in traces.items())
    if not cards:
        cards = '<p class="meta">No claims traced. Run origin_trace.trace_run() with claim_ids first.</p>'

    filters = "".join(
        f'<button class="fbtn{" active" if key=="all" else ""}" '
        f'data-filter="{key}" onclick="setFilter(this)">{label}</button>'
        for key, label in [
            ("all", "All"),
            ("origin", "Origin found"),
            ("noorigin", "No origin"),
            ("unfetchable", "Unfetchable"),
            ("low_conf", "Low confidence"),
            ("max_depth", "Depth limit"),
        ])

    html_doc = _PAGE.format(
        title=_esc(title),
        run_id=_esc(run_id),
        n=n, n_origin=n_origin, n_unfetch=n_unfetch, n_lowconf=n_lowconf,
        filters=filters,
        cards=cards,
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
  .totals {{ font-size:13px; opacity:.9; }}
  .totals b.o {{ color:#5eead4; }} .totals b.x {{ color:#fca5a5; }} .totals b.w {{ color:#fcd34d; }}
  .scopenote {{ font-size:11px; opacity:.6; margin-top:4px; }}
  .legend {{ font-size:12px; padding:8px 24px; background:#0b1220; color:#cbd5e1;
             border-top:1px solid #1f2937; display:flex; gap:16px; flex-wrap:wrap; }}
  .legend span b {{ color:#fff; }}
  .wrap {{ max-width:900px; margin:0 auto; padding:16px 20px 60px; }}
  .filterbar {{ display:flex; gap:6px; margin:8px 0 14px; flex-wrap:wrap; }}
  .fbtn {{ font-size:12px; padding:3px 10px; border:1px solid #d1d5db; border-radius:12px;
           background:#fff; cursor:pointer; color:#374151; }}
  .fbtn.active {{ background:#1f2937; color:#fff; border-color:#1f2937; }}
  .meta {{ font-size:12px; color:#9ca3af; }}

  .card {{ border:1px solid #e5e7eb; border-left-width:5px; border-left-color:#9ca3af;
           border-radius:8px; padding:12px 14px; margin:10px 0; background:#fff; }}
  .card[data-origin="true"] {{ border-left-color:var(--teal); }}
  .card[data-stopped="unfetchable"] {{ border-left-color:var(--amber); }}
  .card[data-stopped="low_conf"] {{ border-left-color:#eab308; }}
  .card-head {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }}
  .head-left {{ display:flex; align-items:center; gap:7px; flex-wrap:wrap; }}
  .claimno {{ font-size:11px; color:#6b7280; font-family:monospace; }}
  .badge {{ font-size:11px; font-weight:700; padding:2px 8px; border-radius:10px; color:#fff; }}
  .badge.origin {{ background:var(--teal); }}
  .badge.noorigin {{ background:#9ca3af; }}
  .stopchip {{ font-size:10px; font-weight:700; padding:1px 7px; border-radius:9px;
               background:#f3f4f6; color:#6b7280; border:1px solid #e5e7eb; }}
  .stopchip.unfetchable {{ background:var(--amber-bg); color:var(--amber); border-color:#fcd34d; }}
  .stopchip.low_conf {{ background:#fefce8; color:#854d0e; border-color:#fde047; }}
  .stopchip.primary {{ background:var(--teal-bg); color:var(--teal); border-color:#99f6e4; }}
  .depthchip {{ font-size:10px; color:#6b7280; background:#f3f4f6; border:1px solid #e5e7eb;
               border-radius:9px; padding:1px 7px; }}
  .card-claim {{ font-size:14px; line-height:1.5; margin-bottom:10px; }}

  .chain {{ display:flex; flex-direction:column; align-items:stretch; }}
  .node {{ border:1px solid #e5e7eb; border-radius:6px; padding:8px 10px; background:#fafafa; }}
  .node.origin {{ background:var(--teal-bg); border-color:#99f6e4; }}
  .node.relay {{ background:var(--amber-bg); border-color:#fcd34d; }}
  .node.cited {{ background:var(--indigo-bg); border-color:#c7d2fe; }}
  .node-head {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; }}
  .rolebadge {{ font-size:9px; font-weight:700; text-transform:uppercase; letter-spacing:.04em;
                padding:2px 7px; border-radius:8px; color:#fff; }}
  .rolebadge.cited {{ background:var(--indigo); }}
  .rolebadge.relay {{ background:var(--amber); }}
  .rolebadge.origin {{ background:var(--teal); }}
  .node-title {{ font-size:13px; font-weight:600; flex:1; min-width:0; }}
  .node-meta {{ margin-top:5px; display:flex; gap:6px; flex-wrap:wrap; align-items:center; }}
  .attrchip {{ font-size:10px; font-weight:700; padding:1px 7px; border-radius:9px; }}
  .attrchip.own {{ background:var(--teal); color:#fff; }}
  .attrchip.derivative {{ background:#fff; color:var(--amber); border:1px solid #fcd34d; }}
  .attrchip.unknown {{ background:#e5e7eb; color:#374151; }}
  .confchip {{ font-size:9px; font-weight:700; padding:1px 6px; border-radius:8px; border:1px solid; }}
  .confchip.high {{ background:#f0fdf4; color:#166534; border-color:#bbf7d0; }}
  .confchip.medium {{ background:#fefce8; color:#854d0e; border-color:#fde047; }}
  .confchip.low {{ background:#fef2f2; color:#b91c1c; border-color:#fecaca; }}
  .node-reason {{ font-size:12px; color:#4b5563; margin-top:5px; font-style:italic; }}
  details.passage {{ margin-top:6px; font-size:12px; }}
  details.passage summary {{ cursor:pointer; color:#6b7280; }}
  blockquote {{ margin:6px 0 0; padding:7px 10px; background:#fff; border-left:3px solid #d1d5db;
                font-size:12px; color:#374151; line-height:1.5; }}
  .connector {{ text-align:center; color:#9ca3af; font-size:14px; line-height:1.2; margin:2px 0; }}
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
  <div class="totals">{n} claims traced &nbsp;·&nbsp;
    <b class="o">{n_origin} origin found</b> &nbsp;·&nbsp;
    <b class="x">{n_unfetch} unfetchable</b> &nbsp;·&nbsp;
    <b class="w">{n_lowconf} low confidence</b></div>
  <div class="scopenote">A trace follows a supported claim from the paper you cited up to the
    primary source. "relay" = the cited paper attributes the claim to another work; "origin" =
    the paper that first asserts it. A trace is a <b>nudge, never a verdict</b> — it changes nothing.</div>
</header>
<div class="legend">
  <span><b style="color:#a5b4fc">cited</b> — what the author cited</span>
  <span><b style="color:#fcd34d">relay</b> — passes the claim along (derivative)</span>
  <span><b style="color:#5eead4">origin</b> — the primary source (own assertion)</span>
</div>
<div class="wrap">
  <div class="exportbar">
    <button class="rbtn" onclick="downloadReview()">Download origin-review.json</button>
    <span class="rev-count" id="revCount"></span>
  </div>
  <div class="filterbar">{filters}</div>
  <div id="claimList">{cards}</div>
</div>
<script>
const RUN_ID = {run_id_json};
const REVIEW_KEY = 'origintrace_review_' + RUN_ID;
let review = {{}};
try {{ review = JSON.parse(localStorage.getItem(REVIEW_KEY) || '{{}}'); }} catch (e) {{ review = {{}}; }}

function save() {{ localStorage.setItem(REVIEW_KEY, JSON.stringify(review)); updateCount(); }}
function updateCount() {{
  const n = Object.keys(review).filter(k => review[k] && review[k].status).length;
  document.getElementById('revCount').textContent = n ? (n + ' marked') : '';
}}
function mark(cid, status) {{
  review[cid] = review[cid] || {{}};
  review[cid].status = (review[cid].status === status) ? null : status;
  const card = document.getElementById('card-' + cid);
  card.querySelector('.tbtn.ok').classList.toggle('on', review[cid].status === 'ok');
  card.querySelector('.tbtn.wrong').classList.toggle('on', review[cid].status === 'wrong');
  save();
}}
function setNote(cid, text) {{ review[cid] = review[cid] || {{}}; review[cid].note = text; save(); }}
function downloadReview() {{
  const blob = new Blob([JSON.stringify(review, null, 2)], {{type: 'application/json'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = 'origin-review.json'; a.click();
}}
function setFilter(btn) {{
  document.querySelectorAll('.fbtn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  const f = btn.dataset.filter;
  document.querySelectorAll('.card').forEach(card => {{
    let show = true;
    if (f === 'origin') show = card.dataset.origin === 'true';
    else if (f === 'noorigin') show = card.dataset.origin !== 'true';
    else if (f === 'unfetchable') show = card.dataset.stopped === 'unfetchable';
    else if (f === 'low_conf') show = card.dataset.stopped === 'low_conf';
    else if (f === 'max_depth') show = card.dataset.stopped === 'max_depth';
    card.style.display = show ? '' : 'none';
  }});
}}
// restore saved marks
(function() {{
  for (const cid in review) {{
    const card = document.getElementById('card-' + cid);
    if (!card || !review[cid]) continue;
    if (review[cid].status === 'ok') card.querySelector('.tbtn.ok').classList.add('on');
    if (review[cid].status === 'wrong') card.querySelector('.tbtn.wrong').classList.add('on');
    if (review[cid].note) card.querySelector('.tnote').value = review[cid].note;
  }}
  updateCount();
}})();
</script>
</body></html>"""
