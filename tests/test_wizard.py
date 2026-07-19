"""Wizard tests — scripted answers via input_fn; no API/network/TTY needed.

Run:  venv/bin/python3 -m unittest tests.test_wizard -v
"""
import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.papertrail import wizard


def scripted(*answers):
    it = iter(answers)
    return lambda prompt: next(it)


def run_quiet(input_fn, run_script=None):
    kw = {"run_script": run_script} if run_script else {}
    with redirect_stdout(StringIO()):
        return wizard.run_wizard(input_fn=input_fn, **kw)


class WizardBase(unittest.TestCase):
    """A minimal import layout (text + sibling refs + sources/) and an empty
    PROJECT_ROOT so key-file detection ignores this machine's real config/."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.text = os.path.join(self.tmp, "my_article.md")
        with open(self.text, "w") as f:
            f.write("A claim. [[a]]\n")
        with open(self.text + ".refs.txt", "w") as f:
            f.write("a = a.txt\n")
        self.src = os.path.join(self.tmp, "sources")
        os.makedirs(self.src)
        with open(os.path.join(self.src, "a.txt"), "w") as f:
            f.write("Some source text.")
        self.fakeroot = os.path.join(self.tmp, "fakeroot")
        os.makedirs(os.path.join(self.fakeroot, "config"))
        self._root = patch.object(wizard, "PROJECT_ROOT", self.fakeroot)
        self._root.start()

    def tearDown(self):
        self._root.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestWizardFlows(WizardBase):

    def test_happy_path_defaults(self):
        argv = run_quiet(scripted(
            self.text,   # text file
            "",          # sources folder -> default <textdir>/sources
            "",          # output folder -> default data/<stem>_verification
            "1",         # model menu -> recommended flash-lite
            "",          # api key (no key file in fakeroot) -> env var
            "",          # concurrency -> default 4 (flag omitted)
            "",          # second opinion -> default no (flag omitted)
            "",          # open in browser -> default yes
        ))
        self.assertEqual(argv[:2], ["--text", self.text])
        self.assertEqual(argv[argv.index("--sources") + 1], self.src)
        self.assertEqual(argv[argv.index("--output-dir") + 1],
                         os.path.join("data", "my_article_verification"))
        self.assertEqual(argv[argv.index("--model") + 1], wizard.RECOMMENDED_MODEL)
        self.assertNotIn("--references", argv)   # sibling refs auto-detected
        self.assertNotIn("--concurrency", argv)  # default kept
        self.assertNotIn("--api-key", argv)
        self.assertNotIn("--second-opinion", argv)  # default kept
        self.assertIn("--open", argv)

    def test_invalid_paths_reask(self):
        argv = run_quiet(scripted(
            "/nonexistent/file.md",   # rejected, re-asked
            self.text,
            "/nonexistent/dir",       # rejected, re-asked
            self.src,
            "out", "1", "", "", "", "n",
        ))
        self.assertEqual(argv[argv.index("--text") + 1], self.text)
        self.assertEqual(argv[argv.index("--sources") + 1], self.src)
        self.assertNotIn("--open", argv)

    def test_ollama_flow_no_key_question(self):
        argv = run_quiet(scripted(
            self.text, "", "out",
            "4",    # local via Ollama
            "",     # tag -> default
            "",     # url -> default
            # no API-key question for ollama
            "8",    # concurrency (non-default -> flag included)
            "",     # second opinion
            "n",
        ))
        self.assertEqual(argv[argv.index("--model") + 1],
                         f"ollama/{wizard.DEFAULT_OLLAMA_TAG}")
        self.assertEqual(argv[argv.index("--api-base") + 1], wizard.DEFAULT_OLLAMA_URL)
        self.assertEqual(argv[argv.index("--concurrency") + 1], "8")

    def test_claude_code_flow_no_key_question(self):
        with patch.object(wizard.shutil, "which", return_value="/usr/bin/claude"):
            argv = run_quiet(scripted(
                self.text, "", "out",
                "3",        # claude-code ($0 backend)
                "",         # alias -> default haiku
                # no API-key question for claude-code
                "", "", "n",
            ))
        self.assertEqual(argv[argv.index("--model") + 1], "claude-code/haiku")
        self.assertNotIn("--api-key", argv)
        self.assertNotIn("--api-base", argv)

    def test_claude_code_sonnet_alias(self):
        with patch.object(wizard.shutil, "which", return_value="/usr/bin/claude"):
            argv = run_quiet(scripted(
                self.text, "", "out",
                "3",
                "sonnet",
                "", "", "n",
            ))
        self.assertEqual(argv[argv.index("--model") + 1], "claude-code/sonnet")

    def test_second_opinion_yes_adds_flag(self):
        argv = run_quiet(scripted(
            self.text, "", "out", "1",
            "",         # api key
            "",         # concurrency
            "y",        # second opinion
            "n",        # open
        ))
        self.assertIn("--second-opinion", argv)

    def test_claude_code_unavailable_reasks_menu(self):
        with patch.object(wizard.shutil, "which", return_value=None):
            argv = run_quiet(scripted(
                self.text, "", "out",
                "3",        # claude-code -> `claude` CLI missing, menu re-asked
                "1",        # fall back to recommended
                "",         # api key
                "", "", "n",
            ))
        self.assertEqual(argv[argv.index("--model") + 1], wizard.RECOMMENDED_MODEL)

    def test_other_model_uses_provider_keyfile(self):
        keyfile = os.path.join(self.fakeroot, "config", "openai_api_key.txt")
        with open(keyfile, "w") as f:
            f.write("sk-test")
        argv = run_quiet(scripted(
            self.text, "", "out",
            "5",                     # other
            "gpt-4o-mini",           # rejected: no provider prefix
            "openai/gpt-4o-mini",
            # key file found -> no key question
            "", "", "n",
        ))
        self.assertEqual(argv[argv.index("--model") + 1], "openai/gpt-4o-mini")
        self.assertEqual(argv[argv.index("--api-key") + 1], keyfile)

    def test_gemini_project_keyfile_not_passed(self):
        # LLMClient falls back to the gemini key file itself; no --api-key flag.
        with open(os.path.join(self.fakeroot, "config", "google_api_key.txt"), "w") as f:
            f.write("g-test")
        argv = run_quiet(scripted(
            self.text, "", "out", "1",
            # key file found -> no key question
            "", "", "n",
        ))
        self.assertNotIn("--api-key", argv)

    def test_refs_question_when_no_sibling(self):
        text = os.path.join(self.tmp, "draft.md")
        with open(text, "w") as f:
            f.write("A claim. [[a]]\n")
        refs = os.path.join(self.tmp, "custom_refs.txt")
        with open(refs, "w") as f:
            f.write("a = a.txt\n")
        argv = run_quiet(scripted(
            text, self.src,
            str(refs),               # refs question appears (no sibling .refs.txt)
            "out", "1", "", "", "", "n",
        ))
        self.assertEqual(argv[argv.index("--references") + 1], refs)

    def test_abort_on_eof(self):
        def eof(prompt):
            raise EOFError
        with self.assertRaises(SystemExit):
            run_quiet(eof)


class TestWizardPipeline(WizardBase):
    """The 2026-07-12 guards: [@key] export detection → importer, missing-source
    check → downloader, marker-less warn-and-confirm. Pipeline scripts are faked."""

    def _runner(self, effect=None):
        calls = []
        def run(script, args):
            calls.append((script, list(args)))
            return effect(script, args) if effect else 0
        return calls, run

    def test_pandoc_export_offers_import(self):
        exp = os.path.join(self.tmp, "export.md")
        with open(exp, "w") as f:
            f.write("Print caused it [@eisenstein1980]. More [@rubin2014; @dittmar2011].\n")
        with open(os.path.join(self.tmp, "export.bib"), "w") as f:
            f.write("@book{eisenstein1980, title={The Printing Press}}\n")
        proj = os.path.join(self.tmp, "proj")

        def effect(script, args):
            os.makedirs(os.path.join(proj, "sources"), exist_ok=True)
            with open(os.path.join(proj, "my_text.md"), "w") as f:
                f.write("Print caused it. [[eisenstein1980]]\n")
            with open(os.path.join(proj, "my_text.md.refs.txt"), "w") as f:
                f.write("eisenstein1980 = e.txt\n")
            with open(os.path.join(proj, "sources", "e.txt"), "w") as f:
                f.write("source text")
            return 0

        calls, run = self._runner(effect)
        argv = run_quiet(scripted(
            exp,     # text file (the [@key] export)
            "y",     # convert it now?
            "",      # bib -> default sibling export.bib
            proj,    # project folder
            "",      # sources -> default proj/sources
            # refs sibling auto-detected; sources check: all present, no question
            "",      # output folder default
            "1", "", "", "", "n",
        ), run)
        self.assertEqual(calls[0][0], "import_claude_research.py")
        self.assertIn("--output-dir", calls[0][1])
        self.assertEqual(argv[argv.index("--text") + 1],
                         os.path.join(proj, "my_text.md"))

    def test_no_markers_declined_aborts(self):
        plain = os.path.join(self.tmp, "plain.md")
        with open(plain, "w") as f:
            f.write("No citations of any kind here.\n")
        with self.assertRaises(SystemExit):
            run_quiet(scripted(plain, "n"))    # continue anyway? -> no

    def test_no_markers_confirmed_continues(self):
        plain = os.path.join(self.tmp, "plain.md")
        with open(plain, "w") as f:
            f.write("No citations of any kind here.\n")
        argv = run_quiet(scripted(
            plain,
            "y",        # continue despite zero [[key]] markers
            self.src,   # sources
            "",         # refs question (no sibling) -> skip
            "y",        # no references mapping found -> continue anyway
            "out", "1", "", "", "", "n",
        ))
        self.assertEqual(argv[argv.index("--text") + 1], plain)

    def test_missing_sources_download_offer(self):
        proj = os.path.join(self.tmp, "proj2")
        src = os.path.join(proj, "sources")
        os.makedirs(src)
        text = os.path.join(proj, "my_text.md")
        with open(text, "w") as f:
            f.write("A claim. [[a]]\n")
        with open(text + ".refs.txt", "w") as f:
            f.write("a = a.pdf\n")
        with open(os.path.join(proj, "sources_manifest.json"), "w") as f:
            f.write('{"sources": [{"key": "a", "title": "A paper"}]}')

        def effect(script, args):
            with open(os.path.join(src, "a.txt"), "w") as f:  # saved as page text
                f.write("downloaded source text")
            return 0

        calls, run = self._runner(effect)
        argv = run_quiet(scripted(
            text,
            "",      # sources -> default proj/sources
            # refs sibling auto-detected; check finds a missing + a manifest
            "y",     # download the missing sources now?
            # re-check: a.txt present -> no more questions
            "", "1", "", "", "", "n",
        ), run)
        self.assertEqual(calls[0][0], "download_sources.py")
        self.assertIn("--manifest", calls[0][1])
        self.assertEqual(argv[argv.index("--sources") + 1], src)

    def test_missing_list_shows_title_and_link(self):
        proj = os.path.join(self.tmp, "proj4")
        os.makedirs(os.path.join(proj, "sources"))
        text = os.path.join(proj, "my_text.md")
        with open(text, "w") as f:
            f.write("A claim. [[iyigun2008]]\n")
        with open(text + ".refs.txt", "w") as f:
            f.write("iyigun2008 = iyigun2008.pdf\n")
        with open(os.path.join(proj, "sources_manifest.json"), "w") as f:
            f.write('{"sources": [{"key": "iyigun2008", "title": "Luther and Suleyman",'
                    ' "year": "2008", "doi": "10.1162/qjec.2008.123.4.1465"}]}')
        _, run = self._runner()
        out = StringIO()
        with redirect_stdout(out):
            wizard.run_wizard(input_fn=scripted(
                text, "",
                "n",     # decline the download offer
                "c",     # continue without the source
                "", "1", "", "", "", "n",
            ), run_script=run)
        printed = out.getvalue()
        self.assertIn("Luther and Suleyman", printed)
        self.assertIn("https://doi.org/10.1162/qjec.2008.123.4.1465", printed)

    def test_missing_sources_no_manifest_continue_or_abort(self):
        proj = os.path.join(self.tmp, "proj3")
        src = os.path.join(proj, "sources")
        os.makedirs(src)
        text = os.path.join(proj, "my_text.md")
        with open(text, "w") as f:
            f.write("A claim. [[b]]\n")
        with open(text + ".refs.txt", "w") as f:
            f.write("b = b.pdf\n")
        argv = run_quiet(scripted(
            text, "",
            "c",     # missing, no manifest -> continue without them
            "", "1", "", "", "", "n",
        ))
        self.assertEqual(argv[argv.index("--text") + 1], text)
        with self.assertRaises(SystemExit):
            run_quiet(scripted(text, "", "a"))   # same spot -> abort


if __name__ == "__main__":
    unittest.main()
