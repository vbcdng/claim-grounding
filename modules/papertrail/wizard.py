"""
Interactive terminal wizard for verify_my_text.py (ROADMAP item 4, owner-confirmed shape).

Starts automatically when verify_my_text.py is run with no arguments on a terminal.
Asks one question per option with a one-line explanation, validates paths as they
are typed, and offers the known-good models from docs/MODEL_OPTIONS.md as named
choices (ROADMAP item 5) with a raw-override escape hatch. It returns an argv list
that main() feeds through the normal argparse path — the CLI stays the single entry
point and the wizard shares the existing cost-estimate + confirmation flow instead
of duplicating it. It also prints the equivalent one-line command, so the wizard
teaches the flags rather than hiding them.

Since 2026-07-12 the wizard walks the WHOLE pipeline, not just the verify flags
(owner terminal test: a raw [@key] Claude-Science export + an empty sources folder
produced a meaningless 2-own-claim run with no warning):
- text step: pandoc [@key] citations with no [[key]] markers → offer to run
  import_claude_research.py and continue on the imported project; a text with no
  markers at all gets a warn-and-confirm instead of a silent all-"own" run.
- sources step: the refs map is checked against the sources folder; missing files
  plus a sources_manifest.json → offer to run download_sources.py, then a
  continue / re-check / abort loop pointing at download_report.md and the
  inbox/ingest path.
Pipeline scripts run as subprocesses of this interpreter (_run_script, injectable
in tests). No LLM/API calls in this module; the only network is the optional
download step, run only with explicit consent.
"""

import os
import re
import sys
import glob
import shlex
import shutil

from . import cost_estimator

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RECOMMENDED_MODEL = "gemini/gemini-2.5-flash-lite"
BIGGER_MODEL = "gemini/gemini-2.5-flash"
DEFAULT_CLAUDE_CODE_ALIAS = "haiku"        # the Stream-E dev backend's tested default
DEFAULT_OLLAMA_TAG = "gemma4:26b"          # ranked #1 for this box in docs/MODEL_OPTIONS.md
DEFAULT_OLLAMA_URL = "http://localhost:11434"


# ---------- low-level prompting ----------

def _read(prompt: str, input_fn) -> str:
    try:
        return input_fn(prompt)
    except (EOFError, KeyboardInterrupt):
        print("\nAborted — nothing was run, nothing was spent.")
        raise SystemExit(1)


def _ask(question, default=None, validate=None, input_fn=input) -> str:
    """One prompt; Enter accepts the default; validate returns an error string
    to re-ask, or None to accept. default='' means 'optional, Enter to skip'."""
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        raw = _read(f"{question}{suffix}: ", input_fn).strip()
        value = raw or (default if default is not None else "")
        err = validate(value) if validate else None
        if err:
            print(f"  ! {err}")
            continue
        return value


def _ask_yn(question, default_yes=True, input_fn=input) -> bool:
    while True:
        raw = _read(f"{question} [{'Y/n' if default_yes else 'y/N'}] ", input_fn).strip().lower()
        if not raw:
            return default_yes
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  ! answer y or n")


def _enable_path_completion():
    """Tab-completes filesystem paths in the prompts, when readline is available."""
    try:
        import readline

        def complete(text, state):
            matches = [m + ("/" if os.path.isdir(m) else "")
                       for m in glob.glob(os.path.expanduser(text) + "*")]
            return matches[state] if state < len(matches) else None

        readline.set_completer_delims(" \t\n;")
        readline.set_completer(complete)
        readline.parse_and_bind("tab: complete")
    except Exception:
        pass


# ---------- validators ----------

def _file_exists(p):
    if not p:
        return "required — type a file path"
    if not os.path.isfile(p):
        return f"file not found: {p}"


def _dir_exists(p):
    if not p:
        return "required — type a folder path"
    if not os.path.isdir(p):
        return f"folder not found: {p}"


def _optional_file(p):
    if p and not os.path.isfile(p):
        return f"file not found: {p}"


