#!/usr/bin/env python3
"""Task #31 follow-up probe: does pinning ONE supplier make the paid host
repeatable, and does numerical precision move the answer?

Replays ONE byte-identical production question (verified by fingerprint against
the paid run's own call log, same trick as task31_determinism_probe.py) N times
against three PINNED endpoints, substitutes refused:

  OpenInference bf16   $0.08/$0.35  full precision, cheapest bf16 listing
  DeepInfra    fp8     $0.13/$0.38  eight-bit compressed
  DeepInfra    fp4     $0.09/$0.34  four-bit compressed; the endpoint that
                                    served all 90 calls of the first probe

A wrong provider slug FAILS LOUDLY here (allow_fallbacks:false leaves nothing to
route to), and resp.provider is checked on every call, so a silent mis-pin cannot
be mistaken for a result. ~90 paid calls, ~$0.01 total.
"""
import collections
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.abspath("."))
from modules.papertrail import matcher  # noqa: E402

RUN = "data/citation_integrity/batch_dev_pilot100_run_task31_openrouter"
FREE_RUN = "data/citation_integrity/batch_dev_pilot100_run_task31_gglB"
GT = "data/citation_integrity/batch_dev_pilot100/ci_ground_truth.json"
MODEL = "openrouter/google/gemma-4-31b-it"
CLAIM = "t105"
N = 30

ARMS = [
    ("OpenInference bf16 (full precision)", "OpenInference", "bf16", 0.08, 0.35),
    ("DeepInfra fp8 (eight-bit)", "DeepInfra", "fp8", 0.13, 0.38),
    ("DeepInfra fp4 (four-bit)", "DeepInfra", "fp4", 0.09, 0.34),
]


def build_prompt():
    a = json.load(open(f"{RUN}/analysis.json", encoding="utf-8"))
    c = [x for x in a["text_claims"] if x["id"] == CLAIM][0]
    tmpl = open("config/prompts/pt_support_judgment_prompt.txt", encoding="utf-8").read()
    passage = f"From cidev0099: {c['evidence']['window']}"
    p = matcher._inject_date_rule(
        tmpl.replace("{CLAIM}", c["text"]).replace("{PASSAGE}", passage), passage)
    h = hashlib.sha256(p.encode()).hexdigest()
    seen = set()
    for line in open(f"{RUN}/llm_calls.jsonl", encoding="utf-8"):
        try:
            seen.add(json.loads(line)["prompt_sha256"])
        except Exception:
            pass
    print(f"question rebuilt: {len(p)} chars, fingerprint {h[:16]}, "
          f"present in the paid run's own log: {h in seen}")
    assert h in seen, "reconstruction does not match a real production request"
    return p, c


def context(claim):
    """What the two hosts already answered for this claim, and the answer key."""
    paid = claim.get("verdict")
    free = None
    try:
        a = json.load(open(f"{FREE_RUN}/analysis.json", encoding="utf-8"))
        free = [x for x in a["text_claims"] if x["id"] == CLAIM][0].get("verdict")
    except Exception:
        pass
    key = None
    try:
        gt = json.load(open(GT, encoding="utf-8"))
        rows = gt if isinstance(gt, list) else gt.get("rows", [])
        for r in rows:
            if str(r.get("ci_id", "")).endswith("cidev0099") or r.get("id") == "cidev0099":
                key = r.get("label") or r.get("grounding_side")
                break
    except Exception:
        pass
    print(f"for this claim: paid run answered {paid!r}, free Google run answered "
          f"{free!r}, answer key says {key!r}")


def verdict_of(text):
    try:
        return str(json.loads(text[text.index("{"):text.rindex("}") + 1]).get("supported"))
    except Exception:
        return "unparseable"


def main():
    import litellm
    litellm.drop_params = True
    litellm.suppress_debug_info = True
    key = open("config/openrouter_api_key.txt", encoding="utf-8").read().strip()
    prompt, claim = build_prompt()
    context(claim)

    total = 0.0
    for label, slug, quant, price_in, price_out in ARMS:
        body = {"provider": {"order": [slug], "quantizations": [quant],
                             "allow_fallbacks": False}}
        answers, served, in_tok, out_tok, failed = [], collections.Counter(), 0, 0, 0
        print(f"\n=== {label} — pinned to {slug}, {quant}, no substitutes ===",
              flush=True)
        for i in range(N):
            try:
                r = litellm.completion(
                    model=MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=2048,
                    api_key=key,
                    extra_body=body)
            except Exception as e:
                failed += 1
                print(f"  call {i} failed: {type(e).__name__}: {str(e)[:160]}",
                      flush=True)
                continue
            answers.append((r.choices[0].message.content or "").strip())
            served[str(getattr(r, "provider", None))] += 1
            in_tok += getattr(r.usage, "prompt_tokens", 0) or 0
            out_tok += getattr(r.usage, "completion_tokens", 0) or 0
        c = collections.Counter(answers)
        v = collections.Counter(verdict_of(a) for a in answers)
        cost = in_tok / 1e6 * price_in + out_tok / 1e6 * price_out
        total += cost
        print(f"  {len(answers)} answers ({failed} failed), {len(c)} distinct, "
              f"verdicts {dict(v)}")
        print(f"  served by: {dict(served)}")
        for txt, n in c.most_common():
            print(f"   {n:>3}x  supported={verdict_of(txt):<11} {txt[:110]!r}")
        print(f"  {in_tok} in / {out_tok} out tokens -> ${cost:.4f}")
    print(f"\ntotal at list prices: ${total:.4f}")


if __name__ == "__main__":
    main()
