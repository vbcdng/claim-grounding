"""Card 398 (branch B), repair step 1: list every cited work of all 300 pieces (both halves) whose
stored text is raw PDF bytes, with the pieces that cite it. Reads pieces through records.load_pieces
(label fields removed), so no held-back label is read. Also looks for a PDF file beside the text.
Usage: pdfscan.py [--json OUT]"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent / "shared"
sys.path.insert(0, str(SHARED))
from sprint_shared_load import sibling  # noqa: E402

records = sibling("records")
split = sibling("split")
REPO = records.REPO


def is_raw_pdf(text):
    head = text[:2000]
    if head.lstrip().startswith("%PDF"):
        return True
    return text.count(" obj") > 20 and text.count("endobj") > 20 and "stream" in text


def pdf_candidates(rel):
    p = REPO / rel
    out = [p.with_suffix(".pdf"), p.parent / (p.stem + ".PDF")]
    out += sorted(p.parent.glob(p.stem.split(".")[0] + "*.pdf"))
    seen, keep = set(), []
    for c in out:
        if c.is_file() and c not in seen:
            seen.add(c)
            keep.append(str(c.relative_to(REPO)))
    return keep


def scan():
    tuning = split.allowed_ids(False)
    works = {}
    for r in records.load_pieces():
        half = "tuning" if r["piece_id"] in tuning else "held_back"
        for w in r.get("cited_works") or []:
            f = w.get("file")
            if not f or not (REPO / f).is_file():
                continue
            if f not in works:
                t = records.work_text(f)
                works[f] = {"file": f, "raw_pdf": is_raw_pdf(t), "chars": len(t),
                            "pdf_files": [], "pieces": {"tuning": [], "held_back": []}}
            works[f]["pieces"][half].append(r["piece_id"])
    bad = [w for w in works.values() if w["raw_pdf"]]
    for w in bad:
        w["pdf_files"] = pdf_candidates(w["file"])
    return works, bad


def main():
    works, bad = scan()
    print(f"works checked: {len(works)}; raw PDF bytes: {len(bad)}")
    pt = sorted({p for w in bad for p in w["pieces"]["tuning"]})
    ph = sorted({p for w in bad for p in w["pieces"]["held_back"]})
    print(f"pieces citing one: tuning {len(pt)}, held back {len(ph)}")
    for w in bad:
        print(f"  {w['file']} chars={w['chars']} pdf={w['pdf_files']} "
              f"tuning={len(w['pieces']['tuning'])} held_back={len(w['pieces']['held_back'])}")
    if "--json" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--json") + 1])
        out.write_text(json.dumps({"works": bad, "tuning_pieces": pt, "held_back_pieces": ph},
                                  indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
