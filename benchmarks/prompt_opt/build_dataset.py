#!/usr/bin/env python3
"""Build the prompt-optimization train/dev sets
(docs/PROMPT_OPTIMIZATION_PLAN_2026-07-30.md — "a legitimate train/dev set").

Sources of items (ONLY data allowed as tuning signal):
- WiCE rows (public labels). Consumed ids are excluded — the union of every
  wice_ground_truth.json under benchmarks/wice_runs/, benchmarks/wice_heldout/
  (the 512 burned held-out rows — wice_bench._used_wice_ids misses these) and
  data/first_check/. The domain blocklist and English probe from wice_bench
  apply. Only supported / not_supported rows (partial is ambiguous for a
  binary judge). WiCE train split -> TRAIN, dev split -> DEV; test stays
  untouched (fully consumed anyway).
- Constructed mutations from data/fresh2026_mut/CONSOLIDATED_scored.jsonl
  (batches hard2 + scaled; labels true by construction): the baseline claim
  (expected True) + its single-fact corruption (expected False), passage =
  the findings-bank verbatim sentences for the cited key. Split by SOURCE KEY
  so a passage never appears in both train and dev.

NEVER included (held out forever): the 11-item judge bench, author-ruled
gold labels, the eggs mutation bench, the 3-paper + coverage gates, and the
retreat_pilot pool (human-panel benchmark — consuming it is the author's
call).

Output: data/prompt_opt/{train,dev}.jsonl + manifest.json (data/ is
gitignored — WiCE text is not redistributable; the sets are regenerable).
No LLM calls, no network. Deterministic (seeded).
"""
import glob
import json
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "benchmarks"))
from wice_bench import _BLOCK, _probe_english  # noqa: E402

OUT_DIR = os.path.join(ROOT, "data", "prompt_opt")
WICE_DIR = os.path.join(ROOT, "data", "wice")
MUT_DIR = os.path.join(ROOT, "data", "fresh2026_mut")
MAX_PASSAGE_CHARS = 6000
SEED = 7


def consumed_wice_ids():
    pats = [
        os.path.join(ROOT, "benchmarks", "wice_runs", "*", "wice_ground_truth.json"),
        os.path.join(ROOT, "benchmarks", "wice_heldout", "*", "wice_ground_truth.json"),
        os.path.join(ROOT, "data", "first_check", "wice_ground_truth.json"),
    ]
    used = set()
    for pat in pats:
        for p in glob.glob(pat):
            with open(p, encoding="utf-8") as f:
                for v in json.load(f)["claims"].values():
                    used.add(v.get("wice_id"))
    used.discard(None)
    return used


def wice_items(split, used):
    path = os.path.join(WICE_DIR, f"{split}.jsonl")
    out = []
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    for it in rows:
        wid = it.get("meta", {}).get("id")
        label = it["label"]
        if label not in ("supported", "not_supported") or wid in used:
            continue
        claim = (it.get("claim") or "").strip()
        passage = "\n".join(it.get("evidence") or [])
        if not claim or not passage.strip():
            continue
        if len(passage) > MAX_PASSAGE_CHARS:
            continue  # drop, never truncate — truncation can cut the proof out
        if _BLOCK.search(claim + " " + passage):
            continue
        ok, _ = _probe_english(passage)
        if not ok:
            continue
        out.append({"id": f"wice_{wid}", "expected": label == "supported",
                    "claim": claim, "passage": passage,
                    "origin": f"wice/{split}"})
    return out


