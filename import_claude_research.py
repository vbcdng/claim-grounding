#!/usr/bin/env python3
"""
import_claude_research.py — convert a Claude Science research export into this
tool's input format (see INPUT_FORMAT.md).

Reads a pandoc/citeproc-style markdown file (citations like [@key] or
[@key1; @key2]) plus its BibTeX bibliography, and writes into --output-dir:
  my_text.md            the prose with [[key]] markers at sentence ends
  my_text.md.refs.txt   key = filename map (suggested <key>.pdf names)
  sources_manifest.json url/DOI per cited key, for downloading the sources
  sources/              empty folder to drop the downloaded sources into

Then: download the sources listed in the manifest into sources/ (fix any
filename in the refs file that isn't a .pdf), and run verify_my_text.py with
--text <output-dir>/my_text.md --sources <output-dir>/sources.

No API calls — pure parsing, free to re-run.
"""

import os
import sys
import logging
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.papertrail import claude_research_importer

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("import_claude_research")


def main():
    ap = argparse.ArgumentParser(description="Import Claude Science markdown into the claim-grounding input format.")
    ap.add_argument("--input", required=True,
                    help="Claude Science markdown export (.md), or a .bib directly with --merge-into")
    ap.add_argument("--bib", help="Bibliography file (.bib); default: the one named in the markdown's frontmatter")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--output-dir", help="Where to write my_text.md, refs, manifest, sources/ (new project)")
    group.add_argument("--merge-into", metavar="PROJECT_DIR",
                       help="Merge ONLY the export's sources into an existing project "
                            "(the dir holding sources_manifest.json + *.refs.txt). For the "
                            "review loop: Claude Science found replacement sources for "
                            "rejected claims — its text is discarded, its bibliography is "
                            "appended (duplicates skipped by DOI/title). Then run "
                            "download_sources.py and re-cite the claims.")
    args = ap.parse_args()

    if not os.path.exists(args.input):
        logger.error(f"Input file not found: {args.input}"); sys.exit(1)

    if args.merge_into:
        try:
            s = claude_research_importer.merge_sources(args.input, args.merge_into,
                                                       bib_path=args.bib)
        except ValueError as e:
            logger.error(str(e)); sys.exit(1)
        print(f"\nMerged {len(s['added'])} new source(s) into {args.merge_into}: "
              + (", ".join(s["added"]) or "—"))
        for d in s["skipped"]:
            print(f"  skipped {d['key']} — already present as {d['existing_key']} ({d['why']})")
        for r in s["renamed"]:
            print(f"  renamed {r['from']} -> {r['to']} (key already used by a different work)")
        if s["needs_search"]:
            print(f"\n{len(s['needs_search'])} new source(s) have no url/DOI — find by title "
                  f"(status=needs_search): " + ", ".join(s["needs_search"]))
        if s["added"]:
            print(f"\nNext: python3 download_sources.py --manifest {s['manifest']}\n"
                  f"then cite the new [[key]]s in your text and re-run verify_my_text.py "
                  f"(incremental — only re-cited claims are judged).")
        return

    summary = claude_research_importer.run_import(args.input, args.output_dir, bib_path=args.bib)

    print(f"\nImported: {summary['citations_converted']} citations, "
          f"{summary['unique_keys']} unique sources")
    print(f"  text:     {summary['text']}")
    print(f"  refs:     {summary['refs']}")
    print(f"  manifest: {summary['manifest']}")
    if summary["unresolved_keys"]:
        print(f"\n⚠ {len(summary['unresolved_keys'])} cited key(s) missing from the bibliography: "
              + ", ".join(summary["unresolved_keys"]))
    if summary["needs_search"]:
        print(f"\n{len(summary['needs_search'])} source(s) have no url/DOI — find these by "
              f"title/author (see manifest, status=needs_search):")
        for k in summary["needs_search"]:
            print(f"  - {k}")
    print("\nNext: put the source files into "
          f"{os.path.join(os.path.dirname(summary['text']), 'sources')}/ "
          "(names per the refs file), then run verify_my_text.py.")


if __name__ == "__main__":
    main()
