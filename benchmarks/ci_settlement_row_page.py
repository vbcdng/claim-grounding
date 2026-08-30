#!/usr/bin/env python3
"""Build a reading page for one or more arbiter-settlement rows.

A settlement row is a row where the tool put a warning on a claim, the second
checker (the arbiter) decided the warning was unfounded and dropped it, and
that drop changes the score. `benchmarks/ci_settlement_rows.py` froze the 29 of
them; this script turns any of those rows into a page a person can rule on
without opening a paper.

Each row block shows, in this order: the sentence under test inside its own
paragraph, whether the original paper cited one article or several, the
benchmark's own answer and the proof sentences its annotators recorded, what
the tool said and why, what the built-in second checker said and which
sentences persuaded it, every replayed checker with its company and whether it
would have dropped the warning too, both blind readers, and the whole cited
paper folded away at the bottom.

Written 2026-08-06 for task #30 step 4. Step 3 of that task cut the reading
list to a single row, `pilot100:cidev0017` — the only row asking the tool a
fair question where a majority-of-companies rule prevents a false support.

Pure: no API calls, no network. Everything shown is already on disk.

  venv/bin/python3 benchmarks/ci_settlement_row_page.py \
      --rows pilot100:cidev0017 \
      --out docs/settlement_rows_2026-08-04/row_pages/cidev0017.html

NOT a grading packet and NOT for raters: it shows the answer key on purpose.
Also not safe for Fable — the rows quote real biomedical papers verbatim.
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

SETTLEMENT_JSON = os.path.join(ROOT, "docs", "settlement_rows_2026-08-04",
                               "settlement_rows.json")
BLIND_READERS = os.path.join(ROOT, "data", "citation_integrity",
                             "blind_readers_2026-08-03.json")
GROUND_TRUTH = {
    "pilot100": os.path.join(ROOT, "data", "citation_integrity",
                             "batch_dev_pilot100", "ci_ground_truth.json"),
    "fresh50": os.path.join(ROOT, "data", "citation_integrity",
                            "batch_dev_fresh50", "ci_ground_truth.json"),
}

# What the benchmark's own labels mean, in words a reader can check the text
# against. Taken from the dataset's annotation guide.
LABELS = {
    "ACCURATE": "The sentence says what the cited paper says.",
    "NOT_SUBSTANTIATE": "The cited paper does not show what the sentence "
                        "claims it shows.",
    "CONTRADICT": "The cited paper says the opposite of the sentence.",
    "MISQUOTE": "The sentence reports a number, date or detail the cited "
                "paper does not report.",
    "OVERSIMPLIFY": "The sentence drops a condition or hedge the cited paper "
                    "insisted on.",
    "ETIQUETTE": "A citing-manners problem rather than a factual one.",
}

# The second checker answers with one of these. The first two mean it dropped
# the tool's warning; the rest mean it let the warning stand.
ACTIONS = {
    "supported": ("dropped the warning",
                  "found proof in the source for the whole sentence"),
    "wrong_or_insufficient_evidence":
        ("dropped the warning",
         "said the tool had shown the wrong sentences, but the proof is in "
         "the source"),
    "add_citation_or_rewrite":
        ("kept the warning",
         "said the sentence needs another source or a rewrite"),
    "conflicting_evidence":
        ("kept the warning", "said the source argues against the sentence"),
    "unclear": ("kept the warning", "could not tell"),
}

CIT_TOKENS = {"<|cit|>": "the citation being tested",
              "<|multi_cit|>": "another citation sharing the same bracket",
              "<|other_cit|>": "another citation elsewhere in the sentence"}


def source_path(split, ref):
    base = os.path.join(ROOT, "data", "citation_integrity", "repo", "Data",
                        "annotations_extracted", "annotations",
                        {"dev": "Dev", "train": "Train", "test": "Test"}[split],
                        "references")
    hits = glob.glob(os.path.join(base, f"{ref}*"))
    return hits[0] if hits else None


def citation_count(span):
    """How many articles did the original paper cite for this sentence?"""
    others = span.count("<|multi_cit|>") + span.count("<|other_cit|>")
    if others == 0:
        return 1, ("one article, so the single source handed to the tool "
                   "really is supposed to carry the whole sentence. A "
                   "warning here is the tool's own responsibility")
    return others + 1, (
        f"{others + 1} articles, but the benchmark hands over only one of "
        "them and deletes the other citations, so the tool was asked to prove "
        "the whole sentence from a fraction of its support. A warning here is "
        "partly the setup's fault")


def esc(text):
    return html.escape(text or "")


def pmc_link(ident):
    """A PubMed Central address from a recorded id, or None.

    Ids arrive two ways: bare (`PMC9224599`, the citing paper) and prefixed with
    the dataset's own numbering (`024_PMC7588823`, the cited paper). Anything
    without a PMC number gets no link rather than a guessed one.
    """
    m = re.search(r"(PMC\d+)", ident or "")
    if not m:
        return None
    # The current host. The older www.ncbi.nlm.nih.gov/pmc/... form still works
    # but answers with a redirect.
    return f"https://pmc.ncbi.nlm.nih.gov/articles/{m.group(1)}/"


def first_line(text):
    """The cited paper's title — it is the first line of the extracted text."""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def link_html(url, ident):
    if not url:
        return f"<span class=none>no address recorded ({esc(ident)})</span>"
    return f'<a href="{esc(url)}" target=_blank rel=noopener>{esc(url)}</a>'


