"""Task #44: prompt snapshot + fingerprints.

What must hold:
  * A prompt file edited after the snapshot keeps serving its run-start text
    (the whole point: a mid-run edit must not reach later calls).
  * PROMPT_OVERRIDES stays dynamic: an override installed after a load still
    takes effect (cache is keyed by resolved path, not by name).
  * metadata_block() reports fingerprints, override names, used names, count.
  * rerun.changed_prompts() flags a changed/removed prompt the previous run
    used, ignores changes to prompts it never loaded, and returns None for
    runs that predate prompt tracking.

All offline, no LLM.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from modules.papertrail import prompt_store, rerun


class PromptStoreTests(unittest.TestCase):
    def setUp(self):
        prompt_store.reset()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self._patch = patch.object(prompt_store, "PROMPTS_DIR", self.dir)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.tmp.cleanup()
        prompt_store.reset()

    def _write(self, name, text, where=None):
        path = os.path.join(where or self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_snapshot_freezes_text_against_midrun_edit(self):
        self._write("a.txt", "run-start wording")
        fps = prompt_store.snapshot()
        self.assertIn("a.txt", fps)
        self._write("a.txt", "EDITED MID-RUN")
        self.assertEqual(prompt_store.load("a.txt"), "run-start wording")
        # fingerprint still matches the run-start text
        self.assertEqual(prompt_store.metadata_block()["fingerprints"]["a.txt"],
                         prompt_store.fingerprint("run-start wording"))

    def test_first_load_freezes_even_without_snapshot(self):
        self._write("b.txt", "first")
        self.assertEqual(prompt_store.load("b.txt"), "first")
        self._write("b.txt", "second")
        self.assertEqual(prompt_store.load("b.txt"), "first")

    def test_override_installed_after_a_load_still_takes_effect(self):
        self._write("c.txt", "production")
        self.assertEqual(prompt_store.load("c.txt"), "production")
        alt = tempfile.TemporaryDirectory()
        self.addCleanup(alt.cleanup)
        override = self._write("c_variant.txt", "override text", where=alt.name)
        self.assertEqual(prompt_store.load("c.txt", {"c.txt": override}),
                         "override text")
        block = prompt_store.metadata_block()
        self.assertIn("c.txt", block["overridden"])
        self.assertEqual(block["fingerprints"]["c.txt"],
                         prompt_store.fingerprint("override text"))
        # dropping the override goes back to the (still frozen) production text
        self.assertEqual(prompt_store.load("c.txt"), "production")
        self.assertNotIn("c.txt", prompt_store.metadata_block()["overridden"])

    def test_snapshot_marks_nothing_used_load_does(self):
        self._write("d.txt", "x")
        self._write("e.txt", "y")
        prompt_store.snapshot()
        self.assertEqual(prompt_store.metadata_block()["used"], [])
        prompt_store.load("d.txt")
        block = prompt_store.metadata_block()
        self.assertEqual(block["used"], ["d.txt"])
        self.assertEqual(block["count"], 2)

    def test_snapshot_includes_override_only_names(self):
        alt = tempfile.TemporaryDirectory()
        self.addCleanup(alt.cleanup)
        override = self._write("x.txt", "ovr", where=alt.name)
        fps = prompt_store.snapshot({"x.txt": override})
        self.assertEqual(fps["x.txt"], prompt_store.fingerprint("ovr"))


class ChangedPromptsTests(unittest.TestCase):
    def test_predates_tracking(self):
        self.assertIsNone(rerun.changed_prompts(None, {"a": "1"}))
        self.assertIsNone(rerun.changed_prompts({}, {"a": "1"}))
        self.assertIsNone(rerun.changed_prompts({"fingerprints": "junk"}, {}))

    def test_identical_prompts_reuse_ok(self):
        prev = {"fingerprints": {"a": "1", "b": "2"}, "used": ["a", "b"]}
        self.assertEqual(rerun.changed_prompts(prev, {"a": "1", "b": "2"}), set())

    def test_changed_used_prompt_is_flagged(self):
        prev = {"fingerprints": {"a": "1", "b": "2"}, "used": ["a"]}
        self.assertEqual(rerun.changed_prompts(prev, {"a": "9", "b": "2"}), {"a"})

    def test_changed_unused_prompt_is_ignored(self):
        prev = {"fingerprints": {"a": "1", "b": "2"}, "used": ["a"]}
        self.assertEqual(rerun.changed_prompts(prev, {"a": "1", "b": "9"}), set())

    def test_used_prompt_removed_counts_as_changed(self):
        prev = {"fingerprints": {"a": "1"}, "used": ["a"]}
        self.assertEqual(rerun.changed_prompts(prev, {}), {"a"})

    def test_legacy_block_without_used_compares_everything(self):
        prev = {"fingerprints": {"a": "1", "b": "2"}}
        self.assertEqual(rerun.changed_prompts(prev, {"a": "1", "b": "9"}), {"b"})


class MatcherIntegrationTests(unittest.TestCase):
    """matcher._load_prompt goes through the store and stays override-aware."""

    def setUp(self):
        from modules.papertrail import matcher
        self.matcher = matcher
        prompt_store.reset()
        self._saved = dict(matcher.PROMPT_OVERRIDES)
        matcher.PROMPT_OVERRIDES.clear()

    def tearDown(self):
        self.matcher.PROMPT_OVERRIDES.clear()
        self.matcher.PROMPT_OVERRIDES.update(self._saved)
        prompt_store.reset()

    def test_production_prompt_loads_and_is_fingerprinted(self):
        text = self.matcher._load_prompt("pt_support_judgment_prompt.txt")
        self.assertTrue(text.strip())
        block = prompt_store.metadata_block()
        self.assertIn("pt_support_judgment_prompt.txt", block["used"])
        self.assertEqual(block["fingerprints"]["pt_support_judgment_prompt.txt"],
                         prompt_store.fingerprint(text))

    def test_override_after_plain_load_wins(self):
        plain = self.matcher._load_prompt("pt_support_judgment_prompt.txt")
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("override wording")
            path = f.name
        self.addCleanup(os.unlink, path)
        self.matcher.PROMPT_OVERRIDES["pt_support_judgment_prompt.txt"] = path
        self.assertEqual(
            self.matcher._load_prompt("pt_support_judgment_prompt.txt"),
            "override wording")
        self.matcher.PROMPT_OVERRIDES.clear()
        self.assertEqual(
            self.matcher._load_prompt("pt_support_judgment_prompt.txt"), plain)


if __name__ == "__main__":
    unittest.main()
