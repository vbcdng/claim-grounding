#!/usr/bin/env python3
"""Task #19 (2026-09-03): build two copies of the same result page, one drawn the
way it looked before today's change and one after, so the author can put them
side by side and see exactly what moved on a card whose cited file is missing.

The "before" copy is produced by switching off the single new helper
(viewer.unchecked_objection); nothing else differs, which is the point.

    venv/bin/python3 benchmarks/task19_notchecked_preview.py
    -> data/task19_notchecked_preview/{before,after}.html
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.papertrail import viewer, viewer_v2

NEW_LEGEND_ROW = ('<div class="legrow"><span class="badge notchecked">NOT CHECKED</span> '
                  'the cited file is missing from the sources folder, or its text could not '
                  'be read \u2014 nothing about this sentence was judged</div>')
NEW_UNSUPPORTED_LEGEND = ("the cited sources were read and none of them backs the claim "
                          "as a whole")
OLD_UNSUPPORTED_LEGEND = ("no cited source backs the claim as a whole (or the source file "
                          "is missing)")

RUN = Path("data/eggs_run2/run")          # holds card t39, cited file missing
OUT = Path("data/task19_notchecked_preview")


def main():
    analysis = json.loads((RUN / "analysis.json").read_text())
    OUT.mkdir(parents=True, exist_ok=True)

    real = viewer.unchecked_objection
    try:
        viewer.unchecked_objection = lambda c: ""        # the old display
        viewer_v2.unchecked_objection = lambda c: ""
        viewer_v2.generate(analysis, str(OUT / "before.html"))
        # The page's legend is fixed text, not drawn through the helper, so the
        # "before" copy is put back to the old two lines by hand — otherwise the
        # comparison would show today's legend row on both sides.
        old = OUT / "before.html"
        page = old.read_text()
        for gone in (NEW_LEGEND_ROW,):
            page = page.replace(gone, "")
        page = page.replace(NEW_UNSUPPORTED_LEGEND, OLD_UNSUPPORTED_LEGEND)
        old.write_text(page)
    finally:
        viewer.unchecked_objection = real
        viewer_v2.unchecked_objection = real
    viewer_v2.generate(analysis, str(OUT / "after.html"))

    ids = [c["id"] for c in analysis["text_claims"]
           if str(c.get("reason") or "").startswith(("source_file_missing",
                                                     "no_source_sentences"))]
    print(f"wrote {OUT}/before.html and {OUT}/after.html")
    print("cards to compare:", ", ".join(ids) or "(none in this run)")


if __name__ == "__main__":
    main()
