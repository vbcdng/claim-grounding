#!/usr/bin/env python3
"""Task #19, question q8: a single self-contained page showing the two benchmark
rows that define the proposed second column of the public results table, so the
author can read them and rule without opening four files.

Everything on the page comes off disk: the answer key
(data/citation_integrity/batch_dev_fresh50/ci_ground_truth.json), our own run of
the same text (data/citation_integrity/batch_dev_fresh50_run_gemma_0802/
analysis.json) and the two source texts. No model calls, no network.

    venv/bin/python3 benchmarks/task19_q8_review_page.py
    -> data/task19_q8_review/q8_rows.html
"""
import html
import json
import re
from pathlib import Path

BATCH = Path("data/citation_integrity/batch_dev_fresh50")
RUN = Path("data/citation_integrity/batch_dev_fresh50_run_gemma_0802")
OUT = Path("data/task19_q8_review/q8_rows.html")
ROWS = ["cidev0031", "cidev0034"]

# Words whose presence in the source the reader will want to check for herself,
# one list per row: the pieces of the missing part that carry its meaning.
PROBE = {"cidev0031": ["12%", "12-50", "12–50", "acute-on-chronic"],
         "cidev0034": ["safety", "safe", "protect"]}


def esc(s):
    return html.escape(str(s or ""))


def sentences_with(text, needle, limit=4):
    """Every sentence of the source that contains the needle, with the needle
    marked, so the reader can see the context rather than a bare hit count."""
    out = []
    for raw in re.split(r"(?<=[.!?])\s+", text):
        if needle.lower() in raw.lower():
            s = " ".join(raw.split())
            if len(s) > 400:
                i = s.lower().find(needle.lower())
                s = "…" + s[max(0, i - 180):i + 180] + "…"
            out.append(s)
        if len(out) >= limit:
            break
    return out


def row_block(key, gt, claim, source_text):
    cc = claim.get("component_check") or {}
    found, missing = cc.get("found") or [], cc.get("missing") or []
    ev_by_comp = {x.get("component"): x for x in (cc.get("evidence") or [])}

    found_html = ""
    for p in found:
        x = ev_by_comp.get(p) or {}
        quote = x.get("sentence") or ""
        found_html += (f'<div class="part ok"><b>Found in the source:</b> {esc(p)}'
                       + (f'<blockquote>{esc(quote)}</blockquote>' if quote else "")
                       + '</div>')
    missing_html = ""
    for p in missing:
        missing_html += (f'<div class="part no"><b>Not found in the source:</b> '
                         f'{esc(p)}</div>')

    key_ev = "".join(f"<li>{esc(s)}</li>" for s in (gt.get("evidence_segments") or []))
    probes = ""
    for needle in PROBE.get(key, []):
        hits = sentences_with(source_text, needle)
        if hits:
            probes += (f'<div class="probe"><b>The words &ldquo;{esc(needle)}&rdquo; do appear '
                       f'in the source, here:</b><ul>'
                       + "".join(f"<li>{esc(h)}</li>" for h in hits) + "</ul></div>")
        else:
            probes += (f'<div class="probe none">The words &ldquo;{esc(needle)}&rdquo; do not '
                       f'appear anywhere in the source text.</div>')

    return f"""
    <section class="row">
      <h2>Row <code>fresh50:{esc(key)}</code> <span class="cardno">(card {esc(claim.get('id'))}
        of the 2026-08-02 run)</span></h2>

      <h3>The sentence, as the writer wrote it</h3>
      <blockquote class="claim">{esc(gt.get('claim_text'))}</blockquote>

      <h3>What our tool decided</h3>
      <p>It printed a red card: <b>{esc(claim.get('verdict'))}</b>. Its own stated reason was:</p>
      <blockquote class="reason">{esc(claim.get('reason'))}</blockquote>
      {found_html}
      {missing_html}

      <h3>What the answer key says</h3>
      <p>The key labels this citation <b>{esc(gt.get('label'))}</b>, which means it counts our
      red card as a false alarm. These are the sentences the key itself offers as the proof:</p>
      <ul class="keyev">{key_ev}</ul>

      <h3>Check the source yourself</h3>
      {probes}
      <details><summary>The whole source text ({len(source_text):,} characters) — use your
        browser's find box inside it</summary>
        <pre>{esc(source_text)}</pre></details>
    </section>"""


def main():
    gt = json.loads((BATCH / "ci_ground_truth.json").read_text())["claims"]
    analysis = json.loads((RUN / "analysis.json").read_text())
    by_marker = {}
    for c in analysis["text_claims"]:
        for m in (c.get("markers") or []):
            by_marker[m] = c

    blocks = ""
    for key in ROWS:
        src = (BATCH / "sources" / f"{key}.txt").read_text(errors="replace")
        blocks += row_block(key, gt[key], by_marker[key], src)

    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Question q8 — the two rows behind the second column</title>
