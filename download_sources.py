#!/usr/bin/env python3
"""
download_sources.py — fetch the cited sources listed in a sources_manifest.json
(produced by import_claude_research.py) into the sources/ folder, and report what
could not be fetched (ROADMAP item 2).

For each manifest entry:
- has_link  -> paper-shaped urls go through the open-access PDF cascade
               (arXiv / Unpaywall / doi.org / publisher patterns); plain web pages
               are saved as extracted page text. Saved as <key>.pdf or <key>.txt.
- needs_search -> a Semantic Scholar title lookup; only a confident match is
               downloaded, otherwise the entry lands in the report as "needs a
               literature search".

Already-present files in sources/ are skipped, so re-running after manually
dropping in papers is cheap. The refs file's suggested extensions are corrected
to match what was actually saved. Failures are listed in the terminal AND in
<dir>/download_report.md (title + DOI + landing-page link, or "no link ever
given") — that report is the hand-off for fetching the rest via your own access.

Paywalls are respected: a miss is reported, never worked around. No LLM calls.

Usage:
  venv/bin/python3 download_sources.py --manifest data/paper1_import/sources_manifest.json
  # smoke-test on a few entries first:
  venv/bin/python3 download_sources.py --manifest ... --keys sastry2024,stanfordhai2025
"""

import os
import sys
import json
import time
import random
import logging
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.papertrail import direct_downloader as dd
from modules.papertrail import semantic_scholar_api as s2

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("download_sources")


def rewrite_refs(refs_path, actual_filenames):
    """Fix `key = <key>.pdf` lines to the extension actually saved. Preserves
    comments, unknown keys, and line order."""
    if not os.path.exists(refs_path):
        logger.warning(f"Refs file not found, skipping extension fix: {refs_path}")
        return
    out_lines = []
    with open(refs_path, "r", encoding="utf-8") as f:
        for line in f.read().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in actual_filenames:
                    out_lines.append(f"{key} = {actual_filenames[key]}")
                    continue
            out_lines.append(line)
    with open(refs_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines) + "\n")


def write_report(report_path, manifest_entries, sources_dir, run_results):
    """
    Full status of EVERY manifest entry, regardless of what this run touched:
    on disk / missing-with-a-link / missing-needs-a-literature-search. This is
    the persistent hand-off document — safe to regenerate any time
    (--report-only), never scoped to a --keys subset.
    """
    present, missing_link, missing_search = [], [], []
    for e in manifest_entries:
        key = e["key"]
        r = run_results.get(key, {})
        filename = None
        for ext in (".pdf", ".txt"):
            p = os.path.join(sources_dir, key + ext)
            if os.path.exists(p) and os.path.getsize(p) > 1000:
                filename = key + ext
                break
        if filename:
            words = None
            if filename.endswith(".txt"):
                with open(os.path.join(sources_dir, filename), "r",
                          encoding="utf-8", errors="ignore") as f:
                    words = len(f.read().split())
            mismatch = dd.content_check(os.path.join(sources_dir, filename),
                                        e.get("title"), e.get("author")) == "mismatch"
            present.append({"key": key, "title": e.get("title"), "filename": filename,
                            "words": words, "mismatch": mismatch,
                            "thin": words is not None and words < dd.THIN_TEXT_WORDS,
                            "detail": r.get("detail")})
        else:
            landing = r.get("landing") or e.get("url") or \
                      (f"https://doi.org/{e['doi']}" if e.get("doi") else None)
            item = {"key": key, "title": e.get("title"), "author": e.get("author"),
                    "year": e.get("year"), "doi": e.get("doi"), "landing": landing,
                    "detail": r.get("detail") or r.get("lookup_note")}
            (missing_link if landing else missing_search).append(item)

    n_pdf = sum(1 for p in present if p["filename"].endswith(".pdf"))
    n_thin = sum(1 for p in present if p["thin"])
    n_mismatch = sum(1 for p in present if p["mismatch"])
    lines = ["# Source download status", ""]
    lines.append(f"**{len(present)} of {len(manifest_entries)} sources in `sources/`** "
                 f"({n_pdf} PDF, {len(present) - n_pdf} page-text"
                 + (f", ⚠ {n_thin} flagged thin" if n_thin else "")
                 + (f", ⚠ {n_mismatch} content mismatch" if n_mismatch else "")
                 + f") — {len(missing_link)} missing with a link — "
                 f"{len(missing_search)} missing, need a literature search.")

    wrong = [p for p in present if p["mismatch"]]
    if wrong:
        lines += ["", "## ⚠ Content mismatch — the file may be the WRONG document",
                  "", "The file's opening pages never mention the cited title. Open each "
                      "one and check; if wrong, delete it and fetch the real document "
                      "(same filename).", ""]
        for p in wrong:
            lines.append(f"- `{p['filename']}` — expected: {p['title']}")

    if missing_link:
        lines += ["", "## Missing — have a link (download manually)",
                  "", "Get these via your own (institutional/library) access, save into "
                      "`sources/` under the listed filename, fix the refs line if not .pdf.", ""]
        for r in missing_link:
            lines.append(f"- **{r['title'] or r['key']}**"
                         + (f" ({r['year']})" if r.get("year") else ""))
            lines.append(f"  - save as: `{r['key']}.pdf`"
                         + (f" — DOI: `{r['doi']}`" if r.get("doi") else ""))
            lines.append(f"  - landing page: {r['landing']}")
            if r.get("detail"):
                lines.append(f"  - note: {r['detail']}")
    if missing_search:
        lines += ["", "## Missing — no link or DOI was ever given; needs a literature "
                      "search by title/author", ""]
        for r in missing_search:
            lines.append(f"- **{r['title'] or r['key']}** — {r.get('author') or 'unknown author'}"
                         + (f", {r['year']}" if r.get("year") else "")
                         + f" — save as `{r['key']}.pdf`")
            if r.get("detail"):
                lines.append(f"  - note: {r['detail']}")

    thin = [p for p in present if p["thin"]]
    if thin:
        lines += ["", "## ⚠ In sources/ but suspiciously thin — check these",
                  "", "Probably a landing page, cookie wall, or abstract rather than the "
                      "full source. Open each file; if incomplete, replace it manually "
                      "(keep the same filename).", ""]
        for p in thin:
            lines.append(f"- `{p['filename']}` — {p['title'] or p['key']} ({p['words']} words)")
    lines += ["", "## In sources/ — ready", ""]
    for p in present:
        if p["thin"] or p["mismatch"]:
            continue
        size = f"{p['words']} words" if p["words"] is not None else "PDF"
        note = f" — ⚠ {p['detail']}" if p.get("detail") and "no extractable text" in str(p["detail"]) else ""
        lines.append(f"- `{p['filename']}` — {p['title'] or p['key']} ({size}){note}")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return {"present": len(present), "thin": n_thin, "mismatch": n_mismatch,
            "missing_link": len(missing_link), "missing_search": len(missing_search)}


