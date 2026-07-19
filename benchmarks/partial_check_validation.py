#!/usr/bin/env python3
"""Validate the partial-check accuracy fix (ROADMAP 7-i) on the 7 hand-re-audited
paper1 flags — WITHOUT a full run. Replays matcher._partial_flags (round-1 hybrid
retrieval + round-2 NEI escalation + ALCE over-cite) on the flagged claims using
the cached analysis, cached decomposed sources, and cached claim embeddings.

Expected (validated live 2026-07-05, 7 passes):
- CLEAR: t8 t36 t65 t74 (the re-audit's false alarms — support sits in the
  source's title/abstract/thesis, now reachable), t69 (the re-audit's one
  real gap, but its wrong-document sevilla2024.txt has since been replaced
  with the real Epoch paper AND the probe verified the Acemoglu component is
  genuinely in acemoglu2024), and t28 (its blocker was letter-spaced PyPDF2
  garble in anthropic2024 — the pdftotext fallback + sentence re-index, done
  here without LLM calls, exposes "each successive model generation is more
  persuasive than the previous" almost verbatim).
- STAY: t44 — its current flag names "an early lead may compound until one
  power pulls clear", which is in NEITHER cited source; the original hand
  audit called that phrase authorial and marked t44 "needs owner review", so
  the nudge pointing there is correct behavior.
- Over-cite nudges observed on this paper (not asserted by this script):
  t36 drago2025, t74 erdil2023, t28 hackenburg2025. The t28 one is
  rubric-consistent but humanly debatable — the judge reads the claim's
  "even if gains ... appear to be levelling off" as the writer's concessive
  framing (rule 0), so the source backing that half looks unneeded. Mildest
  nudge; dismissible in the viewer.

Cost: --dry-run = zero API (prints the assembled evidence per round).
Live = ~7 claims x (1-2 vote-sets + <=3 single probes) of small flash-lite
calls — a few cents, the budget the plan allocates for this validation.

Run from the repo root:
  venv/bin/python3 benchmarks/partial_check_validation.py --dry-run
  venv/bin/python3 benchmarks/partial_check_validation.py
"""
import os
import sys
import json
import glob
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import matcher, embeddings  # noqa: E402

RUN_DIR = "data/paper1_verification"
EXPECT_CLEAR = ["t8", "t28", "t36", "t65", "t69", "t74"]
EXPECT_STAY = ["t44"]        # authorial component, genuinely in no cited source
KNOWN_OPEN = []              # t28 cleared 2026-07-05: its blocker was letter-spaced
                             # PyPDF2 garble in anthropic2024; the pdftotext fallback
                             # + schema-5 sentence re-index (applied below, no LLM)
                             # put the real memo text in front of the judge.


