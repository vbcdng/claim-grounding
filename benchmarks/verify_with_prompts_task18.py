# Task #18 loop runner: verify_my_text.py with the round's combined-judgment
# AND component-split prompts swapped in, without touching config/prompts/
# (other sessions share those files). See docs/TASK18_LOOP.md.
#   verify_with_prompts_task18.py <judge-prompt> <split-prompt> <verify args...>
import os, sys, runpy
REPO = "/home/moje/Documents/python_projects/claim-grounding"
sys.path.insert(0, REPO)
from modules.papertrail import matcher

judge_path = os.path.abspath(sys.argv[1])
split_path = os.path.abspath(sys.argv[2])
matcher.PROMPT_OVERRIDES["pt_combined_judgment_prompt.txt"] = judge_path
matcher.PROMPT_OVERRIDES["pt_component_split_prompt.txt"] = split_path
print(f"[task18] combined-judgment prompt -> {judge_path}")
print(f"[task18] component-split prompt  -> {split_path}")
sys.argv = [os.path.join(REPO, "verify_my_text.py")] + sys.argv[3:]
runpy.run_path(sys.argv[0], run_name="__main__")
