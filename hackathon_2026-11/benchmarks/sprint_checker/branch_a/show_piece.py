"""Card 397 (branch A): print a piece, its work file(s) and the work text around a phrase, to quote the
whole passage when a possible answer-key error is put to the author.

  show_piece.py <piece_id> <phrase> [--chars 700]
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

records = sibling("records")


def main(argv):
    pid, phrase = argv[0], argv[1]
    chars = int(argv[argv.index("--chars") + 1]) if "--chars" in argv else 700
    rec = next(r for r in records.load_pieces() if r["piece_id"] == pid)
    print("keys:", sorted(rec))
    print("piece:", rec["piece"])
    for k in ("original_sentence", "earlier_sentence", "certainty"):
        if rec.get(k):
            print(f"{k}:", rec[k])
    for w in rec.get("cited_works") or []:
        print("cited work record:", {k: v for k, v in w.items() if k != "text"} if isinstance(w, dict) else w)
        rel = w if isinstance(w, str) else (w.get("path") or w.get("file") or w.get("text_path") or "")
        print("work:", rel)
        text = records.work_text(rel)
        i = text.lower().find(phrase.lower())
        print("found at", i)
        if i >= 0:
            print(text[max(0, i - chars):i + chars])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