def load_sources(run_dir: str, analysis=None):
    """paper_id -> cached decomposed source (title, sentences, claims).

    Caches written before CACHE_SCHEMA 5 may hold letter-spaced-garble sentence
    indexes; re-split them from the source file (the tool's own no-LLM upgrade
    path) so this replay judges what a post-upgrade run would judge."""
    from modules.papertrail import source_decomposer as sd
    files = {}
    for s in (analysis or {}).get("sources", []):
        if s.get("filename"):
            files[s["paper_id"]] = s["filename"]
    src_dir = ((analysis or {}).get("metadata", {}) or {}).get("sources_dir")
    out = {}
    for f in glob.glob(os.path.join(run_dir, "source_claims", "*.json")):
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
        if d.get("schema", 0) < sd.CACHE_SCHEMA and src_dir and d["paper_id"] in files:
            path = os.path.join(src_dir, files[d["paper_id"]])
            if os.path.exists(path):
                d["sentences"] = sd._sentence_index(path)
        out[d["paper_id"]] = d
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=RUN_DIR)
    ap.add_argument("--analysis", default=None,
                    help="analysis json holding the flags (default <run-dir>/analysis.json)")
    ap.add_argument("--ids", default=None,
                    help="comma-separated claim ids (default: every flagged claim)")
    ap.add_argument("--dry-run", action="store_true",
                    help="no API: print the evidence each round would judge")
    ap.add_argument("--model", default=None)
    ap.add_argument("--api-key", default=None)
    args = ap.parse_args()

    with open(args.analysis or os.path.join(args.run_dir, "analysis.json"),
              encoding="utf-8") as f:
        analysis = json.load(f)
    sources = load_sources(args.run_dir, analysis)
    claims = [c for c in analysis["text_claims"] if c.get("partial_support")]
    if args.ids:
        want = set(args.ids.split(","))
        claims = [c for c in analysis["text_claims"] if c["id"] in want]
    if not claims:
        sys.exit("no flagged claims found — run with --partial-check first, or pass --ids")

    prompt = matcher._load_prompt("pt_combined_judgment_prompt.txt")
    extract_prompt = matcher._load_prompt("pt_extract_evidence_prompt.txt")
    emb_dir = os.path.join(args.run_dir, "embeddings")

    def _row(pid, claim_text, texts, suffix):
        cache = os.path.join(emb_dir, f"{pid}.{suffix}.npz")
        if not (texts and os.path.exists(cache)):
            return None
        rel = embeddings.cosine_matrix([claim_text], texts, b_cache_file=cache)
        return rel[0] if rel else None

    def claims_row_for(pid, claim_text):
        src = sources.get(pid) or {}
        return _row(pid, claim_text,
                    [(sc.get("text") or "") for sc in (src.get("claims") or [])], "claims")

    def sents_row_for(pid, claim_text):
        src = sources.get(pid) or {}
        return _row(pid, claim_text,
                    [s.get("text", "") for s in (src.get("sentences") or [])], "sents")

    if args.dry_run:
        for c in claims:
            tr = c.get("tail_rescue") or {}
            text = tr["tail"] if tr.get("supported") else c["text"]
            print(f"\n=== {c['id']}  (old flag: {c.get('partial_support', {}).get('reason', '-')})")
            for pid in c.get("paper_ids", []):
                src = sources.get(pid)
                if src is None:
                    print(f"  [{pid}] MISSING from source cache")
                    continue
                lead = matcher._lead_text(src)
                esc = matcher._escalated_context(text, src, claims_row_for(pid, text),
                                                 sents_row_for(pid, text))
                print(f"  [{pid}] {src.get('title')}")
                print(f"    round1 lead: {lead[:220]}")
                print(f"    round2 ctx:  {esc[:400]}")
        print("\n(dry run — no judgments made)")
        return

    from modules.papertrail.llm_client import LLMClient
    llm = LLMClient(model=args.model, api_key=args.api_key)
    results = {}
    for c in claims:
        tr = c.get("tail_rescue") or {}
        text = tr["tail"] if tr.get("supported") else c["text"]
        def esc_context(pid, t):
            src = sources.get(pid) or {}
            return matcher._escalated_context(t, src, claims_row_for(pid, t),
                                              sents_row_for(pid, t))

        def extract_check(pid, t):
            src = sources.get(pid)
            if src is None:
                return False
            e = matcher._extract_evidence(t, pid, src, llm, extract_prompt, prompt,
                                          row=sents_row_for(pid, t))
            return bool(e and e.get("supported"))

        flags = matcher._partial_flags(
            text, c.get("paper_ids", []), sources, c.get("evidences") or [],
            llm, prompt, esc_context=esc_context, extract_check=extract_check)
        ps = flags.get("partial_support")
        results[c["id"]] = flags
        state = (f"STILL FLAGGED ({'escalated' if ps.get('escalated') else 'round 1'}, "
                 f"{ps.get('votes')}): {ps.get('reason')}" if ps else "CLEARED")
        oc = flags.get("over_citation")
        if oc:
            state += " | over-cited: " + ", ".join(
                s.get("source_title") or s["paper_id"] for s in oc["sources"])
        print(f"{c['id']}: {state}")

    wrong = ([i for i in EXPECT_CLEAR if i in results and results[i].get("partial_support")]
             + [i for i in EXPECT_STAY if i in results and not results[i].get("partial_support")])
    checked = [i for i in EXPECT_CLEAR + EXPECT_STAY if i in results]
    if checked:
        open_state = [f"{i}={'flagged' if results[i].get('partial_support') else 'CLEARED (fixed?)'}"
                      for i in KNOWN_OPEN if i in results]
        print(f"\nvs validated expectations ({len(checked)} checked): "
              + ("ALL MATCH" if not wrong else f"MISMATCH on {', '.join(wrong)}")
              + (f" | known-open: {', '.join(open_state)}" if open_state else ""))
        sys.exit(1 if wrong else 0)


if __name__ == "__main__":
    main()
