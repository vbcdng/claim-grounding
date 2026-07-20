"""Every file the code can open by a repo-relative path must exist on disk.

Born 2026-07-20: the first external tester ran a trimmed copy of the repo and
`--backend claude-code` crashed mid-run because the Haiku judging rubric lives
in benchmarks/, which the trimmed copy did not include. The code now degrades
gracefully, but a distributed copy should simply be complete — and because
this test ships in tests/, running the suite INSIDE any distributed copy
(release zip, public snapshot) proves runtime completeness of that copy.

Run: venv/bin/python3 -m unittest tests.test_runtime_resources -v
"""
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Where a prompt file referenced by bare name may legitimately live.
PROMPT_DIRS = [
    os.path.join(REPO, "config", "prompts"),
    os.path.join(REPO, "benchmarks"),  # the Haiku-tuned judging rubric
]

# Files individual modules open directly (path relative to repo root).
KNOWN_RUNTIME_FILES = [
    "config/gemini_config.json",
    "benchmarks/pt_combined_judgment_haiku_v1.txt",  # --backend claude-code
]

_PROMPT_LITERAL = re.compile(r'"(pt_[a-z0-9_]+\.txt)"')


def _python_sources():
    roots = [os.path.join(REPO, "modules")]
    for entry in os.listdir(REPO):
        if entry.endswith(".py"):
            yield os.path.join(REPO, entry)
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn)


class RuntimeResourcesExist(unittest.TestCase):
    def test_every_prompt_literal_resolves(self):
        """Each "pt_*.txt" string in runtime code exists in a prompt dir."""
        missing = []
        for src_path in _python_sources():
            with open(src_path, "r", encoding="utf-8") as f:
                src = f.read()
            for name in set(_PROMPT_LITERAL.findall(src)):
                if not any(os.path.isfile(os.path.join(d, name))
                           for d in PROMPT_DIRS):
                    rel = os.path.relpath(src_path, REPO)
                    missing.append(f"{rel} -> {name}")
        self.assertEqual(missing, [],
                         "prompt files referenced in code but absent from "
                         "config/prompts/ and benchmarks/:\n  "
                         + "\n  ".join(missing))

    def test_known_runtime_files_exist(self):
        missing = [p for p in KNOWN_RUNTIME_FILES
                   if not os.path.isfile(os.path.join(REPO, p))]
        self.assertEqual(missing, [],
                         f"runtime files missing from this copy: {missing}")
