"""Card 398: print a wrong-rejections list (from analyse.py --list) short enough to read."""
import json
import sys

for line in open(sys.argv[1], encoding="utf-8"):
    d = json.loads(line)
    qc = d["quote_check"]
    print(f"## {d['piece_id']}  kind={d.get('label_kind')} door={d.get('door')} tags={d.get('tags')} "
          f"jev={d['jev_score']} g3={d.get('gemma3')}")
    print("PIECE:", d["piece"][:300])
    print(f"REASON: {d['reason_kind']} | DETAIL: {(d['detail'] or '')[:300]}")
    print(f"QUOTE (in work={d['quote_in_work']}, check={qc.get('passed')}): {(d['quote'] or '')[:300]}")
    print()
