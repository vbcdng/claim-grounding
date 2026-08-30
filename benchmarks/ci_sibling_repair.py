#!/usr/bin/env python3
"""Build repaired copies of the Citation-Integrity batches — task #32, round 2.

Reads `sibling_recovery.json` (round 1, `ci_sibling_recover.py`), downloads the
sibling cited papers from Europe PMC, and writes a repaired copy of each batch
where a multi-cited sentence carries ALL of its citations instead of one.

NO API calls, NO paid services, NO AI model. Europe PMC's open endpoints only.
The original batch directories are never modified.

WHAT CHANGES IN A REPAIRED ROW
------------------------------
The claim's own wording is byte-identical to the original. The only edit is
extra markers: `[[cidev0043]]` gains `[[cidev0043_s1]] [[cidev0043_s2]] ...`,
one per sibling paper we could fetch, plus one source file each.

Marker placement was decided by measurement, not taste (round 2, 2026-08-07):

  * `append` — sibling markers at the very end of the paragraph. Works on 70 of
    the 75 rows: consecutive markers group onto the preceding sentence.
  * `adjacent` — sibling markers immediately after the row's own marker. Needed
    by the other 5, whose paragraph already segments into two claims, so an
    appended marker would land on the second one.

Every row is checked after the edit: the repaired paragraph must produce the
same number of claims as before, the row's claim text must be unchanged, and
all of its markers must sit on that one claim. A row failing the check is left
unrepaired rather than shipped broken. This is the failure mode of the
2026-08-01 attempt (`ci_sibling_marker_probe.py`), which put markers inside the
sentence and shredded it — never do that.

FAIRNESS FLAG
-------------
`repair.fair_question` is true only when every citation on the sentence is
present as a source. A row with a paywalled sibling is repaired as far as it
goes and stays flagged unfair, so it can be reported in its own column (author
ruling, 2026-08-07) rather than silently counted as fixed.

Usage:
    python3 benchmarks/ci_sibling_repair.py [--out-suffix _repaired] [--dry-run]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "modules"))
sys.path.insert(0, HERE)

from papertrail.text_decomposer import extract_claims        # noqa: E402
from ci_sibling_recover import _get, EPMC, work_key          # noqa: E402

CI_DIR = os.path.join(ROOT, "data", "citation_integrity")
BATCHES = ("pilot100", "fresh50")


# --- fetching the sibling papers -----------------------------------------

def jats_to_text(xml):
    """Title, abstract and body of a Europe PMC paper as plain text."""
    title = re.search(r"<article-title[^>]*>(.*?)</article-title>", xml, re.S)
    title = re.sub(r"<[^>]+>", "", title.group(1)).strip() if title else ""

    def strip(block):
        block = re.sub(r"<xref[^>]*>.*?</xref>", "", block, flags=re.S)
        block = re.sub(r"<(table-wrap|fig|disp-formula)[^>]*>.*?</\1>", " ", block, flags=re.S)
        paras = re.findall(r"<p[ >].*?</p>", block, re.S)
        out = []
        for p in paras:
            t = re.sub(r"<[^>]+>", "", p)
            t = t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            t = re.sub(r"&#x0*([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), t)
            t = re.sub(r"\s+", " ", t).strip()
            if len(t) > 40:
                out.append(t)
        return out

    abstract = re.search(r"<abstract[^>]*>(.*?)</abstract>", xml, re.S)
    body = re.search(r"<body[^>]*>(.*?)</body>", xml, re.S)
    parts = [f"# {title}"] if title else []
    if abstract:
        parts += ["", "## Abstract"] + strip(abstract.group(1))
    if body:
        parts += [""] + strip(body.group(1))
    return "\n\n".join(parts).strip()


def fetch_paper(pmcid, cache):
    data = _get(f"{EPMC}/{pmcid}/fullTextXML",
                os.path.join(cache, "siblings", f"{pmcid}.xml"))
    if not data or len(data) < 3000:
        return None
    text = jats_to_text(data.decode("utf-8", "replace"))
    return text if len(text) > 1500 else None


# --- the repair itself ----------------------------------------------------

def sibling_markers(key, n):
    return [f"{key}_s{i}" for i in range(1, n + 1)]


def repaired_paragraph(text, key, sib_keys):
    """Return (new_text, placement) or (None, None) if neither placement is safe."""
    marks = " ".join(f"[[{k}]]" for k in sib_keys)
    before = extract_claims(text)
    own_before = [c for c in before if key in (c.get("markers") or [])]
    if len(own_before) != 1:
        return None, None
    want = {key} | set(sib_keys)
    for placement, candidate in (("append", text.rstrip() + " " + marks),
                                 ("adjacent", text.replace(f"[[{key}]]",
                                                           f"[[{key}]] {marks}", 1))):
        after = extract_claims(candidate)
        own = [c for c in after if key in (c.get("markers") or [])]
        if (len(after) == len(before) and len(own) == 1
                and own[0].get("text") == own_before[0].get("text")
                and set(own[0]["markers"]) == want):
            return candidate, placement
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suffix", default="_repaired")
    ap.add_argument("--recovery", default=os.path.join(CI_DIR, "sibling_recovery.json"))
    ap.add_argument("--cache", default=os.path.join(CI_DIR, "sibling_cache"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with open(args.recovery, encoding="utf-8") as f:
        recovery = json.load(f)
    status = recovery["work_status"]
    by_row = {(r["batch"], r["key"]): r for r in recovery["rows"]}

    tally = collections.Counter()
    fetched, failed_fetch = {}, set()
    report = {"batches": {}, "rows": {}}

    for batch in BATCHES:
        src_dir = os.path.join(CI_DIR, f"batch_dev_{batch}")
        out_dir = os.path.join(CI_DIR, f"batch_dev_{batch}{args.suffix}")
        with open(os.path.join(src_dir, "ci_ground_truth.json"), encoding="utf-8") as f:
            meta = json.load(f)
        claims = meta["claims"]

        if not args.dry_run:
            os.makedirs(os.path.join(out_dir, "sources"), exist_ok=True)

        paras, refs, extra_sources = [], [], {}
        for key, row in claims.items():
            text = row["claim_text"]
            title = row.get("ci_id", key)
            refs.append(f"# Citation-Integrity {title}\n{key} = {key}.txt")

            rec = by_row.get((batch, key))
            own = re.search(r"PMC\d+", row.get("ref") or "")
            own = own.group(0) if own else None

            siblings, unfetchable = [], []
            if rec:
                for ref in rec["refs"]:
                    st = status.get(work_key(ref), {})
                    pmcid = ref.get("pmcid") or st.get("pmcid")
                    if pmcid and own and pmcid.replace("PMC", "") == own.replace("PMC", ""):
                        continue                      # this is the paper under test
                    label = (ref.get("title") or "")[:110]
                    if st.get("status") != "full text in Europe PMC" or not pmcid:
                        unfetchable.append({"reason": st.get("status", "unresolved"),
                                            "title": label})
                        continue
                    if pmcid in failed_fetch:
                        unfetchable.append({"reason": "download failed", "title": label})
                        continue
                    if pmcid not in fetched:
                        body = fetch_paper(pmcid, args.cache)
                        if body is None:
                            failed_fetch.add(pmcid)
                            unfetchable.append({"reason": "download failed", "title": label})
                            continue
                        fetched[pmcid] = body
                    siblings.append({"pmcid": pmcid, "title": label})

            # Recomputed from the stored span, never from the `co_citation`
            # field: pilot100 predates that field, so reading it would call
            # every pilot row single-cited and mark unrepaired rows fair.
            span = row.get("annotated_span") or ""
            multi = "<|multi_cit|>" in span or "<|other_cit|>" in span
            entry = {"multi_cited": multi,
                     "siblings_added": len(siblings),
                     "siblings_unavailable": len(unfetchable)}

            if siblings:
                sib_keys = sibling_markers(key, len(siblings))
                new_text, placement = repaired_paragraph(text, key, sib_keys)
                if new_text is None:
                    entry.update(status="left unrepaired: marker placement unsafe",
                                 siblings_added=0)
                    tally["left unrepaired (placement unsafe)"] += 1
                    paras.append(text)
                else:
                    for sk, sib in zip(sib_keys, siblings):
                        extra_sources[sk] = fetched[sib["pmcid"]]
                        refs.append(f"# {sib['title']} ({sib['pmcid']}, sibling citation "
                                    f"of {key})\n{sk} = {sk}.txt")
                    paras.append(new_text)
                    entry.update(status="repaired", placement=placement,
                                 sibling_keys=sib_keys,
                                 sibling_papers=siblings, unfetchable=unfetchable)
                    tally["repaired" if not unfetchable
                          else "repaired but a citation is still missing"] += 1
            else:
                paras.append(text)
                if rec:
                    entry.update(status="not repaired: no sibling could be fetched",
                                 unfetchable=unfetchable)
                    tally["not repaired (no sibling obtainable)"] += 1
                elif multi:
                    entry.update(status="unchanged: its citations could not be recovered")
                    tally["unchanged, still unfair (citations unrecoverable)"] += 1
                else:
                    entry.update(status="unchanged: the sentence cited one paper")
                    tally["unchanged, already fair (one citation)"] += 1

            # Fair means every citation on the sentence is present as a source:
            # either it only ever had one, or all of its siblings were fetched.
            entry["fair_question"] = bool(
                (entry.get("status") == "repaired" and not unfetchable)
                or (not multi and entry.get("status", "").startswith("unchanged")))
            row["repair"] = entry
            report["rows"][f"{batch}:{key}"] = entry

        if args.dry_run:
            report["batches"][batch] = {"claims": len(claims), "dry_run": True}
            continue

        for key in list(claims):
            shutil.copyfile(os.path.join(src_dir, "sources", f"{key}.txt"),
                            os.path.join(out_dir, "sources", f"{key}.txt"))
        for sk, body in extra_sources.items():
            with open(os.path.join(out_dir, "sources", f"{sk}.txt"), "w",
                      encoding="utf-8") as f:
                f.write(body)
        with open(os.path.join(out_dir, "my_text.md"), "w", encoding="utf-8") as f:
            f.write(f"# Citation-Integrity {meta['split']} batch {meta['batch']} "
                    f"(sibling citations restored)\n\n" + "\n\n".join(paras) + "\n")
        with open(os.path.join(out_dir, "my_text.md.refs.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(refs) + "\n")
        meta["repair"] = {"task": 32, "built_by": "benchmarks/ci_sibling_repair.py",
                          "source_batch": f"batch_dev_{batch}"}
        with open(os.path.join(out_dir, "ci_ground_truth.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=1)
        report["batches"][batch] = {"claims": len(claims),
                                    "extra_sources": len(extra_sources),
                                    "output": out_dir}
        print(f"wrote {out_dir}: {len(claims)} claims, "
              f"{len(extra_sources)} sibling source files", flush=True)

    print("\n--- round 2 result ---")
    for k, v in tally.most_common():
        print(f"  {v:4d}  {k}")
    fair = sum(1 for e in report["rows"].values() if e["fair_question"])
    print(f"\nrows now asking a fair question: {fair} of {len(report['rows'])}")
    print(f"distinct sibling papers downloaded: {len(fetched)}")
    if failed_fetch:
        print(f"papers that would not download: {len(failed_fetch)}")

    out = os.path.join(CI_DIR, "sibling_repair_report.json")
    if not args.dry_run:
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"tally": dict(tally), "fair_rows": fair,
                       "sibling_papers_downloaded": len(fetched), **report}, f, indent=1)
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
