# Run verify_my_text.py with a swapped combined-judgment prompt, without
# touching config/prompts/ (other sessions share that file):
#   verify_with_prompt.py <judge-prompt-path> <verify_my_text args...>
import os, sys, runpy
REPO = "/home/moje/Documents/python_projects/claim-grounding"
sys.path.insert(0, REPO)
from modules.papertrail import matcher

prompt_path = os.path.abspath(sys.argv[1])
matcher.PROMPT_OVERRIDES["pt_combined_judgment_prompt.txt"] = prompt_path
print(f"[verify_with_prompt] combined-judgment prompt -> {prompt_path}")
sys.argv = [os.path.join(REPO, "verify_my_text.py")] + sys.argv[2:]
runpy.run_path(sys.argv[0], run_name="__main__")