def paragraph_html(paragraph, span):
    """The citing paragraph with the sentence under test marked."""
    plain_span = span
    for token in CIT_TOKENS:
        plain_span = plain_span.replace(f"[{token}]", "").replace(token, "")
    plain_span = plain_span.strip().rstrip(".").strip()
    clean = paragraph
    for token in CIT_TOKENS:
        clean = clean.replace(f"[{token}]", "[cited here]").replace(token, "")
    at = clean.find(plain_span[:60]) if len(plain_span) >= 60 else -1
    if at < 0:
        return f"<p>{esc(clean)}</p>"
    end = at + len(plain_span)
    tail = clean[end:end + 40]
    stop = tail.find("]")
    if 0 <= stop < 20:
        end += stop + 1
    return ("<p>" + esc(clean[:at]) + "<mark>" + esc(clean[at:end]) +
            "</mark>" + esc(clean[end:]) + "</p>")


def quotes_html(quotes):
    if not quotes:
        return "<p class=none>None given.</p>"
    return "<ul>" + "".join(f"<li>{esc(q)}</li>" for q in quotes) + "</ul>"


def arm_block(name, arm, registry):
    meta = registry.get(name, {})
    ruling = arm.get("ruling") or {}
    action = ruling.get("action") or "no answer"
    verb, why_short = ACTIONS.get(action, ("gave no usable answer", action))
    counted = "counted" if arm.get("votes") else "not counted"
    role = ("" if arm.get("votes") else
            " — a second copy of a model already on the list, run only to see "
            "how far a model agrees with itself")
    return f"""
    <div class="arm {'settles' if arm.get('settles') else 'blocks'}">
      <h4>{esc(meta.get('company', '?'))}
        <span class=small>({esc(ruling.get('model') or name)}, {counted}{esc(role)})</span></h4>
      <p class=verdict><b>{esc(verb.capitalize())}</b> — {esc(why_short)}.</p>
      <p>{esc(ruling.get('why') or '')}</p>
      {('<p class=small><b>What it says is missing:</b> ' +
        esc(ruling.get('missing_subclaim')) + '</p>')
       if ruling.get('missing_subclaim') else ''}
      <details><summary>Sentences it quoted from the source
        ({len(ruling.get('proofs') or [])})</summary>
        {quotes_html(ruling.get('proofs'))}</details>
    </div>"""


