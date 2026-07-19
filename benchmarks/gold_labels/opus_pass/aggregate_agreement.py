#!/usr/bin/env python3
"""Build agreement_full.md from every opus_pass/*_graded.jsonl.

Dedupe by (run, claim_id): corpus files win over batch files (owner decision
2026-07-17), and within one output file the LAST row per claim_id wins (a
retried error row is superseded). Rows whose Fable verdict maps to no
provability axis (not_rulable, true_claim_weak_evidence, ...) are counted
separately, never silently dropped. Idempotent — rerun after each batch.
"""
import glob
import json
import os
from collections import Counter, OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
GOLD_DIR = os.path.dirname(HERE)
OUT = os.path.join(HERE, "agreement_full.md")

# corpus-derived batches win over hand-picked batch files on (run, claim_id)
CORPUS_BASES = {os.path.splitext(os.path.basename(p))[0]
                for p in glob.glob(os.path.join(GOLD_DIR, "corpus", "*.jsonl"))}


def load_graded():
    per_batch = OrderedDict()
    for p in sorted(glob.glob(os.path.join(HERE, "*_graded.jsonl"))):
        base = os.path.basename(p)[:-len("_graded.jsonl")]
        rows = OrderedDict()               # claim_id -> last row wins
        for l in open(p, encoding="utf-8"):
            if l.strip():
                r = json.loads(l)
                rows[(r.get("run"), r["claim_id"])] = r
        per_batch[base] = rows
    return per_batch


def main():
    per_batch = load_graded()
    # global dedupe: corpus batches first, then batch files only for unseen keys
    chosen = {}
    order = sorted(per_batch, key=lambda b: (b not in CORPUS_BASES, b))
    for b in order:
        for key, r in per_batch[b].items():
            if key not in chosen:
                chosen[key] = (b, r)

    lines = ["# Fable-vs-Opus agreement — FULL PASS (2026-07-17)", "",
             "Grader: claude-code/opus, prompt pt_owner_grader_v2.txt (v2 AS-IS, "
             "owner decision: measure the rule-A fork corpus-wide before v3).",
             "Dedupe: (run, claim_id), corpus files over batch files, last row "
             "per file wins. Excluded by owner decision: 8 mutation rows "
             "(eggs_rerun_20260715+mutation) and minwage_argmap_demo (2 rows, "
             "no source_claims cache — not regenerated).", ""]

    # ---- per-batch sections ----
    lines += ["## Per-batch results", "",
              "| batch | graded | agree | disagree | n/a axis | errors | disagree ids |",
              "|---|---|---|---|---|---|---|"]
    for b, rows in per_batch.items():
        ok = [r for r in rows.values() if not r.get("error")]
        errs = [r for r in rows.values() if r.get("error")]
        agree = [r for r in ok if r.get("axis_agreement") == "agree"]
        dis = [r for r in ok if r.get("axis_agreement") == "DISAGREE"]
        na = [r for r in ok if r.get("axis_agreement") == "n/a"]
        lines.append(f"| {b} | {len(ok)} | {len(agree)} | {len(dis)} | {len(na)} "
                     f"| {len(errs)} | {', '.join(r['claim_id'] for r in dis) or '—'} |")
    lines.append("")

    # ---- corpus-wide summary (deduped) ----
    ok = [r for _, r in chosen.values() if not r.get("error")]
    errs = [(b, r) for b, r in chosen.values() if r.get("error")]
    agree = [r for r in ok if r.get("axis_agreement") == "agree"]
    dis = [r for r in ok if r.get("axis_agreement") == "DISAGREE"]
    na = [r for r in ok if r.get("axis_agreement") == "n/a"]
    lines += ["## Corpus-wide summary (deduped)", "",
              f"- graded rows: **{len(ok)}** (+{len(errs)} error rows pending retry)",
              f"- agree: **{len(agree)}**",
              f"- disagree: **{len(dis)}**",
              f"- axis n/a (unmapped Fable vocab, counted not dropped): **{len(na)}**",
              ""]
    if ok:
        rate = len(agree) / max(1, len(agree) + len(dis))
        lines.append(f"- agreement rate on rulable rows: **{rate:.1%}** "
                     f"({len(agree)}/{len(agree) + len(dis)})")
    na_vocab = Counter(r.get("fable_verdict") for r in na)
    if na_vocab:
        lines += ["", "n/a rows by Fable verdict value: "
                  + ", ".join(f"`{k}`×{v}" for k, v in na_vocab.most_common())]
    dir_shape = Counter((r.get("fable_axis"), r.get("grader_axis")) for r in dis)
    if dir_shape:
        lines += ["", "Disagreement direction:"]
        for (fa, ga), n in dir_shape.most_common():
            lines.append(f"- Fable {fa} vs Opus {ga}: {n}")
    if errs:
        lines += ["", "Error rows (retryable): "
                  + ", ".join(f"{r['claim_id']} ({b})" for b, r in errs)]

    # ---- full disagreement list ----
    lines += ["", "## All disagreements (deduped)", "",
              "| run | id | pipeline | Fable | Opus action | Opus missing_subclaim |",
              "|---|---|---|---|---|---|"]
    for b, r in sorted(chosen.values(), key=lambda x: (x[1].get("run") or "", x[1]["claim_id"])):
        if r.get("axis_agreement") == "DISAGREE":
            ms = (r.get("grader_missing_subclaim") or "").replace("|", "/")[:110]
            lines.append(f"| {r.get('run')} | {r['claim_id']} | {r.get('pipeline_verdict')} "
                         f"| {r.get('fable_verdict')} | {r.get('grader_action')} | {ms} |")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"agreement_full.md: {len(ok)} graded, {len(agree)} agree, "
          f"{len(dis)} disagree, {len(na)} n/a, {len(errs)} errors")


if __name__ == "__main__":
    main()
