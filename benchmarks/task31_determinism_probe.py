#!/usr/bin/env python3
"""Task #31 probe: is the paid host's variation caused by a setting we omit?

Replays ONE byte-identical production question (verified by fingerprint against
the paid run's own call log) N times per arm and counts distinct answers:

  arm "as we run it"  = exactly the kwargs llm_client sends today
  arm "+ fixed seed"  = the same, plus seed=42
  arm "+ seed + top_k"= the same, plus seed and greedy tie-breaking

Also records whichever supplier field the response exposes, which would answer
the multi-supplier question directly. Sequential; ~60-90 paid calls total.
"""
import hashlib
import json
import os
import sys
import collections

sys.path.insert(0, os.path.abspath("."))
from modules.papertrail import matcher  # noqa: E402

RUN = "data/citation_integrity/batch_dev_pilot100_run_task31_openrouter"
MODEL = "openrouter/google/gemma-4-31b-it"
N = 30


def build_prompt():
    a = json.load(open(f"{RUN}/analysis.json", encoding="utf-8"))
    c = [x for x in a["text_claims"] if x["id"] == "t105"][0]
    win = c["evidence"]["window"]
    tmpl = open("config/prompts/pt_support_judgment_prompt.txt", encoding="utf-8").read()
    passage = f"From cidev0099: {win}"
    p = matcher._inject_date_rule(
        tmpl.replace("{CLAIM}", c["text"]).replace("{PASSAGE}", passage), passage)
    h = hashlib.sha256(p.encode()).hexdigest()
    seen = set()
    for line in open(f"{RUN}/llm_calls.jsonl", encoding="utf-8"):
        try:
            seen.add(json.loads(line)["prompt_sha256"])
        except Exception:
            pass
    print(f"prompt {len(p)} chars, fingerprint {h[:16]}, "
          f"present in the paid run's own log: {h in seen}")
    assert h in seen, "reconstruction does not match a real production request"
    return p


def supplier_of(resp):
    """Whatever the response reveals about which supplier served it."""
    out = {}
    for attr in ("model", "provider", "_hidden_params"):
        v = getattr(resp, attr, None)
        if v is not None:
            out[attr] = v if not isinstance(v, dict) else {
                k: v[k] for k in list(v)[:12]}
    extra = getattr(resp, "model_extra", None)
    if extra:
        out["model_extra"] = {k: extra[k] for k in list(extra)[:12]}
    return out


def main():
    import litellm
    litellm.drop_params = True          # exactly as llm_client does
    litellm.suppress_debug_info = True
    key = open("config/openrouter_api_key.txt", encoding="utf-8").read().strip()
    prompt = build_prompt()

    arms = {
        "as we run it": {},
        "+ fixed seed": {"seed": 42},
        "+ seed + greedy tie-break": {"seed": 42, "top_k": 1, "top_p": 1.0},
    }
    in_tok = out_tok = 0
    first_meta = {}
    for name, extra in arms.items():
        answers, suppliers = [], collections.Counter()
        for i in range(N):
            try:
                r = litellm.completion(
                    model=MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=2048,
                    api_key=key,
                    **extra)
            except Exception as e:
                print(f"  call {i} failed: {type(e).__name__}: {str(e)[:120]}")
                continue
            answers.append((r.choices[0].message.content or "").strip())
            u = r.usage
            in_tok += getattr(u, "prompt_tokens", 0) or 0
            out_tok += getattr(u, "completion_tokens", 0) or 0
            if not first_meta:
                first_meta = supplier_of(r)
            for k in ("provider", "model"):
                v = getattr(r, k, None)
                if isinstance(v, str):
                    suppliers[f"{k}={v}"] += 1
        c = collections.Counter(answers)
        print(f"\narm '{name}': {len(answers)} answers, {len(c)} distinct")
        for txt, n in c.most_common():
            verdict = "?"
            try:
                verdict = str(json.loads(txt[txt.index('{'):txt.rindex('}') + 1])
                              .get("supported"))
            except Exception:
                pass
            print(f"   {n:>3}x  supported={verdict:<5}  {txt[:120]!r}")
        if suppliers:
            print(f"   response fields: {dict(suppliers)}")

    print(f"\nfirst response's identifying fields: {json.dumps(first_meta, default=str)[:600]}")
    cost = in_tok / 1e6 * 0.08 + out_tok / 1e6 * 0.35
    print(f"\ntotal {in_tok} in / {out_tok} out tokens -> ${cost:.4f} at list price")


if __name__ == "__main__":
    main()
