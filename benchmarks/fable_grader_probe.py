#!/usr/bin/env python3
"""Fable-as-grader calibration probe (2026-07-10, owner request).

Runs claude-code/fable ($0) as an OWNER-STANDARD grader on the owner-marked
paper1 claims: given the claim, the SHOWN supporting sentences, and source
context, produce the actionable outcome the owner described:
  supported (with the proving sentences) /
  unsupported -> cite this subclaim or rewrite it this way.

CHECKPOINTED: results are written atomically to
  data/paper1_verification/fable_grader.json
after EVERY claim; re-running skips finished claims, so it resumes cleanly
after any cancellation/session loss.

Usage:  venv/bin/python3 benchmarks/fable_grader_probe.py [--model claude-code/fable]
"""
import json, os, sys, argparse, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import deep_check as dc
from modules.papertrail.llm_client import LLMClient, extract_json

RUN = os.path.join(ROOT, "data/paper1_verification")
OUT = os.path.join(RUN, "fable_grader.json")  # overridden per --model in main()
# divergence rows first (owner-vs-Opus), control t13, then the rest of the marks
ORDER = ["t6", "t8", "t18", "t22", "t24", "t23", "t13",
         "t9", "t10", "t14", "t17", "t25",
         # negative controls (hand-confirmed supported in run-8 audit, not owner-flagged):
         "t43", "t47", "t59", "t65"]

PROMPT = """You are the final human-standard reviewer for a citation-checking tool.
The tool's job: for a claim in an author's document, show supporting sentences
from the cited source(s) that PROVE the source supports the claim as written.

The standard (the owner's, strict): a claim is properly supported only when the
SHOWN sentences cover EVERY component of the claim. "The paper probably supports
it elsewhere" is not enough — but you are also given wider source context, so if
better sentences EXIST there, you must say so and quote them.

Decide ONE of three outcomes:
1. "supported" — the shown sentences (possibly plus better ones you found in the
   context and quote verbatim) prove every component.
2. "add_citation_or_rewrite" — the claim contains material this source does not
   prove (an unsourced aside, a component from elsewhere, or a contradicted
   part): the AUTHOR must add a citation for that subclaim or rewrite the claim
   to only what the source supports. Name the subclaim precisely.
3. "wrong_or_insufficient_evidence" — the source likely proves the claim but
   the tool showed the wrong/too-few sentences: the TOOL must fetch better
   evidence. Quote the better sentences from the context if you can see them.

Return STRICT JSON only:
{
  "verdict": "supported" | "unsupported",
  "action": "supported" | "add_citation_or_rewrite" | "wrong_or_insufficient_evidence",
  "components": [{"part": "<short>", "proven_by_shown": true|false,
                  "provable_from_context": true|false}],
  "missing_subclaim": "<the exact part needing a new citation/rewrite, or empty>",
  "rewrite_suggestion": "<one-sentence rewrite that fits the cited source, or empty>",
  "proof_sentences": ["<verbatim source sentences that would prove the claim>"],
  "why": "<2-3 sentences>"
}

CLAIM (from the author's document):
{CLAIM}

SUPPORTING SENTENCES THE TOOL SHOWS:
{SHOWN}

WIDER SOURCE CONTEXT (per cited source; use to find better sentences):
{CONTEXT}
"""


def shown_block(c):
    lines = []
    for e in (c.get("evidences") or []):
        s = (e.get("sentence") or "").strip()
        if s:
            lines.append(f"- [{e.get('source_title','?')}] ({'judged supporting' if e.get('supported') else 'judged NOT supporting'}) \"{s}\"")
    return "\n".join(lines) or "(none shown)"


def context_block(c, sources):
    parts, seen = [], set()
    for e in (c.get("evidences") or []):
        pid = e.get("paper_id")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        src = sources.get(pid) or {}
        sents = src.get("sentences", []) or []
        title = e.get("source_title") or src.get("title") or pid
        if not sents:
            parts.append(f'From "{title}": (source text unavailable)')
            continue
        win = dc._window_around(sents, e.get("sentence") or "") if e.get("sentence") else ""
        chunk = dc._top_lex_chunk(c.get("text", ""), sents)
        block = [f'From "{title}":']
        if win:
            block.append(f'  around the shown sentence: "{win}"')
        if chunk:
            block.append(f'  most relevant chunk: "{chunk}"')
        parts.append("\n".join(block))
    return "\n\n".join(parts) or "(no source context)"


def save(results):
    fd, tmp = tempfile.mkstemp(dir=RUN, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(results, f, indent=1)
    os.replace(tmp, OUT)


def main():
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-code/fable")
    args = ap.parse_args()
    tag = args.model.split("/")[-1]
    OUT = os.path.join(RUN, f"{tag}_grader.json")

    analysis = json.load(open(os.path.join(RUN, "analysis.json")))
    claims = {c["id"]: c for c in analysis["text_claims"]}
    sources = dc._load_sources(RUN)
    results = json.load(open(OUT)) if os.path.exists(OUT) else {}

    llm = LLMClient(model=args.model)
    for cid in ORDER:
        if cid in results and isinstance(results[cid], dict) and "error" not in results[cid]:
            print(f"{cid}: already done, skipping")
            continue
        c = claims.get(cid)
        if not c:
            results[cid] = {"error": "claim not found"}
            save(results)
            continue
        prompt = (PROMPT.replace("{CLAIM}", c.get("text", ""))
                        .replace("{SHOWN}", shown_block(c))
                        .replace("{CONTEXT}", context_block(c, sources)))
        try:
            raw = llm.call(prompt, temperature=0.0, max_output_tokens=3000)
            obj = extract_json(raw) or {"error": "unparseable", "raw": (raw or "")[:400]}
        except Exception as e:
            obj = {"error": str(e)[:200]}
        results[cid] = obj
        save(results)
        v = obj.get("verdict", "ERR")
        act = obj.get("action", obj.get("error", "?"))
        print(f"{cid}: {v} / {act}", flush=True)
    print(f"\nDone: {len([r for r in results.values() if 'error' not in r])}/{len(ORDER)} ok -> {OUT}")


if __name__ == "__main__":
    main()
