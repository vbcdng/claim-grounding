#!/usr/bin/env python3
"""WiCE benchmark harness — convert filtered WiCE claims into verifier projects
and score a finished run against WiCE's labels + supporting-sentence sets.

WiCE (Kamoi et al., EMNLP 2023): claims from Wikipedia verified against the web
article they cite; labels supported / partially_supported / not_supported plus
the minimal sets of evidence sentences that prove each claim. That maps 1:1 to
our layer stack: verdict, partial_support/covering ambers, and covering picks.

Usage:
  # build a 26-claim batch project from the dev split (deterministic, seeded)
  python3 benchmarks/wice_bench.py convert --split dev --batch 1 \
      --output-dir data/wice/batch_dev_1

  # score a finished run against the batch's ground truth
  python3 benchmarks/wice_bench.py score \
      --analysis data/wice/batch_dev_1_run/analysis.json \
      --ground-truth data/wice/batch_dev_1/wice_ground_truth.json

No LLM calls, no network. The raw jsonl lives in data/wice/{train,dev,test}.jsonl
(gitignored; re-fetch: github.com/ryokamoi/wice, data/entailment_retrieval/claim/).

Safety filter: the loop's model columns must not hit refusal-prone domains
(owner rule: no health/bio/chem/IT-security). Items whose claim, title, or
source text match the blocklist are excluded before sampling.
"""

import argparse
import json
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WICE_DIR = os.path.join(ROOT, "data", "wice")

# Domain blocklist (owner rule): drop anything health/bio/chem/IT-security
# adjacent so unattended night columns never poke a refusal classifier.
# Deliberately broad — WiCE has ~2k items, we can afford false drops.
_BLOCK = re.compile(
    r"\b(cancer|tumou?r|disease|diagnos|medic|clinic|hospital|patient|drug|"
    r"pharma|vaccin|virus|viral|bacteri|infect|epidemi|pandemi|surg|therap|"
    r"psychiatr|symptom|syndrome|obesit|diabet|cardio|neuro|genom|gene\b|"
    r"genetic|dna|rna|protein|enzyme|organism|species|biolog|botan|ecolog|"
    r"physiolog|anatom|chemical|chemist|compound|toxi|poison|molecul|"
    r"nuclear|radioactiv|explos|weapon|firearm|ammunit|malware|hack|"
    r"cybersec|encrypt|exploit\b|vulnerabilit|suicid|overdose|abortion|"
    r"pregnan|autops|forensic)\w*",
    re.IGNORECASE,
)


# Non-English source pages, excluded from the benchmark (owner rule
# 2026-07-18: the tool targets English sources for now; WiCE occasionally
# cites Dutch/Hebrew/Russian pages). Applied in convert() for future batches
# and skipped in score(); the exporter drops them from the shipped ground
# truths. Identified by stopword/script probe over all 225 claim-source
# pairs; the excluded rows are quarantined, not deleted.
EXCLUDED_NON_ENGLISH = {"neptune", "starbucks", "mikhailrosheuvel", "hardwell"}


