#!/usr/bin/env python3
"""Deep second opinion (TESTING aid, 2026-07-10 owner request): a STRONGER
model (default claude-code/sonnet, $0 API — runs on the Claude subscription)
re-reads every judged claim WITH source context — the tool's evidence
sentence(s) plus their surrounding window plus the lexically-closest chunk of
each cited source — and returns an independent verdict AND commentary. The
commentary (with the verbatim quote it rests on) is rendered on every claim
card in the viewer, so a human can review cards fast instead of re-deriving
each judgment.

NEVER a veto: verdicts in analysis.json are untouched. Results go to
<run>/deep_check.json and the viewer is regenerated with the commentary
injected in-memory. Differs from --second-opinion (same-evidence re-read,
chip on disagreement only): this pass sees SOURCE CONTEXT, so it can also
catch retrieval misses and out-of-context quotes, and it always comments.

Usage:
  venv/bin/python3 deep_check.py <run_dir> [--model claude-code/sonnet]
      [--workers 4] [--limit N] [--no-viewer]
"""
import argparse
import json
import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.papertrail import matcher, viewer
from modules.papertrail.llm_client import LLMClient, extract_json, parallel_map

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

WINDOW_RADIUS = 4          # sentences either side of the tool's evidence pick
MAX_SOURCES_PER_CLAIM = 4  # prompt-size guard on heavily multi-cited claims

PROMPT = """You are auditing the output of a citation-verification tool. Give an INDEPENDENT judgment — the tool's verdict may be wrong in either direction.

CLAIM (from the author's text):
{CLAIM}

TOOL VERDICT: {VERDICT}
TOOL REASON: {REASON}

CITED SOURCE MATERIAL (the tool's chosen evidence sentence, its surrounding context, and the most lexically relevant chunk of each cited source):
{SOURCES}

Question: do the cited sources actually support the claim?
- Judge SUBSTANCE: numbers, direction of effect, who/what is attributed, scope and hedging.
- If the context contains a sentence that supports the claim BETTER than the tool's evidence pick, quote it verbatim.
- Anchor your judgment in the text: quote the exact sentence(s) it rests on.

Return ONLY JSON:
{{"supported": true or false,
 "confidence": "high" or "medium" or "low",
 "commentary": "2-4 sentences: what the sources actually say vs what the claim says; name any mismatch (number, scope, attribution, missing part) explicitly",
 "quote": "verbatim source sentence(s) your judgment rests on",
 "better_sentence": "verbatim better evidence sentence from the context, or null"}}"""


def _load_sources(run_dir: str):
    """paper_id -> {title, sentences:[{text,page}]} from the run's cache."""
    out = {}
    sc_dir = os.path.join(run_dir, "source_claims")
    if not os.path.isdir(sc_dir):
        return out
    for fn in os.listdir(sc_dir):
        if fn.endswith(".json"):
            try:
                d = json.load(open(os.path.join(sc_dir, fn), encoding="utf-8"))
                out[d.get("paper_id")] = d
            except Exception:
                continue
    return out


def _window_around(sents, sentence, radius=WINDOW_RADIUS):
    texts = [s.get("text", "") for s in sents]
    norm = matcher._norm(sentence or "")
    idx = -1
    for j, t in enumerate(texts):
        tn = matcher._norm(t)
        if tn and (tn == norm or (len(norm) > 40 and (norm in tn or tn in norm))):
            idx = j
            break
    if idx < 0:
        return ""
    lo, hi = max(0, idx - radius), min(len(texts), idx + radius + 1)
    return " ".join(texts[lo:hi])


def _top_lex_chunk(claim, sents):
    """The single most lexically relevant ~1200-word chunk (rare-token overlap —
    the signal embeddings miss; pure local math)."""
    chunks = matcher._chunk_sents(sents)
    if not chunks:
        return ""
    lex = matcher._lex_scores(claim, [s.get("text", "") for s in sents])
    best = max(range(len(chunks)), key=lambda i: max(lex[j] for j in chunks[i][1]))
    return chunks[best][0]