def reader_block(who, reader):
    side = ("would keep a warning on this sentence" if reader.get("side") == "flag"
            else "would let this sentence pass")
    return f"""
    <div class="arm {'blocks' if reader.get('side') == 'flag' else 'settles'}">
      <h4>{esc(who.capitalize())} <span class=small>(read the row with the
        answer hidden)</span></h4>
      <p class=verdict><b>{esc(side.capitalize())}</b> — confidence
        {esc(reader.get('confidence') or 'unstated')}.</p>
      <p>{esc(reader.get('reason'))}</p>
      <details><summary>The sentence it rested on</summary>
        {quotes_html([reader.get('quote')] if reader.get('quote') else [])}</details>
    </div>"""


def row_html(row, gt, readers, registry):
    label = row["label"]
    _, cit_note = citation_count(gt.get("annotated_span") or "")
    judge = row.get("judge") or {}
    live = row.get("live_arbiter") or {}
    panel = row.get("panel_scoring") or {}
    live_verb, live_why = ACTIONS.get(live.get("action"),
                                      ("gave no usable answer", ""))
    settling = panel.get("companies_settling") or []
    blocking = panel.get("companies_blocking") or []
    src = source_path(gt.get("split", "dev"), gt.get("ref", ""))
    source_text = ""
    if src and os.path.exists(src):
        with open(src, encoding="utf-8", errors="replace") as f:
            source_text = f.read()

    cited = pmc_link(gt.get("ref"))
    citing = pmc_link(gt.get("citing_pmcid"))

    return f"""
<section class=row>
  <h2>{esc(row['row'])}</h2>

  <h3>1. The sentence being checked</h3>
  <p class=note>It is shown inside the paragraph it came from. The marked part
    is what the tool was asked to prove.</p>
  {paragraph_html(gt.get('citing_paragraph') or '', gt.get('annotated_span') or '')}
  <p class=small><b>The original paper cited</b> {esc(cit_note)}.</p>

  <h3>2. Both papers, as published</h3>
  <p class=note>Open these if you want the real thing — figures, tables, the
    parts plain text loses. The copy below is what the tool was actually
    given, so if the two ever disagree, the tool saw the copy.</p>
  <ul>
   <li><b>The cited paper</b> — the one that has to prove the sentence.
     {'"' + esc(first_line(source_text)) + '"' if source_text else ''}<br>
     {link_html(cited, gt.get('ref'))}</li>
   <li><b>The citing paper</b> — the one making the claim.<br>
     {link_html(citing, gt.get('citing_pmcid'))}</li>
  </ul>

  <h3>3. The cited paper, as the tool read it</h3>
  <p class=note>The whole text, {len(source_text):,} characters. Use your
    browser's find (Ctrl+F) to look for a word from the sentence — that is
    exactly what deciding this row comes down to.</p>
  <pre class=source>{esc(source_text)}</pre>

  <h3>4. Your ruling</h3>
  <p class=note>Before opening section 5. The question: does the cited paper
    show what the marked sentence claims it shows?</p>
  <div class=ruling>
    <label><input type=radio name="r_{esc(row['cidev'])}" value="supported">
      Yes — the paper shows it. The warning was wrong and dropping it was
      right.</label>
    <label><input type=radio name="r_{esc(row['cidev'])}" value="flag">
      No — the paper does not show it. The warning should have stayed.</label>
    <label><input type=radio name="r_{esc(row['cidev'])}" value="unsure">
      Cannot tell from this material.</label>
    <textarea rows=4 placeholder="Why (optional)"></textarea>
    <button onclick="copyRuling(this)">Copy my answer</button>
    <span class=copied></span>
  </div>

  <h3>5. What everyone else said</h3>
  <details class=others>
    <summary><b>Open only after you have decided.</b> Contains the benchmark's
      answer, the tool, all six checkers and both readers.</summary>

    <h4 class=sub>The benchmark's own answer</h4>
    <p><b>{esc(label)}</b> — {esc(LABELS.get(label, ''))}</p>
    <p class=small>The people who built the benchmark recorded these sentences
      from the cited paper as the ones that decide the row:</p>
    {quotes_html(gt.get('evidence_segments'))}

    <h4 class=sub>What the tool said</h4>
    <p class=verdict><b>{esc(judge.get('verdict', '?'))}</b> — reached by
      {esc(judge.get('method', '?'))}.</p>
    <p>{esc(judge.get('reason'))}</p>

    <h4 class=sub>What the built-in second checker said</h4>
    <p class=note>This is the one the tool actually runs today. Its answer is
      what created this row.</p>
    <p class=verdict><b>{esc(live_verb.capitalize())}</b>
      ({esc(live.get('model') or '?')}) — {esc(live_why)}.</p>
    <p>{esc(live.get('why'))}</p>
    <details><summary>Sentences it quoted from the source
      ({len(live.get('proofs') or [])})</summary>
      {quotes_html(live.get('proofs'))}</details>

    <h4 class=sub>Every other checker, one per company</h4>
    <p class=note>All of them read the same claim, the same source and the same
      tool output. Only the model differed.
      <b>{len(settling)} would drop the warning</b>
      ({esc(', '.join(settling) or 'none')});
      <b>{len(blocking)} would keep it</b>
      ({esc(', '.join(blocking) or 'none')}).</p>
    {''.join(arm_block(name, arm, registry)
             for name, arm in (row.get('arms') or {}).items())}

    <h4 class=sub>The two readers who saw no answers</h4>
    <p class=note>Two models read the row on 2026-08-03 with the benchmark's
      answer hidden from them.</p>
    {''.join(reader_block(who, r) for who, r in (readers or {}).items())}
  </details>
</section>"""


