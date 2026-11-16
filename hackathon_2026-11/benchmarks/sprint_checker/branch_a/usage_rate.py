"""Card 397 (branch A): list-price cost per Claude call in a usage file (ask.py's <step>_usage.jsonl),
to compare this branch's rate with card S2's before a Haiku rewording is sized. No label is read.

  usage_rate.py <usage.jsonl> [more]
"""
import json
import sys


def main(paths):
    for p in paths:
        rows = [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()]
        ok = [r for r in rows if not r.get("is_error")]
        usd = sum(r.get("total_cost_usd_list_price") or 0 for r in ok)
        tin = sum((r.get("usage") or {}).get("cache_creation_input_tokens", 0)
                  + (r.get("usage") or {}).get("cache_read_input_tokens", 0)
                  + (r.get("usage") or {}).get("input_tokens", 0) for r in ok)
        print(f"{p}: {len(ok)} calls, ${usd:.2f} at list price, ${usd / max(len(ok), 1):.4f} per call, "
              f"{tin / max(len(ok), 1):,.0f} input tokens per call")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
