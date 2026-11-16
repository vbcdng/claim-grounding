"""Card 405, change 1 of Fable 5.2's third answer: split the Opus objection replies on Sonnet's "no"
(answers/opus_sonnet_no.jsonl) and the tuning ids of that pool.

  ./venv/bin/python3 benchmarks/sprint_checker/branch_a/derive_sonnet_no.py ids
      writes answers/sonnet_no_tuning.txt and answers/sonnet_no_heldback.txt from answers/sonnet_no_ids.txt
  ./venv/bin/python3 benchmarks/sprint_checker/branch_a/derive_sonnet_no.py derive
      writes answers/opus_sonnet_no_diff.jsonl: the same lines, with "answer" recoded from the reply's
      "difference" (combine.py reads only "answer"): c (the first reader is mistaken) -> supported,
      b (real but a fair rewording) -> unsure, a (real and changes the meaning) -> not_supported.
      A reply without a readable "difference" is marked failed. Reads no label.
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "shared"))
from sprint_shared_load import sibling  # noqa: E402

RECODE = {"c": "supported", "b": "unsure", "a": "not_supported"}


def main(what):
    a = HERE / "answers"
    if what == "ids":
        tuning = sibling("split").allowed_ids(False)
        ids = [x.strip() for x in (a / "sonnet_no_ids.txt").read_text().splitlines() if x.strip()]
        t = [i for i in ids if i in tuning]
        (a / "sonnet_no_tuning.txt").write_text("".join(i + "\n" for i in t))
        (a / "sonnet_no_heldback.txt").write_text("".join(i + "\n" for i in ids if i not in tuning))
        print(f"tuning {len(t)}, held-back {len(ids) - len(t)}")
        return 0
    out = []
    for line in (a / "opus_sonnet_no.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        # the saved reply is cut short and may sit in a code fence, so read only the "difference" letter
        m = re.search(r'"difference"\s*:\s*"([abc])"', r.get("reply") or "")
        diff = m.group(1) if m else None
        if not r.get("failed"):
            if diff in RECODE:
                r["answer"] = RECODE[diff]
            else:
                r["failed"] = True
        r["difference"] = diff
        out.append(json.dumps(r, ensure_ascii=False))
    (a / "opus_sonnet_no_diff.jsonl").write_text("".join(x + "\n" for x in out))
    print(f"{len(out)} lines -> answers/opus_sonnet_no_diff.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "derive"))