def mutation_items():
    banks = {}
    for path in (os.path.join(MUT_DIR, "constructed_batch2", "findings_bank.json"),
                 os.path.join(MUT_DIR, "scaled", "findings_bank_new.json")):
        for f in json.load(open(path, encoding="utf-8")):
            banks.setdefault(f["key"], []).append(f["verbatim"])

    texts = {}
    for batch, d in (("hard2", "constructed_batch2"), ("scaled", "scaled")):
        base = open(os.path.join(MUT_DIR, d, "my_text.md"), encoding="utf-8").read()
        mut = open(os.path.join(MUT_DIR, d, "my_text_mut.md"), encoding="utf-8").read()
        texts[batch] = (base.splitlines(), mut.splitlines())

    def find_line(lines, needle, key):
        hits = [ln for ln in lines if needle in ln and f"[[{key}]]" in ln]
        if len(hits) != 1:
            return None
        return re.sub(r"\s*\[\[[^\]]+\]\]\s*$", "", hits[0]).strip()

    items, skipped = [], []
    seen_base = set()
    for line in open(os.path.join(MUT_DIR, "CONSOLIDATED_scored.jsonl"),
                     encoding="utf-8"):
        r = json.loads(line)
        if not r.get("seed_ok"):
            skipped.append((r["batch"], r["id"], "seed_ok=false"))
            continue
        key = r["key"]
        base_lines, mut_lines = texts[r["batch"]]
        if r["class"] == "citation-swap":
            # the mutation swaps the [[marker]], not the words: the judge-item
            # form is the ORIGINAL claim against the swapped-in source's
            # findings — a true-in-the-world claim the passage doesn't back
            frag = r["old"].split(" [[")[0]
            claim = find_line(base_lines, frag, key)
            wrong_key = r["mut_cited"][0]
            passage = "\n".join(banks.get(wrong_key, []))
            if claim is None or not passage:
                skipped.append((r["batch"], r["id"], "citation-swap unresolvable"))
                continue
            items.append({"id": f"mut_{r['batch']}_{r['id']}", "expected": False,
                          "claim": claim, "passage": passage,
                          "origin": f"fresh2026/{r['batch']}", "key": wrong_key,
                          "class": r["class"], "incumbent_caught": r["caught"]})
            continue
        base_claim = find_line(base_lines, r["old"], key)
        mut_claim = find_line(mut_lines, r["new"], key)
        if base_claim is None or mut_claim is None:
            skipped.append((r["batch"], r["id"], "claim line not found/ambiguous"))
            continue
        passage = "\n".join(banks.get(key, []))
        if not passage:
            skipped.append((r["batch"], r["id"], "no findings for key"))
            continue
        tag = f"{r['batch']}_{r['id']}"
        if base_claim not in seen_base:  # several mutations can share a seed line
            seen_base.add(base_claim)
            items.append({"id": f"mutbase_{tag}", "expected": True,
                          "claim": base_claim, "passage": passage,
                          "origin": f"fresh2026/{r['batch']}", "key": key})
        items.append({"id": f"mut_{tag}", "expected": False,
                      "claim": mut_claim, "passage": passage,
                      "origin": f"fresh2026/{r['batch']}", "key": key,
                      "class": r["class"], "incumbent_caught": r["caught"]})
    return items, skipped


def main():
    rng = random.Random(SEED)
    used = consumed_wice_ids()
    print(f"consumed wice_ids excluded: {len(used)}")

    w_train = wice_items("train", used)
    w_dev = wice_items("dev", used)
    muts, skipped = mutation_items()
    for b, i, why in skipped:
        print(f"  mutation skipped {b}/{i}: {why}")

    # mutations: split by source key (passage never crosses the split)
    keys = sorted({m["key"] for m in muts})
    rng.shuffle(keys)
    n_dev_keys = max(1, len(keys) // 3)
    dev_keys = set(keys[:n_dev_keys])
    m_train = [m for m in muts if m["key"] not in dev_keys]
    m_dev = [m for m in muts if m["key"] in dev_keys]

    # WiCE supported rows are abundant — cap them so mutations (the scarce
    # FP-direction signal) keep weight; negatives are taken in full.
    def cap_supported(items, cap):
        pos = [i for i in items if i["expected"]]
        neg = [i for i in items if not i["expected"]]
        rng.shuffle(pos)
        return pos[:cap] + neg

    w_train = cap_supported(w_train, 55)
    w_dev = cap_supported(w_dev, 28)

    train = w_train + m_train
    dev = w_dev + m_dev
    rng.shuffle(train)
    rng.shuffle(dev)

    os.makedirs(OUT_DIR, exist_ok=True)
    for name, items in (("train", train), ("dev", dev)):
        with open(os.path.join(OUT_DIR, f"{name}.jsonl"), "w", encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps(it) + "\n")
        pos = sum(1 for i in items if i["expected"])
        wice_n = sum(1 for i in items if i["origin"].startswith("wice"))
        print(f"{name}: {len(items)} items ({pos} True / {len(items) - pos} False; "
              f"{wice_n} WiCE / {len(items) - wice_n} mutation-constructed)")

    manifest = {
        "seed": SEED, "max_passage_chars": MAX_PASSAGE_CHARS,
        "consumed_wice_ids_excluded": len(used),
        "mutation_dev_keys": sorted(dev_keys),
        "mutation_skipped": [f"{b}/{i}: {w}" for b, i, w in skipped],
        "held_out": ["11-item judge bench", "author gold labels",
                     "eggs mutation bench", "3-paper + coverage gates",
                     "retreat_pilot pool", "WiCE consumed ids (672)"],
        "counts": {"train": len(train), "dev": len(dev)},
    }
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    print(f"-> {OUT_DIR}")


if __name__ == "__main__":
    main()