def _pos_int(v):
    if not v.isdigit() or int(v) < 1:
        return "enter a whole number >= 1"


# ---------- pipeline helpers ----------

_PANDOC_CITE_RE = re.compile(r"\[@[A-Za-z0-9_]")     # [@key] / [@a; @b] pandoc citations
_MARKER_RE = re.compile(r"\[\[[^\[\]]+\]\]")         # this tool's [[key]] markers


def _slug(path):
    return re.sub(r"[^A-Za-z0-9_-]+", "_",
                  os.path.splitext(os.path.basename(path))[0]).strip("_").lower()


def _read_text_file(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return ""


def _run_script(script, args):
    """Run one pipeline script (importer/downloader) with this interpreter,
    streaming its output; returns the exit code. Injectable in tests."""
    import subprocess
    cmd = [sys.executable, os.path.join(PROJECT_ROOT, script)] + list(args)
    print("  $ " + " ".join(shlex.quote(c) for c in cmd) + "\n")
    try:
        return subprocess.call(cmd)
    except OSError as e:
        print(f"  ! could not run {script}: {e}")
        return 1


def _maybe_import_step(text, input_fn, run_script):
    """Pandoc [@key] citations and no [[key]] markers → this is a Claude Science
    export; offer to convert it. Returns the text path to continue with."""
    content = _read_text_file(text)
    if _MARKER_RE.search(content) or not _PANDOC_CITE_RE.search(content):
        return text
    n = len(_PANDOC_CITE_RE.findall(content))
    print(f"\n  ! This text cites with pandoc-style [@key] markers ({n} found) — a Claude\n"
          "    Science export. The verifier only reads [[key]] markers, so as-is NOTHING\n"
          "    would be checked. import_claude_research.py converts it (free, offline) into\n"
          "    [[key]] markers + a refs file + a download manifest for the cited papers.")
    if not _ask_yn("  Convert it now?", default_yes=True, input_fn=input_fn):
        return text
    default_bib = os.path.splitext(text)[0] + ".bib"
    bib = _ask("Bibliography (.bib) of the export",
               default=default_bib if os.path.isfile(default_bib) else None,
               validate=_file_exists, input_fn=input_fn)
    proj = _ask("Project folder (created; gets my_text.md + refs + sources/)",
                default=os.path.join("data", f"{_slug(text)}_project"), input_fn=input_fn)
    rc = run_script("import_claude_research.py",
                    ["--input", text, "--bib", bib, "--output-dir", proj])
    new_text = os.path.join(proj, "my_text.md")
    if rc != 0 or not os.path.isfile(new_text):
        print(f"  ! import did not produce {new_text} — continuing with the original file")
        return text
    print(f"\n  Imported. Continuing with {new_text} — the next step fetches the cited papers.")
    return new_text


def _marker_guard(text, input_fn):
    """A text with zero [[key]] markers verifies nothing (every sentence becomes an
    uncited 'own' claim) — say so and confirm before letting the run happen."""
    if _MARKER_RE.search(_read_text_file(text)):
        return
    print("\n  ! No [[key]] citation markers found in this text. Without markers NOTHING\n"
          "    is verified — the whole text becomes uncited 'own' claims.\n"
          "    Your own draft -> add [[key]] markers (see INPUT_FORMAT.md).\n"
          "    A published paper (PDF/DOI/arXiv) -> import_paper.py.\n"
          "    A Claude Science export ([@key] + .bib) -> import_claude_research.py.")
    if not _ask_yn("  Continue anyway?", default_yes=False, input_fn=input_fn):
        print("Aborted — nothing was run, nothing was spent.")
        raise SystemExit(1)


def _sources_status(text, refs_path, sources):
    """(refs_map, missing_keys): keys whose file is absent from sources/ (the
    refs-named file, or <key>.pdf/.txt/.md as the downloader may have saved it)."""
    from . import text_decomposer
    refs_map, _ = text_decomposer.parse_references(
        _read_text_file(text), refs_path=refs_path or None, text_path=text)
    missing = []
    for key, fname in refs_map.items():
        candidates = [fname, key + ".pdf", key + ".txt", key + ".md"]
        if not any(os.path.isfile(os.path.join(sources, c))
                   and os.path.getsize(os.path.join(sources, c)) > 0
                   for c in candidates):
            missing.append(key)
    return refs_map, missing


def _manifest_entries(manifest):
    """key -> manifest entry (title/doi/url/year), {} when unreadable/absent."""
    if not manifest:
        return {}
    try:
        import json
        with open(manifest, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {e["key"]: e for e in data.get("sources", []) if e.get("key")}
    except Exception:
        return {}


def _print_missing(missing, entries):
    """One line per missing source WITH title/year + DOI/link when the manifest
    knows them — so the user sees what paper to hunt without opening the .bib."""
    print("  Missing:")
    for key in missing[:8]:
        e = entries.get(key, {})
        title = e.get("title") or ""
        year = f" ({e['year']})" if e.get("year") else ""
        link = e.get("url") or (f"https://doi.org/{e['doi']}" if e.get("doi") else "")
        print(f"    {key}" + (f" — {title}{year}" if title else ""))
        if link:
            print(f"      {link}")
    if len(missing) > 8:
        print(f"    … +{len(missing) - 8} more (full list: download_report.md)")


def _sources_pipeline_step(text, refs_path, sources, input_fn, run_script):
    """Check every cited source is on disk; offer the downloader for the gaps,
    then continue / re-check / abort."""
    manifest = None
    for d in (os.path.dirname(os.path.abspath(text)),
              os.path.dirname(os.path.abspath(sources))):
        p = os.path.join(d, "sources_manifest.json")
        if os.path.isfile(p):
            manifest = p
            break
    entries = _manifest_entries(manifest)
    download_offered = False
    while True:
        refs_map, missing = _sources_status(text, refs_path, sources)
        if not refs_map:
            print("\n  ! No references mapping found (no refs file and no [References] block) —\n"
                  "    the [[key]] markers can't be matched to source files, so every cited\n"
                  "    claim would come out 'missing source file'.")
            if not _ask_yn("  Continue anyway?", default_yes=False, input_fn=input_fn):
                print("Aborted — nothing was run, nothing was spent.")
                raise SystemExit(1)
            return
        if not missing:
            print(f"\n  Sources check: all {len(refs_map)} cited source file(s) present.")
            return
        print(f"\n  Sources check: {len(refs_map) - len(missing)}/{len(refs_map)} cited "
              f"source file(s) present.")
        _print_missing(missing, entries)
        if manifest and not download_offered:
            download_offered = True
            print(f"  A download manifest exists ({manifest}).\n"
                  "  download_sources.py fetches open-access copies (network, $0 API) and\n"
                  "  writes download_report.md with a link for everything it can't get.")
            if _ask_yn("  Download the missing sources now?", default_yes=True,
                       input_fn=input_fn):
                run_script("download_sources.py",
                           ["--manifest", manifest, "--sources-dir", sources])
                report = os.path.join(os.path.dirname(manifest), "download_report.md")
                if os.path.isfile(report):
                    print(f"  Per-source status report: {report}")
                continue    # re-check what landed
        print(f"  To add the rest by hand: save each file into {sources} under the name in\n"
              "  the refs file — or drop them in <project>/inbox/ and run ingest_downloads.py.\n"
              "  A claim citing a missing source is judged 'missing source file', never guessed.")
        choice = _ask("  [c]ontinue without them / [r]e-check the folder / [a]bort",
                      default="c",
                      validate=lambda v: None if v in ("c", "r", "a") else "answer c, r or a",
                      input_fn=input_fn)
        if choice == "c":
            return
        if choice == "a":
            print("Aborted — nothing was run, nothing was spent.")
            raise SystemExit(1)
        # "r" → loop and re-check


# ---------- steps ----------

def _model_step(input_fn):
    """Named model choices (ROADMAP item 5) + raw litellm override. Prices shown
    from docs/MODEL_OPTIONS.md via the estimator's parser — one source of truth."""
    prices = cost_estimator.load_pricing()

    def price(m):
        p = prices.get(m)
        return f" (${p['input']:.2f}/M in, ${p['output']:.2f}/M out)" if p else ""

    print("\nModel — does the reading and judging. Embeddings always stay local (free).")
    print(f"  1) {RECOMMENDED_MODEL} — recommended: cheapest tested, "
          f"benchmarked on this tool{price(RECOMMENDED_MODEL)}")
    print(f"  2) {BIGGER_MODEL} — bigger Gemini; thinking model, "
          f"much pricier output{price(BIGGER_MODEL)}")
    print("  3) claude-code — $0 through your Claude Code login (haiku or sonnet); "
          "no API key, slower")
    print("  4) local model via Ollama — free, runs on this machine (Ollama must be running)")
    print("  5) other — any litellm 'provider/model' string")
    while True:
        choice = _ask("Choice", default="1",
                      validate=lambda v: None if v in ("1", "2", "3", "4", "5") else "pick 1-5",
                      input_fn=input_fn)
        if choice == "1":
            return RECOMMENDED_MODEL, None
        if choice == "2":
            return BIGGER_MODEL, None
        if choice == "3":
            if not shutil.which("claude"):
                print("  ! the `claude` CLI is not on PATH — option 3 needs Claude Code\n"
                      "    installed and logged in (run `claude` once). Pick another option.")
                continue
            alias = _ask("Claude Code model — haiku (fast, the tested default) or "
                         "sonnet (stronger, slower)",
                         default=DEFAULT_CLAUDE_CODE_ALIAS,
                         validate=lambda v: None if v in ("haiku", "sonnet")
                         else "haiku or sonnet",
                         input_fn=input_fn)
            return f"claude-code/{alias}", None
        if choice == "4":
            tag = _ask("Ollama model tag", default=DEFAULT_OLLAMA_TAG, input_fn=input_fn)
            base = _ask("Ollama URL", default=DEFAULT_OLLAMA_URL, input_fn=input_fn)
            return f"ollama/{tag}", base
        raw = _ask("litellm model string (provider/model)",
                   validate=lambda v: None if "/" in v
                   else "format is provider/model, e.g. openai/gpt-4o-mini",
                   input_fn=input_fn)
        return raw, None


def _api_key_step(model, input_fn):
    """Returns an --api-key value or None (None = let LLMClient use its own
    fallbacks: the project Gemini key file, or the provider's env var)."""
    provider = model.split("/", 1)[0]
    if provider == "ollama":
        return None                     # local — no key concept
    if provider == "claude-code":
        print("\nAPI key: none needed — calls go through your Claude Code login ($0).")
        return None
    fname = "google_api_key.txt" if provider == "gemini" else f"{provider}_api_key.txt"
    keyfile = os.path.join(PROJECT_ROOT, "config", fname)
    if os.path.exists(keyfile):
        print(f"\nAPI key: using the project key file {keyfile}")
        # gemini: LLMClient falls back to this file by itself; others need the flag.
        return None if provider == "gemini" else keyfile
    env = f"{provider.upper()}_API_KEY"
    print(f"\nAPI key for {provider}: paste the key or a path to a file holding it.\n"
          f"Press Enter to rely on the {env} environment variable instead.")
    return _ask("API key", default="", input_fn=input_fn) or None


def _arbiter_note(model):
    """The arbiter is ON by default but silently self-disables without a DeepSeek
    key — say up front which way THIS run will go (print-only, no question)."""
    print("\nArbiter — a second model that re-checks every flagged claim (default on).")
    if model.startswith("claude-code/"):
        print("  Follows the claude-code backend: claude-code/sonnet, $0 — nothing to set up.")
        return
    if os.environ.get("DEEPSEEK_API_KEY") or \
            os.path.isfile(os.path.join(PROJECT_ROOT, "config", "deepseek_api_key.txt")):
        print("  DeepSeek key found — it will run (deepseek/deepseek-v4-flash, ~a cent per run).")
        return
    print("  No DeepSeek key found, so it will be SKIPPED (one warning, never an error).\n"
          "  To enable it: put a key from platform.deepseek.com into\n"
          "  config/deepseek_api_key.txt (or export DEEPSEEK_API_KEY) and re-run.")


# ---------- the wizard ----------

def run_wizard(input_fn=input, run_script=_run_script) -> list:
    """Walk the pipeline interactively; return the equivalent verify argv list."""
    _enable_path_completion()
    print("verify_my_text — interactive setup. Enter accepts the [default]; Ctrl+C aborts.")
    print("Walks the whole pipeline: text -> (convert) -> sources -> (download) -> model -> run.")
    print("Nothing is spent here: the cost estimate and a confirmation come before the run.")

    argv = []

    print("\nStep 1/5 — your text: the article/draft to verify, with [[key]] citation markers.")
    text = _ask("Text file (.md/.txt)", validate=_file_exists, input_fn=input_fn)
    text = _maybe_import_step(text, input_fn, run_script)
    _marker_guard(text, input_fn)
    argv += ["--text", text]

    print("\nStep 2/5 — sources folder: the cited documents, named as in the references file.")
    default_sources = os.path.join(os.path.dirname(text) or ".", "sources")
    sources = _ask("Sources folder",
                   default=default_sources if os.path.isdir(default_sources) else None,
                   validate=_dir_exists, input_fn=input_fn)
    argv += ["--sources", sources]

    auto_refs = text + ".refs.txt"
    refs = None
    if os.path.isfile(auto_refs):
        print(f"\nReferences: found {auto_refs} — used automatically.")
    else:
        print("\nReferences file: maps [[key]] markers to filenames, one 'key = filename' "
              "per line.\nPress Enter if your text ends with a [References] block instead.")
        refs = _ask("References file", default="", validate=_optional_file, input_fn=input_fn)
        if refs:
            argv += ["--references", refs]

    _sources_pipeline_step(text, refs, sources, input_fn, run_script)

    stem = _slug(text)
    if stem == "my_text":                      # imported project — name by its folder
        stem = _slug(os.path.dirname(os.path.abspath(text))) or stem
    print("\nStep 3/5 — output folder: gets viewer.html, analysis.json and the caches. "
          "Re-using the\nsame folder re-uses the decomposition/embedding caches — re-runs "
          "become fast and nearly free.")
    out = _ask("Output folder", default=os.path.join("data", f"{stem}_verification"),
               input_fn=input_fn)
    argv += ["--output-dir", out]

    print("\nStep 4/5 — model & API key.")
    model, api_base = _model_step(input_fn)
    argv += ["--model", model]
    if api_base:
        argv += ["--api-base", api_base]

    key = _api_key_step(model, input_fn)
    if key:
        argv += ["--api-key", key]
    _arbiter_note(model)

    print("\nStep 5/5 — run options.")
    print("Parallel LLM calls: 4 is a good default; lower it if you hit rate limits, "
          "1 = sequential.")
    conc = _ask("Concurrency", default="4", validate=_pos_int, input_fn=input_fn)
    if conc != "4":
        argv += ["--concurrency", conc]

    cost_note = ("$0 through your Claude Code login, but slower"
                 if model.startswith("claude-code/") else "~a cent extra")
    print("\nSecond opinion: a second model re-reads the evidence behind every verdict\n"
          f"and flags disagreements — it never changes a verdict ({cost_note}).")
    if _ask_yn("Add a second opinion?", default_yes=False, input_fn=input_fn):
        argv += ["--second-opinion"]

    if _ask_yn("\nOpen the result in your browser when done?", default_yes=True,
               input_fn=input_fn):
        argv += ["--open"]

    print("\nEquivalent command (to skip the wizard next time):\n  "
          + "python3 verify_my_text.py " + " ".join(shlex.quote(a) for a in argv) + "\n")
    return argv
