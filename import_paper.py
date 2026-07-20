#!/usr/bin/env python3
"""
Import ANY published scientific paper into this tool's input format, so its
claims can be verified against its own cited sources.

Database-first, PDF-parsing last: the paper is identified (DOI / arXiv / title
with a never-guess gate), its reference list is fetched STRUCTURED from
Semantic Scholar / OpenAlex, and only the body prose comes from the PDF. An
in-text citation that can't be resolved with confidence gets no [[key]] marker
and is listed in import_report.md instead.

Examples:
  python3 import_paper.py --pdf paper.pdf --output-dir data/mypaper
  python3 import_paper.py --doi 10.1037/amp0000904 --output-dir data/mypaper
  python3 import_paper.py --url https://doi.org/10.1086/718371 --output-dir data/mypaper

Then: download_sources.py --manifest <out>/sources_manifest.json  ->  verify_my_text.py

For docx, superscript-citation, or unindexed papers use the /import-paper
Claude Code command instead. Logic in modules/papertrail/paper_importer.py.
"""

import argparse
import logging
import sys

from modules.papertrail.paper_importer import PaperImportError, run_paper_import

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Convert a published paper (PDF/DOI/arXiv/URL/title) into the "
                    "verifier's input format — database-first bibliography.")
    src = ap.add_argument_group("paper (give at least one; --pdf may accompany an id)")
    src.add_argument("--pdf", help="local PDF of the paper (body text source)")
    src.add_argument("--doi", help="the paper's DOI, e.g. 10.1037/amp0000904")
    src.add_argument("--arxiv", help="arXiv id, e.g. 2401.12345")
    src.add_argument("--url", help="a doi.org / arxiv.org URL of the paper")
    src.add_argument("--title", help="exact title (never-guess S2 match)")
    ap.add_argument("--output-dir", required=True,
                    help="project dir to create (my_text.md, refs, manifest, sources/)")
    ap.add_argument("--keep-abstract", action="store_true",
                    help="keep the abstract in my_text.md (default: stripped — "
                         "abstracts are citation-free restatements)")
    ap.add_argument("--keep-appendix", action="store_true",
                    help="keep appendices (default: stripped)")
    args = ap.parse_args()

    if not any((args.pdf, args.doi, args.arxiv, args.url, args.title)):
        ap.error("give the paper as --pdf, --doi, --arxiv, --url or --title")

    try:
        s = run_paper_import(args.output_dir, pdf=args.pdf, doi=args.doi,
                             arxiv=args.arxiv, url=args.url, title=args.title,
                             keep_abstract=args.keep_abstract,
                             keep_appendix=args.keep_appendix)
    except PaperImportError as e:
        print(f"\nIMPORT STOPPED: {e}", file=sys.stderr)
        return 1

    print(f"\nImported: {s['paper'].get('title') or s['paper']['paper_id']}")
    print(f"  {s['citations_converted']} citation occurrence(s) -> "
          f"{s['unique_keys']} unique source(s) "
          f"({s['coverage']:.0%} of the reference list)")
    if s["unresolved_mentions"]:
        print(f"  {len(s['unresolved_mentions'])} unresolved citation(s) — "
              f"left unmarked, see the report")
    print(f"  text:     {s['text']}")
    print(f"  refs:     {s['refs']}")
    print(f"  manifest: {s['manifest']}")
    print(f"  report:   {s['report']}")
    print(f"\nNext: python3 download_sources.py --manifest {s['manifest']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