def main():
    ap = argparse.ArgumentParser(description="Download the sources listed in a sources_manifest.json.")
    ap.add_argument("--manifest", required=True, help="Path to sources_manifest.json")
    ap.add_argument("--sources-dir", help="Where to save sources (default: <manifest dir>/sources)")
    ap.add_argument("--refs", help="Refs file to fix extensions in (default: <manifest dir>/my_text.md.refs.txt)")
    ap.add_argument("--keys", help="Comma-separated subset of keys to fetch (for smoke tests)")
    ap.add_argument("--force", action="store_true",
                    help="Re-fetch even if a file exists (old file is kept if the re-fetch fails)")
    ap.add_argument("--limit", type=int, help="Process at most N entries")
    ap.add_argument("--delay", type=float, default=2.0, help="Base delay between sources in seconds (default: 2)")
    ap.add_argument("--no-title-search", action="store_true",
                    help="Skip the Semantic Scholar lookup for entries with no url/DOI")
    ap.add_argument("--report-only", action="store_true",
                    help="Just regenerate download_report.md from the manifest + sources/ "
                         "on disk — no network access")
    args = ap.parse_args()

    if not os.path.exists(args.manifest):
        logger.error(f"Manifest not found: {args.manifest}"); sys.exit(1)
    with open(args.manifest, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    base_dir = os.path.dirname(os.path.abspath(args.manifest))
    sources_dir = args.sources_dir or os.path.join(base_dir, "sources")
    refs_path = args.refs or os.path.join(base_dir, "my_text.md.refs.txt")
    os.makedirs(sources_dir, exist_ok=True)

    all_entries = manifest.get("sources", [])
    report_path = os.path.join(base_dir, "download_report.md")

    if args.report_only:
        counts = write_report(report_path, all_entries, sources_dir, {})
        print(f"{counts['present']}/{len(all_entries)} sources present"
              + (f" (⚠ {counts['thin']} thin)" if counts["thin"] else "")
              + (f" (⚠ {counts['mismatch']} content mismatch)" if counts["mismatch"] else "")
              + f", {counts['missing_link']} missing with a link, "
              f"{counts['missing_search']} missing needing a literature search.\n"
              f"Report: {report_path}")
        return

    entries = all_entries
    if args.keys:
        wanted = {k.strip() for k in args.keys.split(",")}
        entries = [e for e in entries if e["key"] in wanted]
        missing = wanted - {e["key"] for e in entries}
        if missing:
            logger.warning(f"Keys not in manifest: {sorted(missing)}")
    if args.limit:
        entries = entries[:args.limit]
    if not entries:
        logger.error("No manifest entries to process."); sys.exit(1)

    session = dd.setup_session()
    start = time.time()
    fetched, skipped, not_fetchable, needs_search = [], [], [], []
    s2_failures = 0  # consecutive lookup failures -> stop hammering a rate-limited API

    for i, raw in enumerate(entries):
        entry = dd.normalize_entry(raw)
        meta = {"title": raw.get("title"), "author": raw.get("author"), "year": raw.get("year")}

        if entry["status"] == "needs_search" and not (entry["url"] or entry["doi"]):
            if args.no_title_search:
                needs_search.append({**entry, **meta}); continue
            if s2_failures >= 2:
                needs_search.append({**entry, **meta,
                                     "lookup_note": "Semantic Scholar lookup skipped "
                                     "(rate-limited earlier in this run) — re-run later"})
                continue
            paper, lookup_status = s2.find_paper_by_title(entry["title"], entry.get("year"))
            if lookup_status == "search_failed":
                s2_failures += 1
                needs_search.append({**entry, **meta,
                                     "lookup_note": "Semantic Scholar lookup failed "
                                     "(rate limit/network) — re-run later or add an API key "
                                     "(config/semantic_scholar_api_key.txt)"})
                continue
            s2_failures = 0
            if paper is None:
                needs_search.append({**entry, **meta})
                continue
            entry = s2.enrich_entry_from_s2(entry, paper)

        backups = {}
        if args.force:
            for ext in (".pdf", ".txt"):
                p = os.path.join(sources_dir, entry["key"] + ext)
                if os.path.exists(p):
                    os.replace(p, p + ".bak")
                    backups[p] = p + ".bak"

        result = {**dd.download_source(entry, sources_dir, session, force=args.force), **meta}

        if backups:
            if result["outcome"] in ("pdf", "pdf_no_text", "text", "text_thin"):
                for b in backups.values():
                    os.remove(b)
            else:  # re-fetch failed -> keep what we had
                for p, b in backups.items():
                    os.replace(b, p)
                result = {**result, "outcome": "already_present",
                          "filename": os.path.basename(next(iter(backups))),
                          "detail": "re-fetch failed; kept the previous file"}

        if result["outcome"] == "already_present":
            skipped.append(result)
        elif result["outcome"] == "not_fetchable":
            not_fetchable.append({**result, "doi": entry.get("doi")})
        else:
            fetched.append(result)

        if i < len(entries) - 1:
            time.sleep(args.delay + random.uniform(0, args.delay * 0.5))

    # fix refs extensions for anything saved as .txt
    actual = {r["key"]: r["filename"] for r in fetched + skipped if r.get("filename")}
    rewrite_refs(refs_path, actual)

    run_results = {r["key"]: r for r in fetched + skipped + not_fetchable + needs_search}
    counts = write_report(report_path, all_entries, sources_dir, run_results)

    pdf_warn = [r for r in fetched if r["outcome"] == "pdf_no_text"]
    thin_warn = [r for r in fetched if r["outcome"] == "text_thin"]
    print(f"\nDone in {time.time() - start:.0f}s — {len(fetched)} fetched "
          f"({sum(1 for r in fetched if r['outcome'].startswith('pdf'))} PDF, "
          f"{sum(1 for r in fetched if r['outcome'].startswith('text'))} page-text), "
          f"{len(skipped)} already present, {len(not_fetchable)} not fetchable, "
          f"{len(needs_search)} need a literature search.")
    if pdf_warn:
        print(f"⚠ {len(pdf_warn)} PDF(s) have no extractable text (scan/broken) — "
              f"see the report: {', '.join(r['key'] for r in pdf_warn)}")
    if thin_warn:
        print(f"⚠ {len(thin_warn)} page-text file(s) are suspiciously thin (landing page/"
              f"cookie wall/abstract?) — check and replace manually if incomplete: "
              f"{', '.join(r['key'] for r in thin_warn)}")
    if not_fetchable:
        print("\nNot fetchable openly (get these via your own access):")
        for r in not_fetchable:
            print(f"  - {r['title'] or r['key']}: {r['landing'] or 'no link'}")
    if needs_search:
        print(f"\n{len(needs_search)} source(s) need a literature search by title/author "
              f"(no link/DOI given, no confident Semantic Scholar match):")
        for r in needs_search:
            print(f"  - {r['key']}: {r['title']}")
    print(f"\nOverall: {counts['present']}/{len(all_entries)} sources present"
          + (f" (⚠ {counts['thin']} thin)" if counts["thin"] else "")
          + (f" (⚠ {counts['mismatch']} content mismatch — see report)" if counts["mismatch"] else "")
          + f" — full status: {report_path}")


if __name__ == "__main__":
    main()
