# Extraction-quality benchmark from the paper1 audit's known-needle cases — see
# docs/PAPER1_TUNING_STATE.md ("run 3" section). Measures whether the full-text
# extraction fallback finds the sentence that is KNOWN to be in the source.
#
# Usage: venv/bin/python3 benchmarks/extract_bench.py <whole|chunked|gated|matcher> [K] [liberal]
#   matcher = the PRODUCTION code path (matcher._extract_evidence: gated chunks +
#             retry-on-empty + windowed judging). NOTE: the liberal addon is now
#             MERGED into pt_extract_evidence_prompt.txt, so plain modes already
#             use it; the 'liberal' flag would double-add it (historical only).
#   whole   = production behavior: entire source in ONE extraction call
#   chunked = one extraction call per ~1200-word chunk, hits pooled
#   gated   = chunked, but only the top-K chunks by max sentence cosine vs the
#             claim (K default 6; local SPECTER, reuses the embeddings cache)
# LIVE API (flash-lite): whole/chunked ~ $0.03/run, gated ~ $0.01/run.
"""Cases = the 9 claims run 3 left wrongly-unsupported whose needle sentence is
verified (by substring search) to sit in the cached source text. Success =
extraction returns a sentence containing the needle; judgment is also reported."""
import sys, os, json, re, time
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from modules.papertrail import matcher, embeddings
from modules.papertrail.llm_client import LLMClient, parallel_map

VER = os.path.join(REPO, 'data/paper1_verification')
CHUNK_WORDS = 1200

# (claim_id, source_key, needles) — needle hit = the right sentence was extracted.
CASES = [
    ("t6",  "epochai2025",   ["4.8"]),
    ("t17", "draghi2024",    ["70% of foundational"]),
    ("t22", "cnbc2025",      ["200 million"]),
    ("t24", "aljazeera2026", ["foreign nationals"]),
    ("t32", "sadowski2019",  ["form of capital", "data as capital"]),
    ("t35", "good1965",      ["intelligence explosion"]),
    ("t59", "alramiah2025",  ["Pause Button", "Non-Proliferation", "Wassenaar"]),
    ("t74", "denain2026",    ["doubling", "scale up"]),
    ("t80", "macaskill2025", ["concentration of power", "grand challenge"]),
]


def _load():
    a = json.load(open(os.path.join(VER, 'analysis_run3.json')))
    claims = {c['id']: c['text'] for c in a['text_claims']}
    srcs = {s['key']: s for s in a['sources']}
    out = []
    for tid, key, needles in CASES:
        d = json.load(open(os.path.join(VER, 'source_claims', srcs[key]['paper_id'] + '.json')))
        out.append({"tid": tid, "key": key, "needles": needles, "claim": claims[tid],
                    "pid": srcs[key]['paper_id'], "title": srcs[key]['title'],
                    "sents": d['sentences']})
    return out


def _chunks(sents):
    """[(text, [sentence indices])] of ~CHUNK_WORDS words each."""
    chunks, cur, idxs, words = [], [], [], 0
    for j, s in enumerate(sents):
        t = s.get('text', '')
        cur.append(t); idxs.append(j); words += len(t.split())
        if words >= CHUNK_WORDS:
            chunks.append((" ".join(cur), idxs)); cur, idxs, words = [], [], 0
    if cur:
        chunks.append((" ".join(cur), idxs))
    return chunks


_norm_c = lambda s: re.sub(r'\s+', '', s.lower())


