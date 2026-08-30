#!/usr/bin/env python3
"""Run one blind reader over a Citation-Integrity blind-grading packet.

A *blind reader* is a strong model that reads one benchmark row — the citing
sentence plus the whole cited paper — and gives its own verdict without ever
seeing the benchmark's answer key or the tool's verdict. Two readers from
different model families disagreeing with the key is the strongest cheap signal
that the key itself is wrong; one reader disagreeing means little, because a
single reader over-flags fine points.

    python3 benchmarks/ci_blind_reader.py \
        --packet data/citation_integrity/c0_packet \
        --model claude-code/opus \
        --votes-dir data/citation_integrity/c0_packet/votes_opus_20260803 \
        --concurrency 4

Cost: with a `claude-code/*` model every call goes through the local `claude`
CLI on the author's subscription — $0 of API spend. Any other model spends real
money and needs the author's named go first.

Resumable by design: a row whose vote file already exists is skipped, so a run
killed halfway can simply be started again. One JSON object per row is written
to `<votes-dir>/<cid>.json`, which is the shape `ci_blind_compare.py` reads.

The prompt is the packet's task file with one change: the pointer at
`sources/<cid>.txt` is replaced by the paper's actual text, because a one-turn
headless call cannot open a file and answer in the same turn. The rubric and the
claim reach the model byte-identical to what the packet holds.
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.papertrail import claude_code_backend  # noqa: E402
from modules.papertrail.llm_client import LLMClient  # noqa: E402

DEFECTS = {
    "not_in_source", "unrelated_source", "contradicted", "overstated",
    "partially_supported", "content_present_but_secondhand",
}

# The two-line pointer written by ci_blind_packet.SOURCE_POINTER.
_POINTER = re.compile(r"Full text: `sources/([^`]+)`[^\n]*\nIt is a real[^\n]*\n")

_OUTPUT_RULE = ("\nAnswer with the JSON object and nothing else — no preamble, no "
                "code fence, no commentary after it.\n")


def build_prompt(task_md, source_text, cid):
    """Task file with the source pointer swapped for the paper's actual text."""
    inline = (f"The full text of the cited paper is below, between the markers.\n\n"
              f"<<<CITED PAPER {cid}>>>\n{source_text.strip()}\n<<<END CITED PAPER>>>\n")
    prompt, n = _POINTER.subn(inline, task_md)
    if n != 1:
        raise SystemExit(f"{cid}: could not find the source pointer in the task file "
                         f"(found {n} matches) — was the packet built by "
                         f"benchmarks/ci_blind_packet.py?")
    return prompt + _OUTPUT_RULE


def normalise(raw, cid, model):
    """Model output -> a vote dict, or (None, reason) if it is unusable."""
    if not isinstance(raw, dict):
        return None, f"expected a JSON object, got {type(raw).__name__}"
    vote = str(raw.get("vote", "")).strip().lower()
    if vote not in ("pass", "flag"):
        return None, f"vote={raw.get('vote')!r} is neither pass nor flag"
    defect = raw.get("defect")
    defect = None if defect in (None, "", "null") else str(defect).strip().lower()
    warnings = []
    if vote == "flag" and defect not in DEFECTS:
        warnings.append(f"defect {defect!r} is not one of the six allowed codes")
    if vote == "pass" and defect:
        warnings.append(f"pass vote carried defect {defect!r}; dropped")
        defect = None
    out = {
        "id": cid,
        "vote": vote,
        "defect": defect,
        "quote": str(raw.get("quote") or ""),
        "reason": str(raw.get("reason") or ""),
        "confidence": str(raw.get("confidence") or "").strip().lower() or "unstated",
        "_reader_model": model,
    }
    if warnings:
        out["_warnings"] = warnings
    return out, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--packet", required=True)
    ap.add_argument("--model", required=True,
                    help="claude-code/opus, claude-code/sonnet, ... ($0); any other "
                         "model spends real money")
    ap.add_argument("--votes-dir", required=True)
    ap.add_argument("--concurrency", type=int, default=4,
                    help="the CLI shares one subscription; 4-6 is the ceiling")
    ap.add_argument("--timeout", type=int, default=420,
                    help="seconds per call (whole papers take Opus a while)")
    ap.add_argument("--only", help="comma-separated row ids, for spot checks")
    ap.add_argument("--limit", type=int, help="stop after this many rows (smoke test)")
    ap.add_argument("--dry-run", action="store_true",
                    help="write the first prompt to <votes-dir>/_prompt_sample.txt "
                         "and make no calls")
    a = ap.parse_args()

    tasks_dir = os.path.join(a.packet, "tasks")
    ids = sorted(f[:-3] for f in os.listdir(tasks_dir) if f.endswith(".md"))
    if a.only:
        want = {s.strip() for s in a.only.split(",") if s.strip()}
        ids = [c for c in ids if c in want]
    os.makedirs(a.votes_dir, exist_ok=True)

    def prompt_for(cid):
        md = open(os.path.join(tasks_dir, f"{cid}.md")).read()
        src = open(os.path.join(a.packet, "sources", f"{cid}.txt")).read()
        return build_prompt(md, src, cid)

    if a.dry_run:
        sample = os.path.join(a.votes_dir, "_prompt_sample.txt")
        open(sample, "w").write(prompt_for(ids[0]))
        print(f"dry run — no calls made; wrote {sample} ({len(ids)} rows would run)")
        return 0

    todo = [c for c in ids if not os.path.exists(os.path.join(a.votes_dir, f"{c}.json"))]
    voted = len(ids) - len(todo)
    if a.limit:
        todo = todo[: a.limit]
    print(f"packet {a.packet}: {len(ids)} rows, {voted} already voted, "
          f"{len(todo)} to read with {a.model} at concurrency {a.concurrency}", flush=True)
    if not todo:
        return 0

    claude_code_backend._TIMEOUT_S = a.timeout
    client = LLMClient(model=a.model)
    lock = threading.Lock()
    done = {"n": 0}
    failed = []
    t0 = time.time()

    def run_one(cid):
        raw = client.call_json(prompt_for(cid), temperature=0.1, max_output_tokens=2000,
                               purpose="ci_blind_reader", claim_id=cid)
        vote, why = (None, "the model returned nothing") if raw is None \
            else normalise(raw, cid, a.model)
        if vote is None:
            with lock:
                failed.append((cid, why))
                done["n"] += 1
                print(f"  [{done['n']}/{len(todo)}] {cid} FAILED: {why}", flush=True)
            return
        json.dump(vote, open(os.path.join(a.votes_dir, f"{cid}.json"), "w"), indent=1)
        with lock:
            done["n"] += 1
            rate = done["n"] / max(time.time() - t0, 1) * 60
            print(f"  [{done['n']}/{len(todo)}] {cid} {vote['vote']:<4} "
                  f"{vote['defect'] or '-':<30} conf={vote['confidence']:<8} "
                  f"({rate:.1f} rows/min)", flush=True)

    with cf.ThreadPoolExecutor(max_workers=a.concurrency) as pool:
        list(pool.map(run_one, todo))

    mins = (time.time() - t0) / 60
    print(f"\n{len(todo) - len(failed)}/{len(todo)} rows read in {mins:.1f} min "
          f"-> {a.votes_dir}")
    if failed:
        print(f"{len(failed)} FAILED (re-run the same command to retry them):")
        for cid, why in failed:
            print(f"  {cid}: {why}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
