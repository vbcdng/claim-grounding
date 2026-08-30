# Run judge_bench with an arbitrary prompt file: run_variant.py <litellm-model> <prompt-path>
import os, sys
REPO = "/home/moje/Documents/python_projects/claim-grounding"
sys.path.insert(0, REPO)
src = open(os.path.join(REPO, "benchmarks", "judge_bench.py")).read()
body = src.split("P = os.path.join(REPO,")[0]  # strip the CLI tail
ns = {"__file__": os.path.join(REPO, "benchmarks", "judge_bench.py")}
exec(compile(body, "judge_bench_body", "exec"), ns)
ns["run"](sys.argv[1], sys.argv[2])