PAGE = """<!doctype html>
<meta charset=utf-8>
<title>Settlement rows to read</title>
<style>
 body {{ font: 16px/1.6 Georgia, serif; max-width: 46em; margin: 2em auto;
        padding: 0 1em; color: #1a1a1a; }}
 h1 {{ font-size: 1.6em; }} h2 {{ font-size: 1.3em; margin-top: 2.5em;
        border-top: 2px solid #333; padding-top: .6em; }}
 h3 {{ font-size: 1.05em; margin-top: 1.8em; }}
 h4 {{ font-size: 1em; margin: 0 0 .3em; }}
 mark {{ background: #ffe89a; }}
 .note, .small {{ color: #555; font-size: .9em; }}
 .none {{ color: #777; font-style: italic; }}
 .verdict {{ margin: .3em 0; }}
 .arm {{ border-left: 4px solid #ccc; padding: .7em 1em; margin: .8em 0;
         background: #fafafa; }}
 .arm.settles {{ border-color: #2e7d32; }}
 .arm.blocks {{ border-color: #c62828; }}
 h4.sub {{ font-size: 1em; margin: 1.6em 0 .3em; border-bottom: 1px solid #ddd;
        padding-bottom: .2em; }}
 .source {{ max-height: 26em; }}
 .others {{ border: 1px solid #ddd; padding: .8em 1em; background: #fcfcfc; }}
 .others > summary {{ color: #444; }}
 .ruling {{ border: 1px solid #bbb; padding: 1em; background: #fbfbf7; }}
 .ruling label {{ display: block; margin: .3em 0; }}
 .ruling textarea {{ width: 100%; font: inherit; margin: .6em 0; }}
 .copied {{ color: #2e7d32; margin-left: .8em; }}
 details {{ margin: .6em 0; }} summary {{ cursor: pointer; color: #333; }}
 pre {{ white-space: pre-wrap; font: 13px/1.5 monospace; background: #f4f4f4;
        padding: 1em; max-height: 30em; overflow: auto; }}
 ul {{ margin: .4em 0; }} li {{ margin: .3em 0; }}
 .words dt {{ font-weight: bold; margin-top: .5em; }}
</style>
<h1>Settlement rows to read</h1>
<p class=note>Built {built} from files already on disk. No model was asked
anything to make this page.</p>

<p><b>The order is deliberate.</b> The sentence, then links to both papers, then
the cited paper's full text, then your ruling — and only after that what anyone
else concluded, folded away. Decide first; the other opinions are there to
compare against afterwards, not to read into.</p>

<h2 style="border:0">Words used on this page</h2>
<dl class=words>
 <dt>Warning</dt><dd>Anything the tool puts on a claim to tell the author
   something is wrong with the citation — either a yellow badge saying part of
   the sentence has no proof, or a red verdict saying the source does not
   support it at all.</dd>
 <dt>Second checker</dt><dd>A different model that re-reads a claim the tool
   warned about, with the whole source in front of it instead of the few
   sentences the tool picked out.</dd>
 <dt>Dropping the warning</dt><dd>What the second checker does when it decides
   the tool was wrong to warn. The warning disappears and the card ends up
   telling the author the citation is fine.</dd>
 <dt>Settlement row</dt><dd>A row where the second checker dropped the warning
   and that changed the score.</dd>
 <dt>False support</dt><dd>The dangerous outcome: the benchmark's answer says
   the citation misrepresents its source, but the card tells the author it is
   fine.</dd>
 <dt>Answer key</dt><dd>The benchmark's own judgement of the row, written by
   the people who built it. It can itself be wrong, which is why the readers
   below saw the row with it hidden.</dd>
</dl>

{why}
{rows}
<script>
function copyRuling(btn) {{
  const box = btn.closest('.ruling');
  const picked = box.querySelector('input:checked');
  const text = (picked ? picked.value : 'no answer') + ' | ' +
               box.querySelector('textarea').value;
  navigator.clipboard.writeText(text);
  box.querySelector('.copied').textContent = 'copied';
}}
</script>
"""

