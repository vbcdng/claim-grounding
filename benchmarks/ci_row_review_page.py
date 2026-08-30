#!/usr/bin/env python3
"""Build a self-contained HTML page for hand-checking specific Citation-Integrity
rows — the author reading the real text and ruling on it, not a model.

Written 2026-08-01 so the author can re-check the rows where the tool printed a
red card and the question is whether the tool was RIGHT (the literature-level
class, the ranges already ruled on, and two rows whose published label looks
doubtful). Pure: no API, no network, reads only what is already on disk.

  python3 benchmarks/ci_row_review_page.py \
      --ground-truth data/citation_integrity/batch_dev_pilot100/ci_ground_truth.json \
      --rows cidev0031,cidev0074,cidev0086 \
      --out data/citation_integrity/review_2026-08-01/rows_to_check.html

Each row block shows: the citing paragraph with the exact span under test
highlighted, the marker tokens the benchmark recorded (so multi-citation rows
are visible as such), the published label, the annotator's verbatim proof
sentences from the cited paper, every arm's verdict and reason, and a link to
the cited paper's full text.

NOT a grading packet and NOT for raters: it shows the labels, on purpose.
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

ARMS = [("incumbent", "batch_dev_pilot100_run"),
        ("qwen3.7 thinking", "batch_dev_pilot100_run_qwen37"),
        ("gemma V2 prompt", "batch_dev_pilot100_run_gemma_v2"),
        ("gemma production prompt", "batch_dev_pilot100_run_gemma_prod"),
        ("qwen3.7 thought-free", "batch_dev_pilot100_run_qwen37_nothink")]

CIT_TOKENS = ("<|cit|>", "<|multi_cit|>", "<|other_cit|>")


def _citation_class(span):
    n_multi = span.count("<|multi_cit|>")
    n_other = span.count("<|other_cit|>")
    if n_multi and n_other:
        return f"one of several at this spot, plus {n_other} more elsewhere in the span"
    if n_multi:
        return "one of several citations at this spot"
    if n_other:
        return f"alone at this spot, but {n_other} other citation(s) elsewhere in the span"
    return "the only citation in the span"


def _strip_tokens(text):
    for t in CIT_TOKENS:
        text = text.replace(t, "")
    return re.sub(r"\s+", " ", text).strip()


def _highlight(paragraph, span):
    """Mark the span inside its paragraph. Falls back to showing them apart."""
    bare_par = _strip_tokens(paragraph)
    bare_span = _strip_tokens(span)
    i = bare_par.find(bare_span)
    if i < 0 or not bare_span:
        return None, html.escape(bare_par)
    return (html.escape(bare_par[:i])
            + '<mark>' + html.escape(bare_span) + '</mark>'
            + html.escape(bare_par[i + len(bare_span):])), None


def _source_path(split, ref):
    base = os.path.join(ROOT, "data", "citation_integrity", "repo", "Data",
                        "annotations_extracted", "annotations",
                        {"dev": "Dev", "train": "Train", "test": "Test"}[split],
                        "references")
    hits = glob.glob(os.path.join(base, f"{ref}*"))
    return hits[0] if hits else None


def _arm_rows(marker):
    """(arm label, verdict, method, reason) for the claim carrying `marker`."""
    out = []
    for label, d in ARMS:
        path = os.path.join(ROOT, "data", "citation_integrity", d, "analysis.json")
        if not os.path.exists(path):
            continue
        data = json.load(open(path))
        for c in data.get("text_claims", []):
            if marker in (c.get("markers") or []):
                comp = c.get("component_check") or {}
                out.append((label, c.get("verdict"), c.get("method"),
                            c.get("reason") or "", comp.get("found") or [],
                            comp.get("missing") or []))
                break
    return out


CSS = """
body{font:16px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;max-width:60rem;
margin:2rem auto;padding:0 1.2rem;color:#1a1a1a;background:#fff}
h1{font-size:1.7rem} h2{font-size:1.25rem;margin-top:2.5rem;
border-top:2px solid #ddd;padding-top:1.2rem}
mark{background:#ffe9a8;padding:.1em 0}
.q{background:#eef4ff;border-left:4px solid #3b6fd4;padding:.8rem 1rem;margin:1rem 0}
.meta{color:#555;font-size:.9rem}
table{border-collapse:collapse;width:100%;margin:1rem 0;font-size:.9rem}
th,td{border:1px solid #ddd;padding:.4rem .6rem;text-align:left;vertical-align:top}
th{background:#f5f5f5}
.v-unsupported{color:#b3261e;font-weight:600}
.v-supported{color:#1a7f37;font-weight:600}
blockquote{margin:.6rem 0;padding:.5rem .9rem;background:#fafafa;
border-left:3px solid #bbb}
.label{display:inline-block;background:#222;color:#fff;padding:.1rem .5rem;
border-radius:3px;font-size:.8rem}
footer{margin-top:3rem;color:#666;font-size:.85rem}
@media(prefers-color-scheme:dark){body{background:#161616;color:#e8e8e8}
th{background:#222}th,td{border-color:#3a3a3a}blockquote{background:#1e1e1e;
border-left-color:#555}.q{background:#1b2436;border-left-color:#5b8def}
mark{background:#5c4a00;color:#fff}h2{border-top-color:#333}}
"""


def build(gt_path, row_ids, out_path, questions):
    gt = json.load(open(gt_path))
    claims = gt["claims"]
    parts = [f"<style>{CSS}</style>",
             "<h1>Citation-Integrity rows to check by hand</h1>",
             "<p class='meta'>Built 2026-08-01 from "
             f"<code>{html.escape(os.path.relpath(gt_path, ROOT))}</code>. "
             "Every red card below is on a row the benchmark labelled ACCURATE "
             "or whose label is in doubt. The question in blue is what the tool "
             "team needs ruled.</p>"]
    missing = []
    for rid in row_ids:
        row = claims.get(rid)
        if row is None:
            missing.append(rid)
            continue
        span = row.get("annotated_span", "")
        par = row.get("citing_paragraph", "")
        marked, plain = _highlight(par, span)
        parts.append(f"<h2 id='{rid}'>{rid} "
                     f"<span class='label'>{html.escape(row.get('label',''))}</span></h2>")
        q = questions.get(rid)
        if q:
            parts.append(f"<div class='q'><strong>What I need from you:</strong> "
                         f"{html.escape(q)}</div>")
        parts.append("<p class='meta'>Citation position: "
                     f"{html.escape(_citation_class(span))}. "
                     f"Span is {row.get('span_words')} words"
                     + (", a whole sentence." if row.get("span_is_full_sentence")
                        else ", a clause cut out of a longer sentence.") + "</p>")
        parts.append("<h3>The citing paragraph, with the checked span highlighted</h3>")
        if marked:
            parts.append(f"<blockquote>{marked}</blockquote>")
        else:
            parts.append(f"<blockquote>{plain}</blockquote>"
                         "<p class='meta'>The span could not be located inside the "
                         "paragraph, so it is shown separately:</p>"
                         f"<blockquote>{html.escape(_strip_tokens(span))}</blockquote>")
        segs = row.get("evidence_segments") or []
        parts.append("<h3>What the annotator marked as proof in the cited paper</h3>")
        if segs:
            for s in segs:
                parts.append(f"<blockquote>{html.escape(s)}</blockquote>")
        else:
            parts.append("<p class='meta'>None recorded.</p>")
        sp = _source_path(row.get("split", "dev"), row.get("ref", ""))
        if sp:
            parts.append(f"<p class='meta'>Full text of the cited paper: "
                         f"<a href='file://{html.escape(sp)}'>"
                         f"{html.escape(os.path.basename(sp))}</a> "
                         f"({row.get('source_chars', '?')} characters)</p>")
        parts.append("<h3>What the tool said</h3><table><tr><th>model</th>"
                     "<th>verdict</th><th>path</th><th>its reason</th></tr>")
        for label, verdict, method, reason, found, miss in _arm_rows(rid):
            cls = "v-unsupported" if verdict == "unsupported" else "v-supported"
            extra = ""
            if found or miss:
                extra = ("<br><span class='meta'>parts found: "
                         + html.escape("; ".join(found) or "none")
                         + "<br>parts missing: "
                         + html.escape("; ".join(miss) or "none") + "</span>")
            parts.append(f"<tr><td>{html.escape(label)}</td>"
                         f"<td class='{cls}'>{html.escape(verdict or '')}</td>"
                         f"<td>{html.escape(method or '')}</td>"
                         f"<td>{html.escape(reason)}{extra}</td></tr>")
        parts.append("</table>")
    parts.append("<footer>Labels are shown deliberately: this page is for the "
                 "author's own ruling, and must never be handed to a rater or "
                 "used as a grading packet.</footer>")
    if missing:
        parts.insert(2, "<p class='meta'>Not found in this batch: "
                     + html.escape(", ".join(missing)) + "</p>")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as fh:
        fh.write("<!doctype html><meta charset='utf-8'>"
                 "<title>Citation-Integrity rows to check</title>"
                 "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                 + "".join(parts))
    return len(row_ids) - len(missing), missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ground-truth", required=True)
    ap.add_argument("--rows", required=True, help="comma-separated ids")
    ap.add_argument("--out", required=True)
    ap.add_argument("--questions", help="optional JSON file: {id: question}")
    a = ap.parse_args()
    qs = json.load(open(a.questions)) if a.questions else {}
    n, missing = build(a.ground_truth, [r.strip() for r in a.rows.split(",")],
                       a.out, qs)
    print(f"wrote {n} row(s) -> {a.out}")
    if missing:
        print("not in this batch:", ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