# --- language probe (heldout conversion, prereg 2026-07-19) ---------------
# Same approach as the 2026-07-18 exclusion: foreign stopword sets + a
# non-Latin script count over the source page. Mechanical, conversion-time
# only; every flagged row is logged, never silently dropped.
_EN_STOP = {
    "the", "of", "and", "to", "in", "is", "was", "that", "for", "on", "with",
    "as", "by", "at", "from", "it", "his", "her", "he", "she", "are", "were",
    "be", "this", "which", "or", "an", "not", "have", "has", "had", "their",
    "its", "but", "also", "they", "who", "been", "after", "when", "one",
    "all", "there", "would", "about", "into", "more", "other", "some",
}
_FOREIGN_STOP = {
    "nl": {"het", "een", "van", "ik", "je", "dat", "niet", "zijn", "voor",
           "met", "als", "maar", "ook", "naar", "dan", "nog", "wordt",
           "deze", "meer", "door", "onze", "wij", "hij", "zij", "hebben",
           "werd", "bij", "uit", "aan", "om", "te", "er", "dit", "zich",
           "hun", "nu", "al", "tot", "over", "geen", "onder", "tegen",
           "na", "toen", "hem", "ze", "wel", "kan", "heeft"},
    "de": {"der", "die", "das", "und", "ist", "nicht", "ein", "eine", "mit",
           "von", "für", "auf", "dem", "den", "des", "im", "sich", "auch",
           "als", "werden", "wurde", "bei", "aus", "nach", "über", "noch",
           "nur", "wir", "ihr", "sie", "es", "dass", "oder", "wie", "zum",
           "zur", "hat", "haben", "sind", "dieser", "diese", "einen",
           "einem", "einer", "durch", "wird", "kann", "beim", "sehr"},
    "fr": {"le", "la", "les", "des", "une", "est", "dans", "pour", "que",
           "qui", "avec", "sur", "pas", "par", "plus", "mais", "son", "ses",
           "aux", "cette", "ces", "être", "sont", "été", "était", "ont",
           "nous", "vous", "ils", "elles", "au", "du", "et", "se", "ne",
           "je", "leur", "comme", "tout", "fait"},
    "es": {"el", "los", "las", "una", "es", "en", "que", "por", "para",
           "con", "del", "se", "su", "sus", "como", "más", "pero", "fue",
           "son", "está", "han", "hay", "este", "esta", "estos", "estas",
           "también", "entre", "sobre", "ser", "lo", "ya", "sin", "muy",
           "cuando", "hasta", "desde"},
    "it": {"il", "la", "le", "gli", "uno", "una", "che", "per", "con",
           "del", "della", "dei", "delle", "sono", "è", "non", "più",
           "anche", "come", "nel", "nella", "dal", "alla", "questo",
           "questa", "stato", "hanno", "essere", "ma", "si", "ha", "tra",
           "dopo", "molto"},
    "pt": {"os", "um", "uma", "que", "não", "com", "para", "por", "mais",
           "como", "mas", "foi", "são", "está", "tem", "seu", "sua", "dos",
           "das", "pelo", "pela", "também", "entre", "sobre", "ser", "ele",
           "ela", "ao", "à", "já", "muito", "quando"},
}


def _probe_english(text):
    """Return (is_english, reason). Flags (a) pages with a substantial
    non-Latin-script share (>15% of letters — calibrated on the 7/18
    decisions: starbucks/dev00940 at 20% Cyrillic excluded, the
    mostly-English Myanmar court page at 13% Burmese kept) and (b) pages
    whose foreign-stopword density beats their English one."""
    letters = [ch for ch in text if ch.isalpha()]
    if letters:
        non_latin = sum(1 for ch in letters if ord(ch) > 0x024F)
        frac = non_latin / len(letters)
        if frac > 0.15:
            return False, f"non-Latin script ({non_latin} chars, {frac:.0%})"
    words = re.findall(r"[a-zA-ZÀ-ɏ]+", text.lower())
    if len(words) >= 30:
        en = sum(1 for w in words if w in _EN_STOP)
        for lang, sw in _FOREIGN_STOP.items():
            hits = sum(1 for w in words if w in sw and w not in _EN_STOP)
            if hits > en and hits >= max(10, 0.04 * len(words)):
                return False, (f"foreign stopwords ({lang}: {hits} vs "
                               f"en: {en} of {len(words)} words)")
    return True, None


def _used_wice_ids():
    """Union of wice_ids consumed by every prior run (prereg §1)."""
    import glob
    used = set()
    paths = glob.glob(os.path.join(HERE, "wice_runs", "*", "wice_ground_truth.json"))
    paths.append(os.path.join(ROOT, "data", "first_check", "wice_ground_truth.json"))
    for p in paths:
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            for v in json.load(f)["claims"].values():
                used.add(v.get("wice_id"))
    used.discard(None)
    return used


