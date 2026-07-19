#!/usr/bin/env python3
"""Dataset + agreement metrics for a canonical JSONL. Writes
metrics_<base>.json next to it and prints a summary. Pure stdlib, $0.

Covers: claim counts, auto-flag (junk) rate, fragmentation (claims per 1000
words, ~6 chars/word, from the docs_ sidecar), reviewed counts, human/LLM
confusion matrix + Cohen's kappa, and the disagreement rows (the
prompt-improvement material).
"""
import json
import os
import sys


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: metrics.py <file.jsonl>")
    path = sys.argv[1]
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    base = os.path.splitext(os.path.basename(path))[0]
    out_dir = os.path.dirname(os.path.abspath(path))
    sidecar = os.path.join(out_dir, "docs_" + base + ".json")

    m = {"file": os.path.basename(path),
         "tool": rows[0]["tool"] if rows else "?",
         "corpus": rows[0]["corpus"] if rows else "?",
         "claims": len(rows),
         "docs": len({r["doc_id"] for r in rows})}

    flagged = [r for r in rows if r["auto_flags"]]
    m["auto_flagged"] = len(flagged)
    m["auto_flag_rate"] = round(len(flagged) / max(1, len(rows)), 4)
    per_flag = {}
    for r in flagged:
        for f in r["auto_flags"]:
            per_flag[f] = per_flag.get(f, 0) + 1
    m["auto_flags_by_kind"] = per_flag

    if os.path.exists(sidecar):
        docs = json.load(open(sidecar, encoding="utf-8"))
        chars = sum(d.get("source_text_chars", 0) for d in docs)
        words = chars / 6.0
        m["source_words_est"] = int(words)
        m["claims_per_1000_words"] = round(len(rows) / max(1.0, words) * 1000, 2)
        # coverage red flag: documents that yielded NO claims at all
        have = {r["doc_id"] for r in rows}
        m["docs_total"] = len(docs)
        m["zero_claim_docs"] = [
            {"doc_id": d["doc_id"], "sentences": d.get("num_sentences", 0)}
            for d in docs if d["doc_id"] not in have]

    hum = [r for r in rows if r["human_ok"] in ("y", "n")]
    llm = [r for r in rows if r["llm_ok"] in ("y", "n")]
    m["human_reviewed"] = len(hum)
    m["llm_reviewed"] = len(llm)
    if hum:
        m["human_bad_rate"] = round(
            sum(1 for r in hum if r["human_ok"] == "n") / len(hum), 4)

    both = [r for r in rows if r["human_ok"] in ("y", "n")
            and r["llm_ok"] in ("y", "n")]
    if both:
        cm = {"yy": 0, "yn": 0, "ny": 0, "nn": 0}
        for r in both:
            cm[r["human_ok"] + r["llm_ok"]] += 1
        n = len(both)
        po = (cm["yy"] + cm["nn"]) / n
        ph_y = (cm["yy"] + cm["yn"]) / n
        pl_y = (cm["yy"] + cm["ny"]) / n
        pe = ph_y * pl_y + (1 - ph_y) * (1 - pl_y)
        kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0
        m["confusion_HL"] = cm
        m["agreement"] = round(po, 4)
        m["cohens_kappa"] = round(kappa, 4)
        m["disagreements"] = [
            {"claim_id": r["claim_id"], "human": r["human_ok"],
             "llm": r["llm_ok"], "llm_reason": r.get("llm_reason", ""),
             "note": r.get("note", ""), "claim": r["claim"]}
            for r in both if r["human_ok"] != r["llm_ok"]]

    out = os.path.join(out_dir, "metrics_" + base + ".json")
    json.dump(m, open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    for k, v in m.items():
        if k != "disagreements":
            print(f"{k}: {v}")
    if "disagreements" in m:
        print(f"disagreements: {len(m['disagreements'])} (see {out})")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
