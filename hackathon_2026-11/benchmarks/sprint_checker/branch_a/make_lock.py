"""Card 397 (branch A): write questions.lock, the sha1 of every question file in questions/ (the
freeze of the question files, plan of card 392), or check the files against an existing lock.

  make_lock.py           write questions.lock
  make_lock.py --check   exit 1 when a question file differs from the lock or is missing from it
"""
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
Q = HERE / "questions"
LOCK = HERE / "questions.lock"


def hashes():
    return {p.name: hashlib.sha1(p.read_bytes()).hexdigest() for p in sorted(Q.glob("*.md"))}


def main(argv):
    now = hashes()
    if "--check" in argv:
        lock = json.loads(LOCK.read_text(encoding="utf-8"))["sha1"]
        bad = sorted(k for k in set(now) | set(lock) if now.get(k) != lock.get(k))
        print("question files match the lock" if not bad else f"differ from the lock: {bad}")
        return 1 if bad else 0
    LOCK.write_text(json.dumps({"card": 397, "frozen": "question files of branch A (plan of card 392)",
                                "sha1": now}, indent=1) + "\n", encoding="utf-8")
    print(f"{len(now)} question files locked in {LOCK.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