<style>
 body {{ font-family:-apple-system,Segoe UI,Roboto,sans-serif; margin:0 auto; max-width:900px;
        padding:24px; color:#1f2937; line-height:1.65; background:#f9fafb; }}
 h1 {{ font-size:24px; }} h2 {{ font-size:19px; margin-top:34px; }}
 h3 {{ font-size:15px; color:#374151; margin:20px 0 6px; }}
 section.row {{ background:#fff; border:1px solid #e5e7eb; border-radius:8px;
                padding:4px 20px 20px; margin:26px 0; }}
 blockquote {{ margin:8px 0; padding:9px 12px; background:#f3f4f6; border-left:3px solid #9ca3af;
               font-size:14px; }}
 blockquote.claim {{ border-left-color:#0f766e; font-style:italic; }}
 blockquote.reason {{ border-left-color:#b91c1c; background:#fef2f2; }}
 .part {{ margin:8px 0; padding:8px 12px; border-radius:6px; font-size:14px; }}
 .part.ok {{ background:#ecfdf5; border-left:3px solid #10b981; }}
 .part.no {{ background:#fffbeb; border-left:3px solid #f59e0b; }}
 .part blockquote {{ background:#fff; }}
 .probe {{ margin:8px 0; padding:8px 12px; background:#eff6ff; border-left:3px solid #3b82f6;
           font-size:14px; }}
 .probe.none {{ background:#f8fafc; border-left-color:#cbd5e1; }}
 .probe ul, ul.keyev {{ margin:6px 0 0; padding-left:22px; font-size:14px; }}
 pre {{ white-space:pre-wrap; font-size:12px; background:#f8fafc; padding:12px;
        border:1px solid #e5e7eb; max-height:420px; overflow:auto; }}
 code {{ background:#f3f4f6; padding:1px 5px; border-radius:4px; }}
 .cardno {{ font-size:13px; font-weight:400; color:#6b7280; }}
 table {{ border-collapse:collapse; margin:10px 0; font-size:14px; background:#fff; }}
 th, td {{ border:1px solid #d1d5db; padding:6px 10px; text-align:left; }}
 .intro {{ background:#fff; border:1px solid #e5e7eb; border-radius:8px; padding:4px 20px 18px; }}
</style></head><body>
<h1>Question q8 — the two rows behind the proposed second column</h1>

<div class="intro">
<h3>Words used on this page</h3>
<ul>
 <li><b>The answer key.</b> A published list, made by other researchers, that says for each
     cited sentence whether its citation is sound. We did not write it and cannot change it.</li>
 <li><b>A red card.</b> Our tool's output when it cannot find, in the cited source, everything
     the sentence asserts.</li>
 <li><b>A false alarm.</b> A red card on a sentence the answer key calls sound. Counting these
     is how a public table would compare judge models.</li>
</ul>

<h3>What you are deciding</h3>
<p>Both rows below are sentences where our tool printed a red card, the answer key says the
citation is fine, and you already ruled — in the hand reading of 2026-08-02 — that the tool was
asked an unfair question. The proposal is that a public results table stops reporting one single
false-alarm number and reports two instead: <b>&ldquo;misread the source&rdquo;</b>, where the
tool's stated reason is simply wrong about the source, and <b>&ldquo;strict but
defensible&rdquo;</b>, where the reason is factually true about the source and the disagreement
is about how strictly a citation should be read. On these two rows the difference looks like
this:</p>
<table>
 <tr><th>Table with one number</th><th>Table with two numbers</th></tr>
 <tr><td>false alarms: 2 of 2 — reads as two mistakes by the model</td>
     <td>misread the source: 0 &nbsp;·&nbsp; strict but defensible: 2 — reads as two strict
         readings, both of which you can check below</td></tr>
</table>
<p>Read each row and decide whether the second column tells the truth about it. Your yes or no
is the only thing still blocking task&nbsp;#10, the public results page.</p>
</div>
{blocks}
<section class="row">
 <h2>How this connects to the rest</h2>
 <p>Task&nbsp;#19, the card this page belongs to, is about honesty on the screen: a card must
 not hide from the writer what was proven and what was not. This question is the same honesty
 one level up, in a table meant for other people: a reader choosing a judge model should be
 able to see whether a red card came from a model misreading a paper or from the model reading
 a citation strictly. The strict rows will thin out on their own as task&nbsp;#15 re-labels the
 benchmark under the strict rule, so the second column is expected to shrink over time rather
 than grow.</p>
</section>
</body></html>"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page)
    print(f"wrote {OUT} ({len(page):,} characters)")


if __name__ == "__main__":
    main()
