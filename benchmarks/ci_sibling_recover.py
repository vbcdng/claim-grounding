#!/usr/bin/env python3
"""Recover the sibling citations the Citation-Integrity converter throws away.

Task #32, round 1 (2026-08-07). NO API calls, NO paid services — Europe PMC's
open REST endpoints only. Read-only with respect to the benchmark batches: it
writes a report, it does not touch ci_ground_truth.json or any batch directory.

THE DEFECT
----------
`citation_integrity_bench._claim_text` rewrites the citing sentence so the
citation under test becomes `[[cidevNNNN]]` and every OTHER citation on that
sentence is deleted (lines 376-380). The dataset masks siblings as
`<|other_cit|>`, so their identity looked unrecoverable and the emitted row
asks the tool to prove a whole multi-cited sentence from a single paper. 82 of
the 150 converted rows (54/100 pilot + 28/50 fresh) are multi-cited.

THE ROUTE THIS SCRIPT TAKES
---------------------------
The dataset records `citing_pmcid`. Almost every citing paper is in Europe PMC
with full JATS XML, where each citation is an `<xref ref-type="bibr">` pointing
at a `<ref>` entry carrying a PMID/DOI/PMCID. So:

  1. fetch the citing paper's XML                        (Europe PMC, free)
  2. flatten it to words + citation tokens, then locate the annotated span by
     word alignment (exact window first, then head/tail anchors)
  3. read every citation inside the matched range, plus the ones trailing its
     last word (a sentence's citations sit after its final word)
  4. resolve each `<ref>` to PMID/DOI/PMCID/title, both the `pub-id-type` and
     the `ext-link` + `mixed-citation` encodings
  5. ask Europe PMC whether each cited work has retrievable full text

Both parsing bugs found in round 1 are fixed here and are worth remembering:
missing step 3 left 59 rows with zero citations, and reading only
`pub-id-type` left 130 of 268 citation slots with no identifier at all.

Cached under <scratch>/ci_sibling_cache/ so re-runs are offline and instant.

Usage:
    python3 benchmarks/ci_sibling_recover.py [--out FILE] [--cache DIR]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BATCHES = ("pilot100", "fresh50")
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"

WORD = re.compile(r"[A-Za-z]+")
_XREF_A = re.compile(r'<xref[^>]*ref-type="bibr"[^>]*rid="([^"]+)"[^>]*>.*?</xref>', re.S)
_XREF_B = re.compile(r'<xref[^>]*rid="([^"]+)"[^>]*ref-type="bibr"[^>]*>.*?</xref>', re.S)
_TOKEN = re.compile(r"\{\{XR:([^}]*)\}\}|([A-Za-z]+)")


# --- fetching -------------------------------------------------------------

def _get(url, cache_path):
    if cache_path and os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
        with open(cache_path, "rb") as f:
            return f.read()
    try:
        data = urllib.request.urlopen(url, timeout=30).read()
    except Exception:
        return None
    if cache_path:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "wb") as f:
            f.write(data)
    return data


def citing_xml(pmcid, cache):
    data = _get(f"{EPMC}/{pmcid}/fullTextXML", os.path.join(cache, "citing", f"{pmcid}.xml"))
    if not data or len(data) < 5000 or b"<ref" not in data:
        return None
    return data.decode("utf-8", "replace")


def epmc_search(query, cache):
    safe = re.sub(r"[^A-Za-z0-9]+", "_", query)[:80]
    url = (f"{EPMC}/search?query={urllib.parse.quote(query)}"
           f"&format=json&pageSize=1&resultType=core")
    data = _get(url, os.path.join(cache, "search", f"{safe}.json"))
    if not data:
        return None
    try:
        hits = json.loads(data)["resultList"]["result"]
    except Exception:
        return None
    return hits[0] if hits else None


# --- parsing --------------------------------------------------------------

def flatten(xml):
    """Body text with each citation replaced by a {{XR:rid}} token."""
    body = xml.split("<back")[0]
    body = _XREF_A.sub(lambda m: " {{XR:%s}} " % m.group(1), body)
    body = _XREF_B.sub(lambda m: " {{XR:%s}} " % m.group(1), body)
    body = re.sub(r"<[^>]+>", " ", body)
    body = body.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    body = re.sub(r"&#x0*([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), body)
    return re.sub(r"[ \t]+", " ", body)


def tokenize(text):
    """[('w', word) | ('x', 'rid rid')] in document order."""
    out = []
    for m in _TOKEN.finditer(text):
        out.append(("x", m.group(1)) if m.group(1) is not None else ("w", m.group(2).lower()))
    return out


def refmap(xml):
    """rid -> {label, title, doi, pmid, pmcid} for every <ref> in the paper."""
    out = {}
    for m in re.finditer(r'<ref id="([^"]+)".*?</ref>', xml, re.S):
        rid, block = m.group(1), m.group(0)

        def pid(kind):
            a = re.search(r'pub-id-type="%s"[^>]*>([^<]+)' % kind, block)
            if a:
                return a.group(1).strip()
            b = re.search(r'ext-link-type="%s"[^>]*xlink:href="([^"]+)"' % kind, block)
            return b.group(1).strip() if b else None

        lab = re.search(r"<label>([^<]+)</label>", block)
        title = re.search(r"<article-title>(.*?)</article-title>", block, re.S)
        text = re.sub(r"<[^>]+>", "", title.group(1)) if title else None
        if not text:  # mixed-citation style: one plain citation string
            cs = (re.search(r'content-type="citation-string">(.*?)</named-content>', block, re.S)
                  or re.search(r"<mixed-citation[^>]*>(.*?)</mixed-citation>", block, re.S))
            if cs:
                text = re.sub(r"<[^>]+>", "", cs.group(1))
        out[rid] = {"rid": rid,
                    "label": lab.group(1).strip() if lab else None,
                    "title": re.sub(r"\s+", " ", text).strip() if text else None,
                    "doi": pid("doi"), "pmid": pid("pmid"), "pmcid": pid("pmcid")}
    return out


def span_words(span):
    return [w.lower() for w in WORD.findall(re.sub(r"<\|[a-z_]+\|>", " ", span or ""))]


def locate(toks, sw):
    """Token range of the span inside the document, or None."""
    idx = [(i, t[1]) for i, t in enumerate(toks) if t[0] == "w"]
    words = [w for _, w in idx]
    n = len(sw)
    if n < 6 or n > len(words):
        return None
    for s in range(len(words) - n + 1):
        if words[s:s + n] == sw:
            return idx[s][0], idx[s + n - 1][0]
    head, tail = sw[:6], sw[-6:]          # tolerate small edits inside the span
    for s in (i for i in range(len(words) - 5) if words[i:i + 6] == head):
        lo = s + n - 12 if n > 12 else s
        for e in range(lo, min(len(words), s + n + 40)):
            if words[e:e + 6] == tail:
                return idx[s][0], idx[min(e + 5, len(idx) - 1)][0]
    return None


def citations_of_span(toks, span):
    """rids cited inside the span, including those trailing its last word."""
    loc = locate(toks, span_words(span))
    if not loc:
        return None
    a, b = loc
    while b + 1 < len(toks) and toks[b + 1][0] == "x":
        b += 1                             # a sentence's citations follow its last word
    rids, seen = [], set()
    for kind, val in toks[a:b + 1]:
        if kind != "x":
            continue
        for rid in val.split():
            if rid and rid not in seen:
                seen.add(rid)
                rids.append(rid)
    return rids


# --- driver ---------------------------------------------------------------

def work_key(ref):
    return (ref.get("pmid") or ref.get("doi") or ref.get("pmcid")
            or "T:" + (ref.get("title") or "").lower()[:90])


def availability(ref, cache):
    if ref.get("pmcid"):
        q = "PMCID:%s" % ref["pmcid"]
    elif ref.get("pmid"):
        q = "EXT_ID:%s AND SRC:MED" % ref["pmid"]
    elif ref.get("doi"):
        q = 'DOI:"%s"' % ref["doi"]
    elif ref.get("title"):
        q = '"%s"' % ref["title"][:120]
    else:
        return {"status": "no identifier of any kind"}
    hit = epmc_search(q, cache)
    if not hit:
        return {"status": "not found in Europe PMC"}
    pmcid = hit.get("pmcid")
    if hit.get("inEPMC") == "Y" and pmcid:
        # `inEPMC` overstates it: 13 papers were listed here but answered the
        # full-text request with "not found" (measured in round 2). So the claim
        # "obtainable" is only made after the download has actually succeeded.
        body = _get(f"{EPMC}/{pmcid}/fullTextXML",
                    os.path.join(cache, "siblings", f"{pmcid}.xml"))
        status = ("full text in Europe PMC" if body and len(body) > 3000
                  else "listed in Europe PMC but its full text is not served")
    elif hit.get("isOpenAccess") == "Y":
        status = "open access, text elsewhere"
    else:
        status = "closed access"
    return {"status": status, "pmcid": hit.get("pmcid"), "doi": hit.get("doi"),
            "title": (hit.get("title") or "")[:90]}


def load_rows():
    rows = []
    for batch in BATCHES:
        path = os.path.join(ROOT, "data", "citation_integrity",
                            f"batch_dev_{batch}", "ci_ground_truth.json")
        with open(path, encoding="utf-8") as f:
            claims = json.load(f)["claims"]
        for key, v in claims.items():
            span = v.get("annotated_span", "") or ""
            shared = "<|multi_cit|>" in span
            sibs = span.count("<|other_cit|>")
            rows.append({"batch": batch, "key": key, "ci_id": v.get("ci_id"),
                         "citing_pmcid": v.get("citing_pmcid"), "ref": v.get("ref"),
                         "label": v.get("label"), "span": span,
                         "co_citation_class": ("both" if shared and sibs else
                                               "shared_spot" if shared else
                                               "siblings_in_span" if sibs else "single")})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "citation_integrity",
                                                  "sibling_recovery.json"))
    ap.add_argument("--cache", default=os.path.join(ROOT, "data", "citation_integrity",
                                                    "sibling_cache"))
    args = ap.parse_args()

    rows = load_rows()
    multi = [r for r in rows if r["co_citation_class"] != "single"]
    print(f"rows: {len(rows)} | multi-cited: {len(multi)}", flush=True)

    docs, out, skipped = {}, [], []
    for r in multi:
        pmcid = r["citing_pmcid"]
        if pmcid not in docs:
            xml = citing_xml(pmcid, args.cache)
            docs[pmcid] = (tokenize(flatten(xml)), refmap(xml)) if xml else None
        if docs[pmcid] is None:
            skipped.append(dict(r, why="citing paper has no full text in Europe PMC"))
            continue
        toks, rmap = docs[pmcid]
        rids = citations_of_span(toks, r["span"])
        if rids is None:
            skipped.append(dict(r, why="span could not be located in the citing paper"))
            continue
        if not rids:
            skipped.append(dict(r, why="span located but carries no citation markers"))
            continue
        out.append(dict(r, rids=rids, refs=[rmap.get(x, {"rid": x}) for x in rids]))

    works = {}
    for row in out:
        for ref in row["refs"]:
            works.setdefault(work_key(ref), ref)
    print(f"spans located: {len(out)} | distinct cited works: {len(works)}", flush=True)

    status = {}
    for i, (k, ref) in enumerate(works.items(), 1):
        status[k] = availability(ref, args.cache)
        if i % 40 == 0:
            print(f"  checked {i}/{len(works)}", flush=True)

    for row in out:
        st = [status[work_key(r)]["status"] for r in row["refs"]]
        row["fetchable"] = sum(1 for s in st if s == "full text in Europe PMC")
        row["citations"] = len(st)
        row["repairable"] = ("fully" if row["fetchable"] == row["citations"]
                             else "partly" if row["fetchable"] else "not at all")

    tally = collections.Counter(r["repairable"] for r in out)
    print("\n--- round 1 result ---")
    print(f"multi-cited rows                : {len(multi)}")
    print(f"  every citation obtainable     : {tally['fully']}")
    print(f"  some citations obtainable     : {tally['partly']}")
    print(f"  none obtainable               : {tally['not at all']}")
    print(f"  not recovered at all          : {len(skipped)}")
    for s in skipped:
        print(f"      {s['batch']}:{s['key']} — {s['why']}")
    print("\ncited works: " + ", ".join(f"{v} {k}" for k, v in
                                        collections.Counter(v["status"] for v in status.values()).most_common()))

    payload = {"generated_by": "benchmarks/ci_sibling_recover.py",
               "rows_total": len(rows), "multi_cited": len(multi),
               "summary": {"fully_repairable": tally["fully"],
                           "partly_repairable": tally["partly"],
                           "not_repairable": tally["not at all"],
                           "not_recovered": len(skipped)},
               "work_status": status, "rows": out, "skipped": skipped}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
