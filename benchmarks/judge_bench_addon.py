# Scratch wrapper: run judge_bench's run() with an arbitrary model + addon
# (the repo bench only wires addons to fixed gemini variants). Usage:
#   venv/bin/python3 judge_bench_addon.py <litellm-model> [entailment|framing]
import os, sys

REPO = "/home/moje/Documents/python_projects/claim-grounding"
sys.path.insert(0, REPO)

src = open(os.path.join(REPO, "benchmarks", "judge_bench.py")).read()
body = src.split("P = os.path.join(REPO,")[0]  # strip the CLI tail
ns = {"__file__": os.path.join(REPO, "benchmarks", "judge_bench.py")}
exec(compile(body, "judge_bench_body", "exec"), ns)

model = sys.argv[1]
addon = ns["ENTAILMENT_ADDON"] if (len(sys.argv) < 3 or sys.argv[2] == "entailment") else ns["FRAMING_ADDON"]
P = os.path.join(REPO, "config/prompts/pt_combined_judgment_prompt.txt")
ns["run"](model, P, addon)