def run_case(case, mode, K, llm, extract_prompt, judge_prompt):
    sents, claim, title = case['sents'], case['claim'], case['title']
    if mode == 'matcher':
        # cosine row via the same embeddings the pipeline uses
        s_texts = [s.get('text', '') for s in sents]
        s_vecs = embeddings.embed_cached(
            s_texts, os.path.join(VER, 'embeddings', f"{case['pid']}.sents.npz"))
        from sentence_transformers import util
        row = util.cos_sim(embeddings.embed([claim]), s_vecs)[0].tolist()
        src = {"title": title, "sentences": sents}
        e = matcher._extract_evidence(claim, case['pid'], src, llm,
                                      extract_prompt, judge_prompt, row=row)
        hay = _norm_c((e.get("window") or "") + " " + (e.get("sentence") or ""))
        needle_hit = any(_norm_c(n) in hay for n in case['needles'])
        n_chunks = min(len(matcher._chunk_sents(sents)), matcher.EXTRACT_TOP_CHUNKS)
        return {"needle": needle_hit, "supported": e["supported"],
                "n_extracted": len(e.get("window", "").split(". ")) if e.get("window") else 0,
                "calls": n_chunks + 1,
                "in_tokens": int(n_chunks * matcher.EXTRACT_CHUNK_WORDS * 1.4),
                "reason": (e.get("reason") or "")[:70]}
    if mode == 'whole':
        parts = [" ".join(s.get('text', '') for s in sents)]
    else:
        ch = _chunks(sents)
        if mode == 'gated' and len(ch) > K:
            s_texts = [s.get('text', '') for s in sents]
            s_vecs = embeddings.embed_cached(
                s_texts, os.path.join(VER, 'embeddings', f"{case['pid']}.sents.npz"))
            from sentence_transformers import util
            c_vec = embeddings.embed([claim])
            sim = util.cos_sim(c_vec, s_vecs)[0]
            scored = sorted(range(len(ch)), key=lambda i: -max(sim[j].item() for j in ch[i][1]))
            keep = sorted(scored[:K])           # keep document order
            ch = [ch[i] for i in keep]
        parts = [c[0] for c in ch]

    def extract(part):
        raw = llm.call(extract_prompt.replace("{CLAIM}", claim).replace("{SOURCE}", part),
                       temperature=0.0, max_output_tokens=8192 if mode == 'whole' else 2048)
        return matcher._parse_sentences(raw)

    pooled = [s for out in parallel_map(extract, parts, workers=4) for s in (out or [])]
    mapped = [matcher._map_to_index(e, sents) for e in pooled]
    mapped = [m for m in mapped if not matcher._degenerate(m["text"])
              and not (m["j"] == -1 and matcher._is_claim_echo(m["text"], claim))]
    # dedupe by index, keep order
    seen, uniq = set(), []
    for m in mapped:
        k = m["j"] if m["j"] >= 0 else m["text"]
        if k not in seen:
            seen.add(k); uniq.append(m)
    needle_hit = any(_norm_c(n) in _norm_c(m["text"]) for n in case['needles'] for m in uniq)
    supported, reason = False, "nothing extracted"
    if uniq:
        window = " ".join(m["text"] for m in uniq[:12])
        passage = f"From {title}: {window}"
        supported, reason = matcher._parse_support(
            llm.call(judge_prompt.replace("{CLAIM}", claim).replace("{PASSAGE}", passage),
                     temperature=0.0, max_output_tokens=4096))
    in_tokens = int(sum(len(p.split()) for p in parts) * 1.4)
    return {"needle": needle_hit, "supported": supported, "n_extracted": len(uniq),
            "calls": len(parts) + (1 if uniq else 0), "in_tokens": in_tokens,
            "reason": reason[:70]}


# Recall-first extraction rules, mirroring the entailment rule that fixed the
# judgment prompts (see PAPER1_TUNING_STATE.md): extraction feeds a verifying
# judge, so a missed sentence is fatal while an extra one is filtered downstream.
LIBERAL_ADDON = """- Paraphrase and entailment COUNT as support: a sentence supports the claim if its content, restated plainly, asserts part of the claim — different wording, different phrasing of numbers ("64.4% of the time" vs "about two-thirds"), or a plain-language equivalent all qualify.
- The claim may contain the author's own framing, transitions, or conclusions; look for sentences backing its FACTUAL parts and ignore the rhetorical frame around them.
- Recall matters more than precision here: every sentence you return is re-verified by a later step, so when in doubt, INCLUDE the sentence.
"""


def main():
    mode = sys.argv[1]
    K = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    liberal = "liberal" in sys.argv[2:]
    assert mode in ("whole", "chunked", "gated", "matcher")
    llm = LLMClient(model="gemini/gemini-2.5-flash-lite",
                    api_key=os.path.join(REPO, 'config/google_api_key.txt'))
    extract_prompt = matcher._load_prompt("pt_extract_evidence_prompt.txt")
    if liberal:
        extract_prompt = extract_prompt.replace(
            "Return ONLY a JSON object", LIBERAL_ADDON + "\nReturn ONLY a JSON object")
    judge_prompt = matcher._load_prompt("pt_combined_judgment_prompt.txt")
    cases = _load()
    t0 = time.time()
    n_needle = n_sup = calls = toks = 0
    print(f"### extract_bench mode={mode}" + (f" K={K}" if mode == 'gated' else "")
          + (" +liberal-prompt" if liberal else ""))
    for case in cases:
        r = run_case(case, mode, K, llm, extract_prompt, judge_prompt)
        n_needle += r['needle']; n_sup += r['supported']
        calls += r['calls']; toks += r['in_tokens']
        print(f"  {'HIT ' if r['needle'] else 'MISS'} {case['tid']} ({case['key']}): "
              f"needle={r['needle']} judged={r['supported']} "
              f"extracted={r['n_extracted']} calls={r['calls']} | {r['reason']}", flush=True)
    print(f"needle {n_needle}/{len(cases)}, judged-supported {n_sup}/{len(cases)}, "
          f"{calls} LLM calls, ~{toks/1000:.0f}k in-tokens, {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