def _load(split):
    path = os.path.join(WICE_DIR, f"{split}.jsonl")
    if not os.path.exists(path):
        sys.exit(f"missing {path} — copy WiCE's data/entailment_retrieval/claim/{split}.jsonl there")
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def _safe(item):
    text = " ".join([
        item.get("claim", ""),
        item.get("meta", {}).get("claim_title", ""),
        " ".join(item.get("evidence", [])),
    ])
    return not _BLOCK.search(text)


def _slug(title, used):
    base = re.sub(r"[^a-z0-9]+", "", title.lower())[:24] or "src"
    slug, n = base, 2
    while slug in used:
        slug, n = f"{base}{n}", n + 1
    used.add(slug)
    return slug


def convert(split, batch, size, output_dir, seed=7):
    items = [(i, it) for i, it in enumerate(_load(split)) if _safe(it)]
    rng = random.Random(seed)
    rng.shuffle(items)

    # stratify: fill each batch with a fixed label mix so every run sees all
    # three outcomes (dev reality is ~55% partial)
    quota = {"supported": size * 4 // 10, "partially_supported": size * 4 // 10}
    quota["not_supported"] = size - sum(quota.values())
    picked, counts, skipped = [], {k: 0 for k in quota}, 0
    for _ in range(batch):  # earlier batches consume earlier items — no overlap
        picked, counts = [], {k: 0 for k in quota}
        while items and len(picked) < size:
            idx, it = items.pop(0)
            lab = it["label"]
            if counts[lab] >= quota[lab]:
                skipped += 1
                continue
            counts[lab] += 1
            picked.append((idx, it))
    if len(picked) < size:
        print(f"WARNING: only {len(picked)}/{size} items available for batch {batch}")

    os.makedirs(os.path.join(output_dir, "sources"), exist_ok=True)
    used, refs, paras, gt = set(), [], [], {}
    for idx, it in picked:
        meta = it.get("meta", {})
        key = _slug(meta.get("claim_title", "src"), used)
        if key in EXCLUDED_NON_ENGLISH:
            continue
        with open(os.path.join(output_dir, "sources", f"{key}.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(it["evidence"]))
        refs.append(f"# {meta.get('claim_title', key)} (WiCE {split} #{idx})\n{key} = {key}.txt")
        paras.append(f"{it['claim']} [[{key}]]")
        gt[key] = {
            "wice_id": meta.get("id"),
            "split_index": idx,
            "label": it["label"],
            "supporting_sentences": it.get("supporting_sentences", []),
            "n_source_sentences": len(it["evidence"]),
        }

    with open(os.path.join(output_dir, "my_text.md"), "w", encoding="utf-8") as f:
        f.write(f"# WiCE {split} batch {batch}\n\n" + "\n\n".join(paras) + "\n")
    with open(os.path.join(output_dir, "my_text.md.refs.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(refs) + "\n")
    with open(os.path.join(output_dir, "wice_ground_truth.json"), "w", encoding="utf-8") as f:
        json.dump({"split": split, "batch": batch, "seed": seed, "claims": gt}, f, indent=1)
    print(f"wrote {len(picked)} claims -> {output_dir} "
          f"(labels: {counts}; unsafe filtered out of pool: see score header)")
    print(f"run: venv/bin/python3 verify_my_text.py --text {output_dir}/my_text.md "
          f"--sources {output_dir}/sources --references {output_dir}/my_text.md.refs.txt "
          f"--output-dir {output_dir}_run --yes")


def convert_all(split, batch_size, output_root, label=None, exclude_used=False):
    """Prereg 2026-07-19 heldout mode: emit ALL rows of a split (file order,
    no sampling, no stratification, no domain blocklist — the prereg permits
    exactly two exclusion rules) in fixed-size batches. Optional label filter
    and used-id exclusion define the refuted stress set; those are set
    definition, not exclusions. Every dropped row (non-English source or
    unconvertible) is logged to <parent>/exclusions.json."""
    pool = list(enumerate(_load(split)))
    if label:
        pool = [(i, it) for i, it in pool if it["label"] == label]
    n_label = len(pool)
    if exclude_used:
        used_ids = _used_wice_ids()
        pool = [(i, it) for i, it in pool
                if it.get("meta", {}).get("id") not in used_ids]
        print(f"used-id exclusion: {n_label - len(pool)} of {n_label} "
              f"already-consumed rows removed ({len(used_ids)} used ids)")
    n_pool = len(pool)

    emitted, excluded = [], []
    for idx, it in pool:
        meta = it.get("meta", {})
        claim = (it.get("claim") or "").strip()
        src = "\n".join(it.get("evidence") or [])
        if not claim or not src.strip():
            excluded.append({"wice_id": meta.get("id"), "split": split,
                             "split_index": idx, "slug": None,
                             "reason": "unconvertible (missing/empty claim or source)"})
            continue
        ok, why = _probe_english(src)
        if not ok:
            excluded.append({"wice_id": meta.get("id"), "split": split,
                             "split_index": idx, "slug": None,
                             "reason": f"non-English source page: {why}"})
            continue
        emitted.append((idx, it))

    parent = os.path.dirname(output_root) or "."
    os.makedirs(parent, exist_ok=True)
    root_name = os.path.basename(output_root)
    n_batches = 0
    for b0 in range(0, len(emitted), batch_size):
        n_batches += 1
        chunk = emitted[b0:b0 + batch_size]
        out = f"{output_root}_b{n_batches:02d}"
        os.makedirs(os.path.join(out, "sources"), exist_ok=True)
        # pre-seed with the legacy slug-skip set so score() never drops a row
        used_slugs = set(EXCLUDED_NON_ENGLISH)
        refs, paras, gt = [], [], {}
        for idx, it in chunk:
            meta = it.get("meta", {})
            key = _slug(meta.get("claim_title", "src"), used_slugs)
            with open(os.path.join(out, "sources", f"{key}.txt"), "w",
                      encoding="utf-8") as f:
                f.write("\n".join(it["evidence"]))
            refs.append(f"# {meta.get('claim_title', key)} (WiCE {split} #{idx})\n"
                        f"{key} = {key}.txt")
            paras.append(f"{it['claim']} [[{key}]]")
            gt[key] = {
                "wice_id": meta.get("id"),
                "split_index": idx,
                "label": it["label"],
                "supporting_sentences": it.get("supporting_sentences", []),
                "n_source_sentences": len(it["evidence"]),
            }
        with open(os.path.join(out, "my_text.md"), "w", encoding="utf-8") as f:
            f.write(f"# WiCE {split} heldout batch {n_batches} ({root_name})\n\n"
                    + "\n\n".join(paras) + "\n")
        with open(os.path.join(out, "my_text.md.refs.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(refs) + "\n")
        with open(os.path.join(out, "wice_ground_truth.json"), "w", encoding="utf-8") as f:
            json.dump({"split": split, "mode": "heldout_all", "batch": n_batches,
                       "label_filter": label, "exclude_used": exclude_used,
                       "claims": gt}, f, indent=1)
        print(f"  {out}: {len(chunk)} claims")

    excl_path = os.path.join(parent, "exclusions.json")
    prior = []
    if os.path.exists(excl_path):
        prior = json.load(open(excl_path, encoding="utf-8"))
        prior = [e for e in prior if e.get("split") != split or e.get("label_filter") != label]
    for e in excluded:
        e["label_filter"] = label
    with open(excl_path, "w", encoding="utf-8") as f:
        json.dump(prior + excluded, f, indent=1)

    print(f"\n{root_name}: pool {n_pool} rows -> emitted {len(emitted)} in "
          f"{n_batches} batches + excluded {len(excluded)} "
          f"(sanity: {len(emitted)} + {len(excluded)} = "
          f"{len(emitted) + len(excluded)}, pool = {n_pool})")
    for e in excluded:
        print(f"  EXCLUDED {e['wice_id']}: {e['reason']}")


def _tool_bucket(claim):
    """Map a tool claim record to WiCE's 3-way scheme."""
    if claim.get("verdict") != "supported":
        return "not_supported"
    cov = claim.get("covering") or {}
    if claim.get("proof_state") == "partial" or cov.get("uncovered") or claim.get("partial_support"):
        return "partially_supported"
    return "supported"


def _adjudicated_bucket(claim):
    """3-way bucket with the arbiter's finding folded in BEFORE scoring
    (scoring-time only — the verdict field is never touched; house rule).

    WiCE labels source ENTAILMENT, not display quality, so a verified
    tool-side miss (arbiter action wrong_or_insufficient_evidence WITH
    gate-verified proof quotes) counts as supported here even though the
    card rightly stays flagged in the viewer.

    Returns (bucket, reason) — reason is None when the arbiter changed
    nothing (not escalated, unparseable, or same bucket)."""
    base = _tool_bucket(claim)
    arb = claim.get("arbiter") or {}
    action = arb.get("action")
    if not action:
        return base, None
    proofs = bool(arb.get("proofs"))
    if claim.get("verdict") == "unsupported":
        if action == "supported":
            return "supported", "arbiter: shown evidence proves it (false unsupported)"
        if action == "wrong_or_insufficient_evidence" and proofs:
            return "supported", "arbiter: proof exists in source (verified quotes)"
        if action == "add_citation_or_rewrite":
            if proofs:
                return "partially_supported", "arbiter: mixed — parts provable, a component is not"
            return base, None  # arbiter concurs with not_supported
        return base, None  # tool-fetch with every quote dropped: low confidence, no flip
    # supported verdict (escalated via gaps/partial_support/conflict-candidate)
    if action == "supported":
        return ("supported",
                "arbiter: gaps look minor — fully proven" if base != "supported" else None)
    if action == "wrong_or_insufficient_evidence" and proofs:
        return ("supported",
                "arbiter: all components provable (verified quotes)" if base != "supported" else None)
    if action == "add_citation_or_rewrite":
        return ("partially_supported",
                "arbiter: a component is not provable from the source" if base != "partially_supported" else None)
    return base, None


def score(analysis_path, gt_path):
    analysis = json.load(open(analysis_path, encoding="utf-8"))
    gt = json.load(open(gt_path, encoding="utf-8"))["claims"]
    dropped = sorted(set(gt) & EXCLUDED_NON_ENGLISH)
    if dropped:
        gt = {k: v for k, v in gt.items() if k not in EXCLUDED_NON_ENGLISH}
        print(f"note: skipping non-English-source rows: {', '.join(dropped)}")
    claims = analysis.get("text_claims", [])

    by_key = {}
    for c in claims:
        for key in c.get("markers") or []:
            by_key.setdefault(key, c)

    rows, agree, agree_adj = [], 0, 0
    conf, conf_adj = {}, {}  # (wice, tool) -> count
    flips, has_arbiter = [], False
    for key, g in gt.items():
        c = by_key.get(key)
        if c is None:
            rows.append((key, g["label"], "MISSING", "MISSING", ""))
            continue
        has_arbiter = has_arbiter or bool(c.get("arbiter"))
        tool = _tool_bucket(c)
        adj, why = _adjudicated_bucket(c)
        conf[(g["label"], tool)] = conf.get((g["label"], tool), 0) + 1
        conf_adj[(g["label"], adj)] = conf_adj.get((g["label"], adj), 0) + 1
        agree += tool == g["label"]
        agree_adj += adj == g["label"]
        if adj != tool:
            flips.append((key, g["label"], tool, adj, why or "", c))

        # sentence-level: do covering picks land inside any WiCE minimal set?
        note = ""
        cov = c.get("covering") or {}
        shown = {r.get("sentence", "").strip() for r in cov.get("covered", [])}
        if shown and g["supporting_sentences"]:
            src_path = os.path.join(os.path.dirname(gt_path), "sources", f"{key}.txt")
            if os.path.exists(src_path):
                src = open(src_path, encoding="utf-8").read().split("\n")
                gold = {s for group in g["supporting_sentences"] for s in group}
                hit = sum(1 for s in shown if any(src[i].strip() == s for i in gold if i < len(src)))
                note = f"picks_in_gold={hit}/{len(shown)}"
            else:
                # sources are not redistributed with the repo (copyright) —
                # verdict scoring above is unaffected, only this note is skipped
                note = "picks_in_gold=n/a (sources not present)"
        rows.append((key, g["label"], tool, adj, note))

    n = len(gt)
    print(f"verdict-level agreement: {agree}/{n} ({100 * agree // max(n, 1)}%)")
    print("confusion (wice_label -> tool):")
    for (w, t), k in sorted(conf.items()):
        marker = "" if w == t else "   <-- disagreement"
        print(f"  {w:22s} -> {t:22s} {k:3d}{marker}")

    if has_arbiter:
        print(f"\narbiter-adjudicated agreement: {agree_adj}/{n} "
              f"({100 * agree_adj // max(n, 1)}%)")
        print("confusion (wice_label -> adjudicated):")
        for (w, t), k in sorted(conf_adj.items()):
            marker = "" if w == t else "   <-- disagreement"
            print(f"  {w:22s} -> {t:22s} {k:3d}{marker}")
        print(f"adjudication flips: {len(flips)}")
        for key, wl, tl, adj, why, _ in flips:
            good = "+" if (adj == wl) else ("-" if tl == wl else "~")
            print(f"  {good} {key:26s} {tl} -> {adj:22s} wice={wl:22s} ({why})")

    # hard safety metric: a refuted row must never score supported
    fs_base = conf.get(("not_supported", "supported"), 0)
    fs_adj = conf_adj.get(("not_supported", "supported"), 0)
    n_refuted = sum(1 for g in gt.values() if g["label"] == "not_supported")
    print(f"\nfalse-supports on {n_refuted} refuted rows: base={fs_base}"
          + (f", adjudicated={fs_adj}" if has_arbiter else ""))
    if fs_base or fs_adj:
        print("*** FALSE-SUPPORT FAILURE: refuted row(s) scored supported — "
              "stop and report ***")

    print("\nper-claim:")
    for key, wl, tl, adj, note in rows:
        flag = "OK " if wl == tl else "DIFF" if tl != "MISSING" else "MISS"
        adjcol = f"adj={adj:22s} " if has_arbiter and adj != tl else ""
        print(f"  {flag} {key:26s} wice={wl:22s} tool={tl:22s} {adjcol}{note}")
    return 1 if fs_base or fs_adj else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("convert")
    c.add_argument("--split", default="dev", choices=["train", "dev", "test"])
    c.add_argument("--batch", type=int, default=1, help="1-based batch number (deterministic slices)")
    c.add_argument("--size", type=int, default=26)
    c.add_argument("--seed", type=int, default=7)
    c.add_argument("--output-dir", help="required unless --all")
    c.add_argument("--all", action="store_true",
                   help="heldout mode: emit ALL rows of the split, file order, batched")
    c.add_argument("--label", choices=["supported", "partially_supported", "not_supported"],
                   help="with --all: keep only rows with this label")
    c.add_argument("--exclude-used", action="store_true",
                   help="with --all: drop wice_ids consumed by any prior run")
    c.add_argument("--batch-size", type=int, default=26)
    c.add_argument("--output-root", help="with --all: batch dirs <root>_b01..")
    s = sub.add_parser("score")
    s.add_argument("--analysis", required=True)
    s.add_argument("--ground-truth", required=True)
    a = ap.parse_args()
    if a.cmd == "convert":
        if a.all:
            if not a.output_root:
                sys.exit("--all requires --output-root")
            convert_all(a.split, a.batch_size, a.output_root,
                        label=a.label, exclude_used=a.exclude_used)
        elif not a.output_dir:
            sys.exit("--output-dir required")
        else:
            convert(a.split, a.batch, a.size, a.output_dir, a.seed)
    else:
        sys.exit(score(a.analysis, a.ground_truth))


if __name__ == "__main__":
    main()
