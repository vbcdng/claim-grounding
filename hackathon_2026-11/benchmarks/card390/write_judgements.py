"""Card 390: the judgements file for the content reader (one line per piece label, text included).

Run: ./venv/bin/python3 benchmarks/card390/write_judgements.py <out.jsonl>
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main(out):
    pieces = {json.loads(l)["piece_id"]: json.loads(l) for l in open(HERE / "pieces_262.jsonl")}
    n = 0
    with open(out, "w") as f:
        for line in open(HERE / "piece_labels.jsonl"):
            l = json.loads(line)
            p = pieces[l["piece_id"]]
            f.write(json.dumps({
                "id": l["piece_id"], "text": p["piece"], "certainty": p["certainty"],
                "whole_claim": p["original_sentence"], "source_files": p["cited_works"],
                "ruling": l["label"], "kind": l["kind"], "decided": l["decided"],
                "quote": l["quote"], "reason": l["reason"],
                "doubtful_because": l["doubtful_because"]}, ensure_ascii=False) + "\n")
            n += 1
    print(n, "judgements written to", out)


if __name__ == "__main__":
    main(sys.argv[1])
