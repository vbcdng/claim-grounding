"""Card 398 (branch B), repair step 2: convert each work listed in raw_pdf_works.json to text with
pdftotext (the project's PDF route; no web download) into repaired_texts/, and write
repaired_texts/map.json {original file: repaired text file}. A work pdftotext cannot read (missing
file, error, or under 500 characters of text) is marked "not readable" and left out of the map.
The original files are never touched. Usage: pdfrepair.py"""
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
OUT = HERE / "repaired_texts"
MIN_CHARS = 500


def target(rel):
    return OUT / (re.sub(r"[^A-Za-z0-9._-]+", "__", rel)[:180] + ".txt")


def convert(rel):
    src = REPO / rel
    if not src.is_file():
        return None, "file missing"
    dst = target(rel)
    # same call as the tool's source reader (modules/papertrail/source_decomposer.py)
    r = subprocess.run(["pdftotext", "-enc", "UTF-8", str(src), str(dst)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not dst.is_file():
        return None, f"pdftotext exit {r.returncode}: {r.stderr.strip()[:200]}"
    text = dst.read_text(encoding="utf-8", errors="replace")
    if len(text.strip()) < MIN_CHARS:
        dst.unlink()
        return None, f"only {len(text.strip())} characters of text"
    return dst, len(text)


def main():
    listed = json.loads((HERE / "raw_pdf_works.json").read_text(encoding="utf-8"))
    OUT.mkdir(exist_ok=True)
    mapping, report = {}, []
    for w in listed["works"]:
        dst, info = convert(w["file"])
        if dst is None:
            report.append({"file": w["file"], "readable": False, "why": info})
            print(f"NOT READABLE {w['file']}: {info}")
            continue
        rel = str(dst.relative_to(REPO))
        mapping[w["file"]] = rel
        report.append({"file": w["file"], "readable": True, "text": rel, "chars": info,
                       "old_chars": w["chars"]})
        print(f"ok {w['file']} {w['chars']} -> {info} chars")
    (OUT / "map.json").write_text(json.dumps(mapping, indent=1) + "\n", encoding="utf-8")
    (OUT / "report.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(f"readable {len(mapping)} of {len(listed['works'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
