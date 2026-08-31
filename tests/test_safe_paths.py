"""
Tests for the path/link safety helpers (security review task #70, 2026-08-31).

Each test names the attack it blocks. The two path cases are the ones that were
actually exploitable before the fix: os.path.join drops its base directory when
the second part is absolute, and a '../' run walks out of the sources folder.
Offline, no network, no API calls.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from modules.papertrail.safe_paths import safe_key, resolve_inside, safe_link  # noqa: E402
from modules.papertrail.claude_research_importer import (  # noqa: E402
    PandocCitationRecognizer, _parse_bibtex,
)


class TestSafeKey(unittest.TestCase):
    def test_ordinary_keys_are_untouched(self):
        for key in ("smith2020", "van_der_berg2019", "abc-123", "A1"):
            self.assertEqual(safe_key(key), key)

    def test_absolute_path_key_loses_its_separators(self):
        # The exploit: os.path.join(sources, "/tmp/pwned.pdf") == "/tmp/pwned.pdf"
        self.assertEqual(safe_key("/tmp/pwned"), "tmp_pwned")

    def test_traversal_key_loses_its_separators(self):
        self.assertEqual(safe_key("x/../../../home/user/.bashrc"), "x_home_user_bashrc")

    def test_key_cannot_start_with_a_dash(self):
        # A leading '-' would be read as an option by a command-line program.
        self.assertFalse(safe_key("--output=/etc/passwd").startswith("-"))

    def test_unusable_keys_stay_distinct(self):
        # Two different junk keys must not collapse into one entry.
        self.assertNotEqual(safe_key("///"), safe_key("..."))

    def test_result_is_always_a_valid_marker(self):
        from modules.papertrail.text_decomposer import MARKER_RE
        for hostile in ("/tmp/x", "../../etc/passwd", "a:b.c~d", "///", "", "-x"):
            key = safe_key(hostile)
            self.assertRegex(key, r"^[A-Za-z0-9_-]+$")
            self.assertTrue(MARKER_RE.fullmatch(f"[[{key}]]"),
                            f"{key!r} is not a marker the claim splitter can read")


class TestResolveInside(unittest.TestCase):
    def setUp(self):
        self.base = os.path.realpath(os.path.dirname(__file__))

    def test_ordinary_name_resolves(self):
        got = resolve_inside(self.base, "smith2020.pdf")
        self.assertEqual(got, os.path.join(self.base, "smith2020.pdf"))

    def test_absolute_name_is_refused(self):
        self.assertIsNone(resolve_inside(self.base, "/tmp/pwned.pdf"))

    def test_traversal_is_refused(self):
        self.assertIsNone(resolve_inside(self.base, "x/../../../../tmp/pwned.pdf"))

    def test_the_base_itself_is_refused(self):
        self.assertIsNone(resolve_inside(self.base, "."))


class TestSafeLink(unittest.TestCase):
    def test_web_addresses_pass(self):
        for url in ("https://example.org/a.pdf", "http://example.org", "HTTPS://X/y"):
            self.assertEqual(safe_link(url), url)

    def test_script_scheme_is_dropped(self):
        self.assertEqual(safe_link("javascript:alert(1)"), "")
        self.assertEqual(safe_link("JavaScript:alert(1)"), "")
        self.assertEqual(safe_link("data:text/html,<script>x</script>"), "")

    def test_relative_paths_pass(self):
        self.assertEqual(safe_link("sources/x.pdf"), "sources/x.pdf")

    def test_empty_is_empty(self):
        self.assertEqual(safe_link(None), "")
        self.assertEqual(safe_link("  "), "")


class TestImporterSanitizesKeys(unittest.TestCase):
    """The importer is the seam where someone else's file becomes our filenames."""

    def test_marker_keys_are_cleaned(self):
        rec = PandocCitationRecognizer()
        cites = rec.find_citations("A claim [@x/../../../tmp/pwned] here.")
        self.assertEqual(cites[0].keys, ["x_tmp_pwned"])

    def _entries_for(self, bib_body):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".bib", delete=False) as f:
            f.write(bib_body)
            path = f.name
        try:
            return _parse_bibtex(path)
        finally:
            os.unlink(path)

    def test_absolute_bibtex_key_is_cleaned(self):
        # The .bib side is the looser of the two: its entry pattern accepts any
        # character but a comma or space, so it alone can carry a leading '/'.
        entries = self._entries_for("@article{/tmp/pwned,\n  title = {Anything}\n}\n")
        self.assertIn("tmp_pwned", entries)
        self.assertEqual(entries["tmp_pwned"]["key"], "tmp_pwned")
        self.assertNotIn("/tmp/pwned", entries)

    def test_traversal_key_cleans_to_the_same_string_on_both_sides(self):
        # A key the marker side can also express must clean identically, or an
        # entry and its citation would stop finding each other.
        entries = self._entries_for("@article{x/../../tmp/pwned,\n  title = {A}\n}\n")
        self.assertIn("x_tmp_pwned", entries)
        rec = PandocCitationRecognizer()
        self.assertEqual(rec.find_citations("[@x/../../tmp/pwned]")[0].keys,
                         ["x_tmp_pwned"])

    def test_ordinary_bibtex_key_is_untouched(self):
        entries = self._entries_for("@article{smith2020,\n  title = {A}\n}\n")
        self.assertIn("smith2020", entries)


class TestWizardNeverPrintsAKey(unittest.TestCase):
    """Task #70: the wizard invites the author to save the command it prints, so a
    pasted key must never appear in it."""

    def test_api_key_value_is_replaced_in_the_display_copy(self):
        from modules.papertrail.wizard import _redacted_argv
        argv = ["--text", "a.md", "--api-key", "AIzaSecretLiveKey123", "--open"]
        shown = _redacted_argv(argv)
        self.assertNotIn("AIzaSecretLiveKey123", shown)
        self.assertNotIn("AIzaSecretLiveKey123", " ".join(shown))
        # the real argv is untouched — only the printed copy changes
        self.assertEqual(argv[3], "AIzaSecretLiveKey123")
        # everything else survives
        self.assertEqual(shown[0:2], ["--text", "a.md"])
        self.assertEqual(shown[-1], "--open")

    def test_argv_without_a_key_is_unchanged(self):
        from modules.papertrail.wizard import _redacted_argv
        argv = ["--text", "a.md", "--open"]
        self.assertEqual(_redacted_argv(argv), argv)

    def test_a_trailing_api_key_flag_does_not_crash(self):
        from modules.papertrail.wizard import _redacted_argv
        self.assertEqual(_redacted_argv(["--api-key"]), ["--api-key"])


if __name__ == "__main__":
    unittest.main()
