# Run verify_my_text.py with one or more prompt files swapped in, WITHOUT editing
# config/prompts/ — those files are shared by every session, matcher re-opens them on
# every call, and an edit mid-run silently changes the instructions for the remaining
# claims (CLAUDE.md convention, 2026-08-11; permanent fix = task #44). Generalised
# from verify_with_prompts_task18.py so any task, and the shared gate runner, can test
# a prompt variant on production code paths without touching production files.
#
#   verify_with_prompts.py <prompt_name>=<path> [<prompt_name>=<path> ...] \
#                          -- <verify_my_text.py args ...>
#
# <prompt_name> is the file name as matcher asks for it, e.g.
# pt_combined_judgment_prompt.txt, pt_component_split_prompt.txt.
import os, sys, runpy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from modules.papertrail import matcher

args = sys.argv[1:]
if "--" not in args:
    sys.exit("usage: verify_with_prompts.py name=path [name=path ...] -- <verify args>")
split = args.index("--")
pairs, verify_args = args[:split], args[split + 1:]
if not pairs:
    sys.exit("no prompt overrides given (name=path before --)")

for pair in pairs:
    if "=" not in pair:
        sys.exit(f"not a name=path pair: {pair}")
    name, path = pair.split("=", 1)
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        sys.exit(f"prompt file does not exist: {path}")
    matcher.PROMPT_OVERRIDES[name] = path
    print(f"[prompt override] {name} -> {path}", flush=True)

sys.argv = [os.path.join(REPO, "verify_my_text.py")] + verify_args
runpy.run_path(sys.argv[0], run_name="__main__")
