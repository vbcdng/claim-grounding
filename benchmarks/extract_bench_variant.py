# extract_bench with injectable model + judgment prompt:
#   extract_variant.py <litellm-model> <judge-prompt-path> [mode]
import os, sys, importlib.util
REPO = "/home/moje/Documents/python_projects/claim-grounding"
sys.path.insert(0, REPO)
from modules.papertrail.llm_client import LLMClient

spec = importlib.util.spec_from_file_location("extract_bench", os.path.join(REPO, "benchmarks/extract_bench.py"))
eb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eb)  # main() is __main__-guarded

def key_for(model):
    # same convention as judge_bench.key_for (not importable — judge_bench runs at import)
    provider = model.split("/")[0]
    fname = "google_api_key.txt" if provider == "gemini" else f"{provider}_api_key.txt"
    path = os.path.join(REPO, "config", fname)
    return path if os.path.exists(path) else None

model, judge_path = sys.argv[1], sys.argv[2]
mode = sys.argv[3] if len(sys.argv) > 3 else "matcher"
llm = LLMClient(model=model, api_key=key_for(model))
extract_prompt = eb.matcher._load_prompt("pt_extract_evidence_prompt.txt")
judge_prompt = open(judge_path).read()

print(f"### extract bench mode={mode} model={model} judge_prompt={os.path.basename(judge_path)}")
hits = sup = 0
for case in eb._load():
    r = eb.run_case(case, mode, 6, llm, extract_prompt, judge_prompt)
    hits += bool(r["needle"]); sup += bool(r["supported"])
    print(f"  {case['tid']:>4} {case['key']:<14} needle={'HIT ' if r['needle'] else 'MISS'} "
          f"supported={r['supported']} calls={r['calls']} | {r['reason']}")
print(f"needle hits: {hits}/9   judged supported: {sup}/9")
