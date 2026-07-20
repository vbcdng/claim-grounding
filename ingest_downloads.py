#!/usr/bin/env python3
"""
ingest_downloads.py — pick up manually-downloaded sources from an inbox folder,
match them to the manifest, and file them into sources/ under the right names.

Workflow: download_report.md lists what's missing; you download those papers
with whatever filenames the publishers give them and drop them into the inbox
(default: <manifest dir>/inbox/). This script matches each file to its manifest
entry (key in the filename, DOI found inside the PDF, or title match), renames
it to <key>.pdf / <key>.txt, moves it into sources/, updates the refs file, and
regenerates download_report.md. Files it cannot match confidently are left in
the inbox and listed with what to do.

No LLM calls, no network — pure local file handling. Safe to re-run any time.

Usage:
  venv/bin/python3 ingest_downloads.py --manifest data/paper1_import/sources_manifest.json
  venv/bin/python3 ingest_downloads.py --manifest ... --dry-run   # preview only
"""

import os
import sys
import logging
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.papertrail import source_ingestor
from modules.papertrail import direct_downloader as dd
import download_sources

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ingest_downloads")


def main():
    ap = argparse.ArgumentParser(description="File manually-downloaded sources from an inbox into sources/.")
    ap.add_argument("--manifest", required=True, help="Path to sources_manifest.json")
    ap.add_argument("--inbox", help="Folder with your downloaded files (default: <manifest dir>/inbox)")
    ap.add_argument("--sources-dir", help="Sources folder (default: <manifest dir>/sources)")
    ap.add_argument("--refs", help="Refs file to update (default: <manifest dir>/my_text.md.refs.txt)")
    ap.add_argument("--copy", action="store_true",
                    help="Copy matched files instead of moving them — use when --inbox "
                         "points at a folder you keep, like ~/Downloads")
    ap.add_argument("--dry-run", action="store_true", help="Only show what would happen")
    args = ap.parse_args()

    if not os.path.exists(args.manifest):
        logger.error(f"Manifest not found: {args.manifest}"); sys.exit(1)
    import json
    with open(args.manifest, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    entries = manifest.get("sources", [])

    base_dir = os.path.dirname(os.path.abspath(args.manifest))
    inbox_dir = args.inbox or os.path.join(base_dir, "inbox")
    sources_dir = args.sources_dir or os.path.join(base_dir, "sources")
    refs_path = args.refs or os.path.join(base_dir, "my_text.md.refs.txt")
    os.makedirs(inbox_dir, exist_ok=True)
    os.makedirs(sources_dir, exist_ok=True)

    files = source_ingestor.scan_inbox(inbox_dir)
    if not files:
        print(f"Inbox is empty: {inbox_dir}\n"
              f"Drop downloaded sources there (any filename; .pdf/.txt/.html) and re-run.")
        return

    def has_file(key):
        return any(os.path.exists(os.path.join(sources_dir, key + ext))
                   for ext in (".pdf", ".txt"))

    to_ingest, blocked, unmatched = source_ingestor.plan_ingest(files, entries, has_file)

    ingested = []
    for path, entry, how in to_ingest:
        filename, warning = source_ingestor.ingest_file(path, entry["key"], sources_dir,
                                                        dry_run=args.dry_run,
                                                        copy=args.copy)
        # Key-named files skip content matching entirely — a plausible-but-wrong
        # paper saved under the right key would land unnoticed. Advisory check.
        if not args.dry_run and not warning:
            if dd.content_check(os.path.join(sources_dir, filename),
                                entry.get("title"), entry.get("author")) == "mismatch":
                warning = (f"file never mentions the cited title ({entry.get('title')!r}) "
                           f"— check it's the right document")
        ingested.append((os.path.basename(path), filename, how, warning, entry.get("title")))

    if not args.dry_run and ingested:
        download_sources.rewrite_refs(refs_path, {f.split(".")[0]: f for _, f, _, _, _ in ingested})
        report_path = os.path.join(base_dir, "download_report.md")
        counts = download_sources.write_report(report_path, entries, sources_dir, {})
    else:
        counts = None

    prefix = "[dry-run] would ingest" if args.dry_run else "Ingested"
    print(f"\n{prefix} {len(ingested)} file(s):")
    for name, filename, how, warning, title in ingested:
        print(f"  {name}  ->  sources/{filename}   ({how})")
        if warning:
            print(f"    ⚠ {warning}")
    if blocked:
        print(f"\nSkipped {len(blocked)} file(s):")
        for path, key, why in blocked:
            print(f"  {os.path.basename(path)}  ~  {key}: {why}")
    if unmatched:
        print(f"\nNo confident match for {len(unmatched)} file(s) — left where they are"
              " (unrelated files are fine to ignore):")
        for path, note in unmatched[:15]:
            print(f"  {os.path.basename(path)}: {note}")
        if len(unmatched) > 15:
            print(f"  ... and {len(unmatched) - 15} more")
    if counts:
        print(f"\nOverall: {counts['present']}/{len(entries)} sources present"
              + (f" (⚠ {counts['thin']} thin)" if counts["thin"] else "")
              + " — download_report.md updated.")


if __name__ == "__main__":
    main()