WHY_0017 = """
<h2 style="border:0">Why this row and no other</h2>
<p>Twenty-nine rows exist where the second checker dropped the tool's warning
and that changed the score. On 2026-08-04 five companies each read all
twenty-nine. Requiring a majority of them to agree before a warning may be
dropped would prevent five false supports, at the cost of putting seven
warnings back onto citations that were fine.</p>
<p>Splitting those rows by whether the tool was asked a fair question changed
the picture. Four of the five false supports sit on rows where the original
paper cited several articles and the benchmark handed over only one of them —
the tool was asked to prove a whole sentence from a fraction of its support.
On rows that cite a single article, the majority rule prevents
<b>one</b> false support and creates seven warnings on good citations.</p>
<p>This is that one row. If the cited paper really does fail to show what the
sentence claims, the majority rule has a real, if small, benefit. If you read
it and think the sentence is fine, the rule has no measured benefit at all and
can be closed rather than parked.</p>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True,
                    help="comma-separated, batch-qualified, "
                         "e.g. pilot100:cidev0017")
    ap.add_argument("--out", required=True)
    ap.add_argument("--settlement-json", default=SETTLEMENT_JSON)
    ap.add_argument("--built", default="2026-08-06",
                    help="date printed on the page")
    args = ap.parse_args()

    with open(args.settlement_json, encoding="utf-8") as f:
        frozen = json.load(f)
    by_id = {r["row"]: r for r in frozen["rows"]}
    registry = frozen.get("arm_registry") or {}

    with open(BLIND_READERS, encoding="utf-8") as f:
        blind = json.load(f)["rows"]

    wanted = [r.strip() for r in args.rows.split(",") if r.strip()]
    missing = [r for r in wanted if r not in by_id]
    if missing:
        sys.exit(f"not a settlement row: {', '.join(missing)}")

    gts = {}
    blocks = []
    for rid in wanted:
        row = by_id[rid]
        if row["batch"] not in gts:
            with open(GROUND_TRUTH[row["batch"]], encoding="utf-8") as f:
                gts[row["batch"]] = json.load(f)["claims"]
        gt = gts[row["batch"]][row["cidev"]]
        readers = (blind.get(rid) or {}).get("readers") or {}
        blocks.append(row_html(row, gt, readers, registry))

    why = WHY_0017 if wanted == ["pilot100:cidev0017"] else ""
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(PAGE.format(built=args.built, why=why, rows="\n".join(blocks)))
    print(f"wrote {args.out} ({len(blocks)} row(s))")


if __name__ == "__main__":
    main()
