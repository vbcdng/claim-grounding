"""Card 398 (branch B): Fable's third answer, which tuning pieces need an Opus answer for round 12.
A piece needs one when Sonnet said not supported or unsure (the only pieces round 12 reads Opus on)
and Opus has no usable answer. Pieces whose work reached the models as raw PDF bytes (Jev failed and
Gemma, Haiku and Sonnet all unsure: longlook.py) are listed apart, because Opus gets the same bytes.
Writes the ids to ask in answers/opus_gaps.txt. Labels are not read. Usage: opus_gaps.py"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name):
    out = {}
    for x in (HERE / "answers" / name).read_text(encoding="utf-8").splitlines():
        if x.strip():
            d = json.loads(x)
            if d.get("repeat", 0) == 0:
                ok = not d.get("failed") and d.get("answer") is not None
                if ok or d["piece_id"] not in out:
                    out[d["piece_id"]] = d if ok else None
    return out


def main():
    tuning = (HERE / "answers" / "tuning_ids.txt").read_text().split()
    s, g, h, j, o = (load(f) for f in ("sonnet.jsonl", "gemma.jsonl", "haiku.jsonl", "jev.jsonl",
                                       "opus_neutral.jsonl"))
    ask, raw = [], []
    for pid in tuning:
        sa = (s.get(pid) or {}).get("answer")
        if sa not in ("not_supported", "unsure") or o.get(pid):
            continue
        ans = lambda t: (t.get(pid) or {}).get("answer")  # noqa: E731
        if j.get(pid) is None and ans(g) == ans(h) == sa == "unsure":
            raw.append(pid)
        else:
            ask.append(pid)
    (HERE / "answers" / "opus_gaps.txt").write_text("\n".join(ask) + "\n")
    print(f"Sonnet not supported or unsure, no Opus answer: {len(ask) + len(raw)}")
    print(f"  to ask: {len(ask)} -> answers/opus_gaps.txt")
    print(f"  left out (raw PDF bytes, all unsure, Jev failed): {len(raw)}: {' '.join(raw)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