def build_prompt(tc, sources):
    parts = []
    evs = tc.get("evidences") or ([tc["evidence"]] if tc.get("evidence") else [])
    for e in evs[:MAX_SOURCES_PER_CLAIM]:
        pid = e.get("paper_id")
        src = sources.get(pid) or {}
        sents = src.get("sentences", []) or []
        title = e.get("source_title") or src.get("title") or pid or "?"
        sent = e.get("sentence") or ""
        block = [f"From \"{title}\":"]
        block.append(f"  tool evidence sentence ({'judged supporting' if e.get('supported') else 'judged NOT supporting'}): "
                     + (f"\"{sent}\"" if sent else "(none found)"))
        win = _window_around(sents, sent) if sent else ""
        if win:
            block.append(f"  context around it: \"{win}\"")
        chunk = _top_lex_chunk(tc.get("text", ""), sents) if sents else ""
        if chunk and matcher._norm(chunk) not in matcher._norm(win or " "):
            block.append(f"  most relevant chunk of this source: \"{chunk}\"")
        parts.append("\n".join(block))
    return PROMPT.format(CLAIM=tc.get("text", ""),
                         VERDICT=tc.get("verdict", "?"),
                         REASON=tc.get("reason") or "(none)",
                         SOURCES="\n\n".join(parts) or "(no source material found)")


def check_claim(tc, sources, llm):
    raw = llm.call(build_prompt(tc, sources), temperature=0.0, max_output_tokens=2048)
    obj = extract_json(raw) or {}
    if not isinstance(obj, dict) or "supported" not in obj:
        return {"error": "unparseable reply", "raw": (raw or "")[:400]}
    sup = bool(obj.get("supported"))
    return {
        "model": llm.model,
        "supported": sup,
        "agrees": sup == (tc.get("verdict") == "supported"),
        "confidence": str(obj.get("confidence") or "?"),
        "commentary": str(obj.get("commentary") or "").strip(),
        "quote": (str(obj.get("quote")) if obj.get("quote") else "").strip(),
        "better_sentence": (str(obj.get("better_sentence")).strip()
                            if obj.get("better_sentence")
                            and str(obj.get("better_sentence")).lower() != "null" else None),
    }


def regenerate_viewer(run_dir, analysis, dc_map):
    for tc in analysis.get("text_claims", []):
        if tc.get("id") in dc_map and "error" not in dc_map[tc["id"]]:
            tc["deep_check"] = dc_map[tc["id"]]     # in-memory only
    source_texts = {}
    for s in analysis.get("sources", []):
        fn = s.get("filename")
        if fn and not fn.lower().endswith(".pdf"):
            p = os.path.join(run_dir, "sources", fn)
            if os.path.exists(p):
                source_texts[s["paper_id"]] = open(p, encoding="utf-8", errors="ignore").read()
    text_file = analysis.get("metadata", {}).get("text_file", "")
    out = os.path.join(run_dir, "viewer.html")
    viewer.generate(analysis, out,
                    title=f"Verification — {os.path.basename(text_file) or 'run'}",
                    source_texts=source_texts)
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Testing aid: a stronger model re-reads every judged claim of a "
                    "finished run WITH source context and writes an independent verdict "
                    "+ commentary onto each claim card in the viewer. Never changes the "
                    "run's verdicts (analysis.json untouched). See docs/DEEP_CHECK.md.")
    ap.add_argument("run_dir",
                    help="a finished run's output dir (must contain analysis.json)")
    ap.add_argument("--model", default="claude-code/sonnet",
                    help="model that re-reads the claims (default: %(default)s — $0 "
                         "through a logged-in Claude Code install)")
    ap.add_argument("--workers", type=int, default=4,
                    help="parallel model calls (default: %(default)s)")
    ap.add_argument("--limit", type=int, default=0, help="only the first N judged claims")
    ap.add_argument("--no-viewer", action="store_true",
                    help="write deep_check.json but skip regenerating viewer.html")
    a = ap.parse_args()

    analysis = json.load(open(os.path.join(a.run_dir, "analysis.json"), encoding="utf-8"))
    sources = _load_sources(a.run_dir)
    judged = [tc for tc in analysis.get("text_claims", [])
              if tc.get("verdict") in ("supported", "unsupported")]
    if a.limit:
        judged = judged[:a.limit]
    logger.info(f"deep check: {len(judged)} judged claims, model {a.model}")

    llm = LLMClient(model=a.model)
    results = parallel_map(lambda tc: (tc["id"], check_claim(tc, sources, llm)),
                           judged, a.workers)
    dc_map = dict(results)
    ok = {k: v for k, v in dc_map.items() if "error" not in v}
    disagree = [k for k, v in ok.items() if not v["agrees"]]
    errs = [k for k, v in dc_map.items() if "error" in v]

    out_path = os.path.join(a.run_dir, "deep_check.json")
    json.dump({"model": a.model, "results": dc_map},
              open(out_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    logger.info(f"wrote {out_path}: {len(ok)} checked, {len(disagree)} disagree "
                f"{disagree}, {len(errs)} errors {errs}")

    if not a.no_viewer:
        v = regenerate_viewer(a.run_dir, analysis, dc_map)
        logger.info(f"viewer refreshed: {v}")


if __name__ == "__main__":
    main()
