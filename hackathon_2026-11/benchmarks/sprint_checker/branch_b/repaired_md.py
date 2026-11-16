"""Card 398 (branch B), repair step 6: write repaired_texts.md (the repaired works, the pieces that
cite them per half, and the new answer files) from raw_pdf_works.json and repaired_texts/report.json.
Usage: repaired_md.py"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ANS = HERE / "answers"
FILES = ["haiku_repaired.jsonl", "sonnet_repaired.jsonl", "opus_repaired.jsonl", "gemma_repaired.jsonl",
         "jev_repaired.jsonl"]


def count(path):
    if not path.exists():
        return "not there yet"
    ds = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    bad = sum(1 for d in ds if d.get("failed") or d.get("answer") is None)
    return f"{len(ds)} answers, {bad} failed"


def main():
    listed = json.loads((HERE / "raw_pdf_works.json").read_text(encoding="utf-8"))
    rep = {r["file"]: r for r in json.loads((HERE / "repaired_texts" / "report.json").read_text(encoding="utf-8"))}
    L = ["# Repaired cited works (card 398, branch B, 15 Nov)", "",
         f"{len(listed['works'])} cited works of the 300 sprint pieces were stored as raw PDF bytes, so every model was "
         f"given unreadable data for the {len(listed['tuning_pieces'])} tuning pieces and "
         f"{len(listed['held_back_pieces'])} held-back pieces that cite them. `pdfscan.py` found them (it reads the "
         "pieces without their labels); `pdfrepair.py` turned each PDF into text with pdftotext, the same call the "
         "tool's own source reader makes, into `repaired_texts/` (the original files are unchanged); "
         "`repaired_texts/map.json` maps each original file to its text. `ask_repaired.py` runs the unchanged "
         "`shared/ask.py` with these texts in place of the raw bytes, and `build_answers.py` prefers the new answers "
         "over the old ones of the same pieces. Branch A can use the same map and the same answer files.", "",
         "## Works", "", "| Original file | Text file | Characters before / after | Tuning pieces | Held-back pieces |",
         "|---|---|---|---|---|"]
    for w in listed["works"]:
        r = rep.get(w["file"], {})
        text = f"`{Path(r['text']).name}`" if r.get("readable") else f"not readable ({r.get('why')})"
        L.append(f"| `{w['file']}` | {text} | {w['chars']:,} / {r.get('chars', 0):,} | "
                 f"{', '.join(w['pieces']['tuning']) or '-'} | {', '.join(w['pieces']['held_back']) or '-'} |")
    L += ["", "## New answer files (folder `answers/`)", ""]
    for f in FILES:
        L.append(f"- `{f}`: {count(ANS / f)}")
    L += ["", "Answers are lines of the file: a failed call that was asked again stays as its own line. The 2 failed "
          "Sonnet lines are the held-back piece c312_vb200.r0.p1 (two tries, the reply could not be read), so Sonnet "
          "has no repaired answer there and its old answer stays. Opus was asked on the 5 repaired tuning pieces "
          "round 12 reads Opus on and on all 11 held-back pieces, chosen without reading any held-back answer.", "",
          "Held-back answers are only saved; the card session neither scores nor reads them.", ""]
    (HERE / "repaired_texts.md").write_text("\n".join(L), encoding="utf-8")
    print("repaired_texts.md written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
