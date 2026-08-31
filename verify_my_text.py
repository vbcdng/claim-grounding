#!/usr/bin/env python3
"""
verify_my_text.py — PaperTrail-style claim grounding for your own writing.

Takes a text you wrote (with [[key]] citation markers) plus a folder of the source
documents it cites, and produces an interactive HTML review showing, for every
claim, the verbatim supporting sentence from the cited source — and flagging
unsupported claims and source claims your text omitted.

Usage:
  python3 verify_my_text.py --text my_writing.md --sources sources/ \\
    [--references my_writing.md.refs.txt] [--output-dir data/my_text_verification/] \\
    [--api-key config/google_api_key.txt] [--model gemini/gemma-4-31b-it] \\
    [--api-base URL] [--open]

LLM provider: any litellm-supported model via --model "provider/model", e.g.
  gemini/gemma-4-31b-it (default, free Google tier)   openai/gpt-4o-mini   openrouter/<model>...
  ollama/llama3 (with --api-base http://localhost:11434)   openrouter/<model>
$0 option: --backend claude-code (or --model claude-code/haiku) sends every call to the
local `claude` CLI headlessly — no API key, runs on a Claude subscription (dev runs).
Auth: --api-key takes a file path OR a raw key; or set the provider env var
(OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, ...). Embeddings stay local (SPECTER).

Markers: write [[key]] after a sentence; map keys to filenames in a references file
(default <text>.refs.txt, or a trailing [References] block), one per line:  key = filename
"""

import os
import sys
import json
import time
import shutil
import hashlib
import logging
import argparse
import datetime
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.papertrail import (text_decomposer, source_decomposer, matcher, viewer,
                                checkpoint,
                                cost_estimator, rerun, second_opinion, own_claims, arbiter,
                                citation_scope,
                                argument_map, crux, evidence_independence,
                                provenance_export, llm_client, prompt_store,
                                deep_check_store)
from modules.papertrail.llm_client import LLMClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("verify_my_text")

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


def paper_id_for(filename: str) -> str:
    return hashlib.sha1(filename.encode("utf-8")).hexdigest()


def apply_backend(args) -> bool:
    """Normalize the two claude-code spellings (--backend claude-code / --model
    claude-code[/alias]) and install the Haiku-tuned combined-judgment rubric.
    Returns True when this run uses the $0 local-CLI backend. Idempotent."""
    wants_cc = (getattr(args, "backend", "api") == "claude-code"
                or str(args.model or "").startswith("claude-code"))
    if not wants_cc:
        return False
    from modules.papertrail.claude_code_backend import (
        canonical_model, RECOMMENDED_MAX_CONCURRENCY)
    m = str(args.model or "")
    if m and not m.startswith("claude-code") and "/" in m:
        logger.warning(f"--backend claude-code ignores provider model '{m}' — using "
                       "claude-code/haiku (pass --model claude-code/<alias> or a bare "
                       "alias like 'sonnet' to pick the CLI model)")
        m = ""
    args.model = canonical_model(m or None)
    args.backend = "claude-code"
    # The $0 CLI shares one subscription across the whole fan-out; a high
    # --concurrency trips a rate/concurrency ceiling that silently mislabels
    # claims unsupported (walkthrough #1 / P2.1). Clamp to a safe ceiling and say so.
    if getattr(args, "concurrency", 0) and args.concurrency > RECOMMENDED_MAX_CONCURRENCY:
        logger.warning(f"--concurrency {args.concurrency} is too high for the claude-code "
                       f"backend (shared subscription) — clamping to "
                       f"{RECOMMENDED_MAX_CONCURRENCY} to avoid rate-limit corruption. "
                       f"Use the Gemini default backend for large, fast runs.")
        args.concurrency = RECOMMENDED_MAX_CONCURRENCY
    if getattr(args, "second_opinion", None) == second_opinion.DEFAULT_MODEL:
        # Bare --second-opinion on the $0 backend must not silently spend on the
        # paid Gemini default; claude-code/sonnet keeps the "genuinely different
        # model" property at $0. (An explicit provider model stays as given.)
        args.second_opinion = canonical_model("sonnet")
        logger.info(f"--second-opinion follows the claude-code backend: "
                    f"{args.second_opinion} ($0)")
    if getattr(args, "arbiter", None) == arbiter.DEFAULT_MODEL:
        # Bare --arbiter on the $0 backend follows it too (same rule as
        # --second-opinion): claude-code/sonnet reads big contexts fine at $0.
        args.arbiter = canonical_model("sonnet")
        logger.info(f"--arbiter follows the claude-code backend: {args.arbiter} ($0)")
    haiku_prompt = os.path.join(REPO_ROOT, "benchmarks",
                                "pt_combined_judgment_haiku_v1.txt")
    if os.path.isfile(haiku_prompt):
        matcher.PROMPT_OVERRIDES["pt_combined_judgment_prompt.txt"] = haiku_prompt
    else:
        # A trimmed copy of the repo may lack benchmarks/ — degrade to the
        # production prompt instead of crashing mid-run (first-tester report,
        # 2026-07-20).
        logger.warning("Haiku-tuned judging rubric not found (%s) — "
                       "using the production judging prompt.", haiku_prompt)
    return True


def file_sha1(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def run_fix_claim(args):
    """--fix-claim <id>: rewrite one claim from a FINISHED run's cached data
    (analysis.json + source_claims/ + embeddings/ in --output-dir), verify the
    rewrite, and refresh viewer.html. No full re-run."""
    from modules.papertrail import claim_fixer

    analysis_path = os.path.join(args.output_dir, "analysis.json")
    if not os.path.exists(analysis_path):
        logger.error(f"No finished run found: {analysis_path} is missing "
                     "(--fix-claim works on an existing --output-dir)"); sys.exit(1)
    with open(analysis_path, "r", encoding="utf-8") as f:
        analysis = json.load(f)

    cache_dir = os.path.join(args.output_dir, "source_claims")
    sources = {}
    for s in analysis.get("sources", []):
        p = os.path.join(cache_dir, f"{s['paper_id']}.json")
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                sources[s["paper_id"]] = json.load(f)
            if s.get("title"):     # the run's title (manifest-enriched) beats the stem
                sources[s["paper_id"]]["title"] = s["title"]

    # Same model as the run unless overridden — a different judge would make the
    # verification incomparable with the run's verdicts.
    llm_client.set_call_log(os.path.join(args.output_dir, "llm_calls.jsonl"))
    args.model = args.model or analysis.get("metadata", {}).get("model")
    apply_backend(args)      # a claude-code run's metadata routes the fix there too
    llm = LLMClient(model=args.model, api_key=args.api_key, api_base=args.api_base)
    try:
        sug = claim_fixer.fix_claim(analysis, sources, llm, args.fix_claim,
                                    emb_cache_dir=os.path.join(args.output_dir, "embeddings"))
    except (ValueError, RuntimeError) as e:
        logger.error(str(e)); sys.exit(1)

    with open(analysis_path, "w", encoding="utf-8") as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)

    # Refresh the viewer (embedded text sources come from the run's own copies).
    source_texts = {}
    for s in analysis.get("sources", []):
        fn = s.get("filename")
        if fn and not fn.lower().endswith(".pdf"):
            path = os.path.join(args.output_dir, "sources", fn)
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    source_texts[s["paper_id"]] = f.read()
    from modules.papertrail import viewer as viewer_mod
    text_file = analysis.get("metadata", {}).get("text_file", "")
    viewer_path = os.path.join(args.output_dir, "viewer.html")
    # A run made with --argument-map has its assessment JSONs on disk — reload
    # them so refreshing the viewer doesn't silently drop the panel the user
    # already paid for.
    assessment = {}
    for akey, fn in (("argument_map", "argument_map.json"),
                     ("independence", "independence.json"),
                     ("crux", "crux.json")):
        p = os.path.join(args.output_dir, fn)
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    assessment[akey] = json.load(f)
            except Exception as e:
                logger.warning(f"Could not reload {fn} for the viewer: {e}")
    viewer_mod.generate(analysis, viewer_path,
                        title=f"Verification — {os.path.basename(text_file)}",
                        source_texts=source_texts,
                        assessment=assessment or None)
    from modules.papertrail import viewer_v2
    viewer_v2.generate(analysis, os.path.join(args.output_dir, "viewer_v2.html"),
                       title=f"Verification — {os.path.basename(text_file)}",
                       source_texts=source_texts, assessment=assessment or None)

    ok = "✓ verified: supported by the sources" if sug["verified_supported"] \
        else "⚠ re-check inconclusive — review manually"
    print(f"\nSuggested fix for {args.fix_claim} ({ok}):\n\n  {sug['text']}\n")
    if sug.get("changes"):
        print(f"  What changed: {sug['changes']}\n")
    print(f"The suggestion is now in the viewer too:\n  {os.path.abspath(viewer_path)}")
    if args.open:
        webbrowser.open("file://" + os.path.abspath(viewer_path))


def main():
    ap = argparse.ArgumentParser(description="Verify your writing against its cited sources (PaperTrail-style).",
                                 epilog="Run with no arguments for an interactive setup wizard.")
    ap.add_argument("--text", help="Path to your text (.txt/.md) with [[key]] markers")
    ap.add_argument("--sources", help="Folder containing the cited source documents")
    ap.add_argument("--references", help="References mapping file (default: <text>.refs.txt or [References] block)")
    ap.add_argument("--output-dir", default="data/my_text_verification", help="Output directory")
    ap.add_argument("--api-key", help="API key: a file path or a raw key value (else uses provider env var)")
    ap.add_argument("--model", help="litellm model string 'provider/model' (default: gemini/<config model>), "
                                    "or 'claude-code/<alias>' for the $0 local-CLI backend")
    # --decompose / --decomp-model were RETIRED from the CLI on 2026-07-16 (owner:
    # the advisory decomposition layer needs a redesign before being offered again —
    # see IDEAS.md "source decomposition v2"). The machinery (source_decomposer,
    # caches, decomp_bench) is kept; runs simply never request claim extraction.
    ap.add_argument("--backend", choices=["api", "claude-code"], default="api",
                    help="'api' (default) = the provider API from --model via litellm. "
                         "'claude-code' = dispatch every LLM call headlessly to the local "
                         "`claude` CLI (default claude-code/haiku): $0 API spend on a Claude "
                         "subscription, higher per-call latency. Judging swaps in the "
                         "Haiku-tuned rubric (benchmarks/pt_combined_judgment_haiku_v1.txt; "
                         "validated 0-false-positive in docs/HAIKU_VS_GEMINI_JUDGE.md). "
                         "Meant for development/iteration runs — the ship-gate baseline "
                         "stays the Gemini API. Shortcut: --model claude-code/haiku.")
    ap.add_argument("--api-base", help="Base URL for local/OpenAI-compatible endpoints (e.g. Ollama)")
    ap.add_argument("--open", action="store_true", help="Open the viewer in a browser when done")
    ap.add_argument("--estimate", action="store_true",
                    help="Only print the predicted LLM call counts and cost range, then exit "
                         "(no API calls)")
    ap.add_argument("--yes", action="store_true",
                    help="Skip the cost confirmation prompt for runs estimated above "
                         f"${cost_estimator.CONFIRM_THRESHOLD_USD:.2f}")
    ap.add_argument("--fix-claim", metavar="ID",
                    help="Rewrite ONE claim (e.g. t6) so its cited sources support it, "
                         "using the finished run in --output-dir (no full re-run; a few "
                         "small LLM calls). Stores the verified suggestion in "
                         "analysis.json and refreshes viewer.html.")
    ap.add_argument("--full", action="store_true",
                    help="Re-judge every claim even when --output-dir holds a previous run. "
                         "By default unchanged claims (same text + citations, same model) "
                         "reuse their previous verdicts and cost nothing; use --full after "
                         "changing a source file's CONTENT or to get fresh verdicts.")
    ap.add_argument("--second-opinion", metavar="MODEL", nargs="?",
                    const=second_opinion.DEFAULT_MODEL, default=None,
                    help="After judging, have a SECOND model re-read the same evidence "
                         f"for every verdict (default: {second_opinion.DEFAULT_MODEL}; "
                         "same API key). Disagreements are flagged in the viewer — "
                         "never overriding a verdict. ~1 small call per judged claim; "
                         "catches both false positives and over-strict rejections.")
    ap.add_argument("--arbiter", metavar="MODEL", nargs="?",
                    const=arbiter.DEFAULT_MODEL, default=arbiter.DEFAULT_MODEL,
                    help="Escalate the flagged claims (unsupported, supported-with-gaps, "
                         "conflicting sentence) to a strong model that re-reads them WITH "
                         "large source context. DEFAULT ON since 2026-07-14 with "
                         f"{arbiter.DEFAULT_MODEL} (needs OPENROUTER_API_KEY or "
                         "config/openrouter_api_key.txt — skipped with a one-line note "
                         "if neither exists); pass a MODEL to override, --no-arbiter to "
                         "turn off. Findings render as chips with verbatim-verified "
                         "quotes — never overriding a verdict. ~1 large call per flagged "
                         "claim (docs/ARBITER_PLAN.md).")
    ap.add_argument("--no-arbiter", action="store_true",
                    help="Skip the arbiter tier (it is on by default): no flagged-claim "
                         "escalation, no amber resolution, no arbiter rescue.")
    ap.add_argument("--no-arbiter-rescue", action="store_true",
                    help="With --arbiter: keep the 'proof may exist' chip only, "
                         "without the rescue step (by default the arbiter's "
                         "verbatim-verified proof windows are re-judged by the "
                         "PRIMARY judge, and a unanimous positive flips a false "
                         "unsupported to supported, method=arbiter_rescue).")
    ap.add_argument("--no-citation-scope", action="store_true",
                    help="Skip classifying the citation scope of unsupported cited "
                         "claims. By default each gets one tiny LLM call deciding "
                         "whether the citation claims the WHOLE passage or is a "
                         "methods/concept/related-work pointer inside the authors' "
                         "own text; scoped cards are re-badged in the viewer "
                         "(indigo, never red) — the verdict itself never changes.")
    ap.add_argument("--no-own-split", action="store_true",
                    help="Skip classifying uncited (own) claims as structural / opinion / "
                         "fact. By default each own claim gets one tiny LLM call and "
                         "factual assertions without a citation are chipped "
                         "'citation needed?' in the viewer (pennies; tags are reused "
                         "on incremental runs).")
    # Back-compat no-op: --partial-check was the opt-in switch before it went
    # default-on (2026-07-05, after the streamC 3-round-ladder fix passed the
    # fresh 3-paper gate). Kept so old invocations still parse.
    ap.add_argument("--partial-check", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--no-partial-check", action="store_true",
                    help="Skip the partial-support check. By DEFAULT every cited claim "
                         "(single- AND multi-citation) judged supported is re-checked with "
                         "the component-complete combined judge over the UNION of cited "
                         "evidence (each source's lead/title/abstract sentences included; "
                         "on a negative, escalates to the source's cached decomposed "
                         "claims, then verifies each named-missing component alone via "
                         "full-text extraction before flagging). If a specific component "
                         "(a number, an attribution) is in none of the cited sources, it's "
                         "chipped 'partial support?' + lower confidence, and the project's "
                         "other sources are searched for the missing component; a cited "
                         "source the others already cover gets an 'over-cited?' chip. "
                         "Nudges, never a veto — the verdict stays supported. Passing this "
                         "flag turns the whole check off.")
    ap.add_argument("--concurrency", type=int, default=4,
                    help="Parallel LLM calls (default: 4). Chunk decomposition and per-claim "
                         "judgments run on a thread pool; lower it if you see many rate-limit "
                         "waits, raise it on a generous API tier. 1 = fully sequential. The "
                         "$0 --backend claude-code shares one subscription, so it is clamped "
                         "to 6 (a higher value trips a rate ceiling that mislabels claims).")
    ap.add_argument("--argument-map", action="store_true",
                    help="After judging, build the ARGUMENT STRUCTURE view: an argument map "
                         "(inference edges between your claims), crux ranking (claims the "
                         "argument leans hardest on), and evidence-independence check (cited "
                         "sources that aren't independent confirmations). Writes "
                         "argument_map.json / crux.json / independence.json and adds an "
                         "'Argument structure' panel to the viewer. Edge inference costs ~1 "
                         "call per candidate pair — free with --backend claude-code.")
    ap.add_argument("--provenance-export", action="store_true",
                    help="Also write provenance.json — a PROV-O-shaped export of every "
                         "verdict (claim / evidence+method / run-metadata). No LLM calls.")
    argv = sys.argv[1:]
    wizard_mode = False
    if not argv:
        if sys.stdin.isatty():
            # No flags on a terminal -> interactive wizard (ROADMAP item 4). It
            # returns the equivalent argv, so everything below runs exactly as a
            # flagged call.
            from modules.papertrail import wizard
            argv = wizard.run_wizard()
            wizard_mode = True
        else:
            # No flags AND no terminal: the wizard can't prompt. Say so plainly
            # rather than the misleading "run with no arguments" hint below.
            ap.error("no arguments given and no interactive terminal — the "
                     "wizard needs a TTY. Pass --text and --sources (see "
                     "--help), or run this in an interactive shell for the wizard.")
    args = ap.parse_args(argv)
    cc_backend = apply_backend(args)
    # Arbiter is DEFAULT ON (owner ruling 2026-07-14) on every backend — under
    # claude-code apply_backend already routed the default to claude-code/sonnet
    # ($0). Opt out with --no-arbiter. A missing provider key (OpenRouter for
    # the luna default, DeepSeek for that override) downgrades to a one-line
    # info note + skip, never an error or warning: the tier is additive and
    # the no-key path is a documented, nominally-correct run (judge F-7).
    arbiter_skipped_no_key = False
    if args.no_arbiter:
        args.arbiter = None
    elif args.arbiter and not arbiter.key_available(args.arbiter):
        logger.info(
            "Note: the optional arbiter re-check is off (no key found for "
            f"{args.arbiter}); the run works fine without it. To enable it, add "
            f"{arbiter.key_hint(args.arbiter)}, or pass --arbiter <other-model>; "
            "pass --no-arbiter to hide this note.")
        args.arbiter = None
        arbiter_skipped_no_key = True

    if args.fix_claim:
        return run_fix_claim(args)
    if not args.text or not args.sources:
        ap.error("--text and --sources are required (or run with no arguments for the "
                 "wizard, or use --fix-claim <id> with --output-dir)")
    if not os.path.exists(args.text):
        logger.error(f"Text file not found: {args.text}"); sys.exit(1)
    if not os.path.isdir(args.sources):
        logger.error(f"Sources folder not found: {args.sources}"); sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)
    # Per-call bookkeeping (task #20): every LLM call from here on appends one
    # JSONL line to <output-dir>/llm_calls.jsonl (purpose, tokens, cost, response;
    # prompt hash-only unless PAPERTRAIL_LOG_PROMPTS=1). Read-side: nothing on
    # the verdict path consumes this file.
    llm_client.set_call_log(os.path.join(args.output_dir, "llm_calls.jsonl"))
    cache_dir = os.path.join(args.output_dir, "source_claims")
    sources_out = os.path.join(args.output_dir, "sources")
    os.makedirs(sources_out, exist_ok=True)
    start = time.time()

    # Freeze every instruction file NOW (task #44): each prompt's text is read
    # once here and served from memory for the whole run, so an edit to
    # config/prompts/ while this run is in flight can no longer reach it. The
    # returned {name: fingerprint} map is compared against the previous run
    # below and written into metadata.prompts at the end.
    prompt_fingerprints = prompt_store.snapshot(matcher.PROMPT_OVERRIDES)

    with open(args.text, "r", encoding="utf-8") as f:
        raw_text = f.read()

    # Stage 2: parse references + decompose the user's text into claims.
    refs_map, body = text_decomposer.parse_references(raw_text, refs_path=args.references, text_path=args.text)
    text_claims = text_decomposer.extract_claims(body)

    # Content hashes of every cited source file: recorded in metadata so the NEXT
    # incremental run can tell a replaced source from an unchanged one (verdict
    # reuse against different file content would be silently stale).
    source_hashes = {}
    for tc in text_claims:
        for key in tc.get("markers", []):
            fn = refs_map.get(key)
            if fn and fn not in source_hashes:
                path = os.path.join(args.sources, fn)
                if os.path.exists(path):
                    source_hashes[fn] = file_sha1(path)

    # Incremental re-verification (the review loop): when this output dir already
    # holds a finished run with the SAME model, unchanged claims reuse their
    # previous verdicts — only edited/new claims are judged. --full disables.
    model_str = LLMClient._normalize_model(args.model)
    prev_analysis, reuse_map, prev_info = None, {}, {}
    analysis_path = os.path.join(args.output_dir, "analysis.json")
    if os.path.exists(analysis_path) and not args.full:
        try:
            with open(analysis_path, "r", encoding="utf-8") as f:
                prev_analysis = json.load(f)
        except Exception as e:
            logger.warning(f"Could not read the previous analysis.json ({e}) — doing a full run")
    if prev_analysis is not None:
        prev_model = prev_analysis.get("metadata", {}).get("model")
        # Instruction-file check (task #44): None = the previous run predates
        # prompt tracking (warned about in the reuse branch below); a non-empty
        # set = at least one prompt the previous run used has different content
        # now, so its verdicts were made under different instructions.
        changed_prompt_names = rerun.changed_prompts(
            prev_analysis.get("metadata", {}).get("prompts"), prompt_fingerprints)
        # Extractor check (task #71): source files are compared by BYTES, so a
        # changed PDF library is invisible to changed_source_files even though
        # every sentence read out of those files may have moved. A MISSING
        # source_reader means the run predates the 2026-08-31 swap and so used
        # PyPDF2 — known, not unknown — and is re-judged (author ruling, option
        # B). Projects with no PDF sources at all are exempt: no PDF was read,
        # so nothing about them can have changed.
        reader_now = source_decomposer.source_reader_id()
        prev_reader = prev_analysis.get("metadata", {}).get("source_reader")
        has_pdf_sources = any(str(f).lower().endswith(".pdf") for f in source_hashes)
        reader_changed = rerun.source_reader_changed(
            prev_reader, reader_now, has_pdf_sources)
        if not prev_model:
            # Runs made before model-in-metadata tracking have no model recorded.
            # The default model changed (flash -> flash-lite, 2026-07-04), so these
            # legacy runs are exactly the ones most likely to differ from this run's
            # model — reusing their verdicts could silently cross a model change.
            # Force a full re-run rather than guess they match.
            logger.warning("Previous run predates model tracking (metadata.model missing) — "
                           "can't confirm it used the same model, so doing a full re-run "
                           "(reused verdicts must not silently cross a model change)")
        elif prev_model != model_str:
            logger.info(f"Previous run used {prev_model}, this one uses {model_str} — "
                        "verdicts are not comparable, doing a full re-run")
        elif changed_prompt_names:
            logger.info(f"Instruction files changed since the previous run: "
                        f"{', '.join(sorted(changed_prompt_names))} — its verdicts "
                        f"were made under different instructions, so none are "
                        f"reused and the whole text is judged fresh")
        elif reader_changed:
            prev_desc = (prev_reader if prev_reader else
                         f"{rerun.PRE_TRACKING_READER} (not recorded, so it predates "
                         f"the 2026-08-31 reader swap and used that one)")
            logger.info(f"Previous run read PDFs with {prev_desc}, this one uses "
                        f"{reader_now} — the same PDF file yields different text, so "
                        f"its verdicts rest on text this run cannot reproduce; none "
                        f"are reused and the whole text is judged fresh "
                        f"(--full does the same thing explicitly)")
        else:
            if changed_prompt_names is None:
                logger.warning("Previous run predates prompt tracking "
                               "(metadata.prompts missing) — cannot prove it used "
                               "the same instruction files; reusing its verdicts "
                               "anyway (git history of config/prompts/ can confirm; "
                               "--full forces a re-judge)")
            changed_files = rerun.changed_source_files(
                prev_analysis.get("metadata", {}).get("source_hashes"), source_hashes)
            if changed_files is None:
                logger.info("Previous run predates source-content tracking — reuse "
                            "assumes source files are unchanged (--full if they aren't)")
                changed_files = set()
            elif changed_files:
                logger.info(f"Source content changed since the last run: "
                            f"{', '.join(sorted(changed_files))} — claims citing "
                            f"these files will be re-judged")
            matched = rerun.match_claims(prev_analysis.get("text_claims", []), text_claims)
            for tc in text_claims:
                m = matched.get(tc["id"]) or {}
                p = m.get("reuse")
                if p is not None:
                    src_changed = any(refs_map.get(k) in changed_files
                                      for k in tc.get("markers", []))
                    # A refs-file edit can repoint a [[key]] at a DIFFERENT
                    # already-present file — both files' content hashes are
                    # unchanged, but the previous verdict was judged against
                    # the old file. Compare the marker->file resolution itself.
                    cur_pids = {paper_id_for(fn)
                                for fn in (refs_map.get(k) for k in tc.get("markers", []))
                                if fn and os.path.exists(os.path.join(args.sources, fn))}
                    remapped = cur_pids != set(p.get("paper_ids") or [])
                    if rerun.reusable(p) and not src_changed and not remapped:
                        # Runs before the 2026-08-07 near-duplicate merge can
                        # carry the same missing fact twice (hedged + plain);
                        # clean the reused list so old caches heal on re-run.
                        if p.get("judge_missing_parts"):
                            p["judge_missing_parts"] = matcher._dedupe_missing_parts(
                                p["judge_missing_parts"])
                        reuse_map[tc["id"]] = p
                    elif src_changed or remapped:
                        # same text, different source content/mapping -> re-judged;
                        # surface it in the Changed filter with the previous verdict.
                        prev_info[tc["id"]] = {"text": p.get("text"),
                                               "verdict": p.get("verdict")}
                    # matched but legacy-uncited/missing-file: re-derived at zero
                    # cost, not "changed" ('own' claims reuse -> tag kept)
                else:
                    prev_info[tc["id"]] = m.get("prev")   # {"text","verdict"} or None (new)
            logger.info(f"Incremental: {len(reuse_map)} unchanged claims keep their previous "
                        f"verdicts; {len(text_claims) - len(reuse_map)} will be judged "
                        f"(--full forces a complete re-run)")

    # Save-as-you-go recovery (2026-08-10): an INTERRUPTED run journals every
    # judged claim to checkpoint.jsonl as it lands (see checkpoint.py); if that
    # journal matches this exact configuration (model + text + source content),
    # its claims re-enter through the same reuse guards as incremental runs —
    # including the never-reuse-an-outage-verdict rule. Applies even with
    # --full: --full distrusts a FINISHED previous run, while the journal is
    # this same run, interrupted mid-flight.
    ckpt_path = os.path.join(args.output_dir, "checkpoint.jsonl")
    text_sha1 = hashlib.sha1(body.encode("utf-8")).hexdigest()
    ck_claims = checkpoint.load(ckpt_path, model_str, text_sha1, source_hashes,
                                source_decomposer.source_reader_id())
    if ck_claims:
        ck_matched = rerun.match_claims(ck_claims, text_claims)
        n_ck = 0
        for tc in text_claims:
            if tc["id"] in reuse_map:
                continue
            p = (ck_matched.get(tc["id"]) or {}).get("reuse")
            if p is not None and rerun.reusable(p):
                reuse_map[tc["id"]] = p
                n_ck += 1
        if n_ck:
            logger.info(f"Recovered {n_ck} judged claim(s) from the interrupted "
                        f"previous run (checkpoint.jsonl) — they skip re-judging")

    # Pre-run cost estimate (no API calls). --estimate prints and exits; a real run
    # above the threshold asks for confirmation first (--yes skips). On incremental
    # runs the estimate covers only the claims that will actually be judged.
    est_claims = [tc for tc in text_claims if tc["id"] not in reuse_map]
    do_decompose = False    # CLI flag retired 2026-07-16 — see IDEAS.md "source decomposition v2"
    point = None
    if cc_backend:
        print(f"\nclaude-code backend: $0 API spend — every LLM call runs on your Claude "
              f"subscription through the local `claude` CLI (higher per-call latency than "
              f"a raw API). {len(est_claims)} claims to judge this run.\n")
    else:
        est = cost_estimator.estimate(est_claims, refs_map, args.sources, cache_dir,
                                      model_str, paper_id_for, decompose=do_decompose)
        print("\n" + cost_estimator.format_estimate(est) + "\n")
        if args.second_opinion:
            print(f"(--second-opinion adds ~1 small judgment call per judged claim "
                  f"with {args.second_opinion} — typically a cent or two)\n")
        if args.arbiter:
            n_judged_est = sum(1 for tc in est_claims if tc.get("markers"))
            wc_arb = cost_estimator.arbiter_worst_case(args.arbiter, n_judged_est)
            wc_s = (f"; worst case all {n_judged_est} flagged: ~${wc_arb:.2f}"
                    if wc_arb is not None else "")
            print(f"(--arbiter adds ~1 LARGE call per FLAGGED claim with {args.arbiter} "
                  f"— typically 30-60% of judged claims{wc_s})\n")
        n_own_est = (0 if args.no_own_split
                     else sum(1 for tc in est_claims if not tc.get("markers")))
        if n_own_est:
            print(f"(+ ~{n_own_est} tiny calls to classify uncited claims — "
                  f"structural / opinion / citation-needed; --no-own-split skips)\n")
        if not args.no_citation_scope:
            n_cs_est = sum(1 for tc in est_claims if tc.get("markers"))
            if n_cs_est:
                print(f"(+ citation-scope check: 1 tiny call per cited claim judged "
                      f"unsupported (up to {n_cs_est}) — flags methods/concept "
                      f"citations on the authors' own text; --no-citation-scope "
                      f"skips)\n")
        # The default-on partial-support check isn't in the numbers above: it
        # runs on every CITED claim that ends up JUDGED SUPPORTED (single- and
        # multi-citation since 2026-07-07), so its size isn't knowable
        # pre-run — say so instead of underestimating.
        n_partial_est = 0
        if not args.no_partial_check:
            n_partial_est = sum(1 for tc in est_claims if tc.get("markers"))
            n_partial_est += sum(1 for p in reuse_map.values()
                                 if p.get("verdict") == "supported"
                                 and not p.get("partial_checked")
                                 and p.get("markers"))
            if n_partial_est:
                print(f"(+ partial-support check on up to {n_partial_est} cited "
                      f"claim(s) if judged supported — a few extra judge calls each, "
                      f"NOT included in the estimate above; --no-partial-check skips)\n")
        # The covering-set display pass (always on): one small call per cited
        # claim that ends up judged supported, incl. reused claims whose cached
        # verdict predates the pass.
        n_cover = (sum(1 for tc in est_claims if tc.get("markers"))
                   + sum(1 for p in reuse_map.values()
                         if p.get("verdict") == "supported"
                         and not p.get("covering_checked")
                         and p.get("markers")))
        if n_cover:
            print(f"(+ evidence-coverage display pass on up to {n_cover} cited "
                  f"claim(s) if judged supported — TWO small calls each "
                  f"(covering + pick-verify audit), NOT "
                  f"included in the estimate above)\n")
        worst = cost_estimator.addon_worst_case(model_str, n_own=n_own_est,
                                                n_partial=n_partial_est,
                                                n_cover=n_cover)
        if worst is not None and (n_own_est or n_partial_est or n_cover):
            worst_s = f"~${worst:.2f}" if worst >= 0.01 else "under $0.01"
            print(f"(worst case if every cited claim is judged supported, the "
                  f"passes above add {worst_s} on top)\n")
        point = (est.get("usd") or {}).get("point")
    if reuse_map:
        print(f"(incremental: {len(reuse_map)} of {len(text_claims)} claims are unchanged "
              f"and reuse their previous verdicts — the estimate covers the "
              f"{len(est_claims)} to be judged)\n")
    if args.estimate:
        return
    if wizard_mode and not (point is not None
                            and point > cost_estimator.CONFIRM_THRESHOLD_USD):
        # Wizard runs get a final go/no-go after the estimate is on screen; a
        # cheap run defaults to proceed. Above the threshold the wizard falls
        # through to the SAME explicit-opt-in gate as flagged runs (default:
        # abort) — that guard exists for exactly this audience.
        answer = input("Proceed with the run? [Y/n] ").strip().lower()
        if answer in ("n", "no"):
            print("Aborted — nothing was spent.")
            return
    elif point is not None and point > cost_estimator.CONFIRM_THRESHOLD_USD and not args.yes:
        answer = input(f"Estimated cost exceeds ${cost_estimator.CONFIRM_THRESHOLD_USD:.2f}. "
                       "Proceed? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted — nothing was spent.")
            return

    # Real titles/authors from a sibling sources_manifest.json (written by the
    # importer) — the filename stem is a poor identity for judgment provenance
    # ("undp2025" vs the actual report title) and for the viewer's labels.
    manifest_meta = {}
    manifest_path = os.path.join(os.path.dirname(os.path.abspath(args.text)),
                                 "sources_manifest.json")
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest_meta = {s["key"]: s for s in json.load(f).get("sources", [])}
            logger.info(f"Loaded titles/authors for {len(manifest_meta)} sources from the manifest")
        except Exception as e:
            logger.warning(f"Could not read {manifest_path}: {e}")

    # Resolve markers -> source files -> paper_ids.
    llm = LLMClient(model=args.model, api_key=args.api_key, api_base=args.api_base)
    # Fail fast on a bad/missing API key: one tiny call BEFORE any expensive
    # work (the SPECTER encode of the sources can take minutes). Without this,
    # a typo'd key surfaces as a per-call error wall and a garbage run
    # (2026-07-14 clean-venv test). call() logs the provider's actual error.
    if llm.call("Reply with the single word: ok", max_output_tokens=128,
                purpose="sanity_check") is None:
        print(f"\nAPI check failed for model {llm.model}: a tiny test call returned "
              f"nothing (the provider's error is logged above — usually a missing or "
              f"invalid API key). Fix the key (--api-key <file-or-value>, or the "
              f"provider's env var, e.g. GEMINI_API_KEY / OPENROUTER_API_KEY / "
              f"ANTHROPIC_API_KEY) and re-run. Nothing else was spent.",
              file=sys.stderr)
        sys.exit(2)
    # Source decomposition is retired from the CLI (2026-07-16, owner — redesign
    # parked in IDEAS.md): verdicts and supporting sentences never used the
    # decomposed claims (0/90 measured). Runs build the sentence index only;
    # sources whose cache already holds claims keep them (advisory back-compat).
    logger.info("Source decomposition: off (sentence index only)")
    decomp_llm = llm
    sources = {}            # paper_id -> source-claims dict
    source_texts = {}       # paper_id -> full text (non-PDF sources, embedded in the viewer)
    marker_errors = []
    for tc in text_claims:
        pids = []
        for key in tc.get("markers", []):
            filename = refs_map.get(key)
            if not filename:
                marker_errors.append(f"claim {tc['id']}: marker [[{key}]] has no reference mapping")
                continue
            path = os.path.join(args.sources, filename)
            if not os.path.exists(path):
                marker_errors.append(f"marker [[{key}]] -> file not found: {filename}")
                tc.setdefault("missing_files", []).append(filename)
                # keep the key alongside so the viewer can label a "source file
                # missing" row on multi-citation claims (item 16, t14)
                tc.setdefault("missing_markers", []).append({"key": key, "filename": filename})
                continue
            pid = paper_id_for(filename)
            pids.append(pid)
            if pid not in sources:
                # Stage 1: index (and, with --decompose, decompose) the source (cached on disk).
                sources[pid] = source_decomposer.decompose_source(path, pid, key, cache_dir, decomp_llm,
                                                                  workers=args.concurrency,
                                                                  extract_claims=do_decompose)
                meta = manifest_meta.get(key) or {}
                if meta.get("title"):
                    sources[pid]["title"] = (meta["title"]
                                             + (f" — {meta['author']}" if meta.get("author") else ""))
                # Copy the source next to the viewer so both the live server and the
                # later file:// deep-links can reach it via a relative path.
                dst = os.path.join(sources_out, filename)
                if not os.path.exists(dst):
                    try:
                        shutil.copy2(path, dst)
                    except Exception as e:
                        logger.warning(f"Could not copy source {filename} into output: {e}")
                # For non-PDF (text) sources, embed the full text so the viewer can show
                # it inline with the supporting sentence highlighted (works in any mode).
                if not filename.lower().endswith(".pdf"):
                    try:
                        with open(path, "r", encoding="utf-8", errors="ignore") as sf:
                            source_texts[pid] = sf.read()
                    except Exception as e:
                        logger.warning(f"Could not read text source {filename}: {e}")
        tc["paper_ids"] = list(dict.fromkeys(pids))

    marker_errors = list(dict.fromkeys(marker_errors))  # a file cited by N claims -> one warning
    if marker_errors:
        for e in marker_errors:
            logger.warning(e)

    # Stage 3: match + verdict. Embeddings cache next to the source-claims cache —
    # SPECTER encoding of ~30k source claims/sentences dominated re-run wall time.
    ckpt_writer = checkpoint.Writer(ckpt_path, model_str, text_sha1, source_hashes,
                                    source_decomposer.source_reader_id())
    analysis = matcher.run(text_claims, sources, llm, workers=args.concurrency,
                           emb_cache_dir=os.path.join(args.output_dir, "embeddings"),
                           reuse=reuse_map, partial_check=not args.no_partial_check,
                           checkpoint=ckpt_writer)
    # Diff vs the previous run: changed/new claims get a "prev" record so the
    # viewer can flag them (✎, the Changed filter) and show what they replaced.
    if prev_analysis is not None and (reuse_map or prev_info):
        for c in analysis["text_claims"]:
            if c["id"] in prev_info:
                p = prev_info[c["id"]]
                c["prev"] = ({"changed": True, "text": p.get("text"),
                              "verdict": p.get("verdict")} if p else {"changed": True})
    # Reused claims can carry a second_opinion from a previous run; when this run
    # didn't ask for the pass, a stale disagreement chip would misreport the
    # current configuration — drop it.
    if not args.second_opinion:
        n_stale_so = sum(1 for c in analysis["text_claims"]
                         if c.pop("second_opinion", None) is not None)
        if n_stale_so:
            logger.info(f"Dropped {n_stale_so} second-opinion flag(s) carried from the "
                        f"previous run (--second-opinion not requested this run)")
    if not args.arbiter and not arbiter_skipped_no_key:
        # --no-arbiter: drop everything the tier ever wrote, including a
        # previous run's amber resolution (the badge must revert to amber).
        # A key-caused skip keeps previous results — the tier wasn't declined.
        n_stale_arb = 0
        for c in analysis["text_claims"]:
            if c.pop("arbiter", None) is not None:
                n_stale_arb += 1
            if c.get("proof_state") == "arbiter_resolved":
                c["proof_state"] = "partial"
                (c.get("covering") or {}).pop("arbiter_resolution", None)
        if n_stale_arb:
            logger.info(f"Dropped {n_stale_arb} arbiter result(s) carried from the "
                        f"previous run (--no-arbiter this run)")
    if args.no_citation_scope:
        n_stale_cs = sum(1 for c in analysis["text_claims"]
                         if c.pop("citation_scope", None) is not None)
        if n_stale_cs:
            logger.info(f"Dropped {n_stale_cs} citation-scope tag(s) carried from the "
                        f"previous run (--no-citation-scope this run)")

    # Author verdict rulings (verdict_feedback.json, written by /apply-review):
    # surfaced as "author disputed" chips; those claims skip the second opinion —
    # the owner's ruling outranks any model's.
    # Split the own verdict (owner-approved 2026-07-04): tag each uncited claim
    # structural / opinion / fact so factual assertions that escaped citation get
    # an amber "citation needed?" prompt in the viewer. A nudge, not a verdict.
    own_summary = None
    if not args.no_own_split:
        own_summary = own_claims.classify(analysis["text_claims"], llm,
                                          workers=args.concurrency)
        if own_summary["checked"] or own_summary["reused"]:
            k = own_summary["counts"]
            logger.info(f"Own-claim split: {own_summary['checked']} classified"
                        + (f", {own_summary['reused']} kept from the previous run"
                           if own_summary["reused"] else "")
                        + f" — {k['structural']} structural, {k['opinion']} opinion, "
                        + f"{k['fact']} citation-needed"
                        + (f" ({', '.join(own_summary['fact_ids'])})"
                           if own_summary["fact_ids"] else ""))

    feedback = second_opinion.load_feedback(args.output_dir)
    if feedback:
        n_disputed = second_opinion.annotate_feedback(analysis["text_claims"], feedback)
        if n_disputed:
            logger.info(f"verdict_feedback.json: {n_disputed} claim(s) carry an author dispute")
    # Citation-scope check (owner ask 2026-07-12, from the foi/regret real-paper
    # runs): is each unsupported cited claim's citation asserting the WHOLE
    # passage, or a methods/concept/related pointer inside the authors' own
    # text? Scoped cards re-badge indigo in the viewer. DISPLAY ONLY — the
    # verdict field never changes. Runs after feedback annotation so
    # author-ruled claims are skipped.
    cs_summary = None
    if not args.no_citation_scope:
        cs_summary = citation_scope.classify(analysis["text_claims"], llm,
                                             workers=args.concurrency)
        if cs_summary["checked"] or cs_summary["reused"]:
            k = cs_summary["counts"]
            logger.info(f"Citation scope: {cs_summary['checked']} classified"
                        + (f", {cs_summary['reused']} kept from the previous run"
                           if cs_summary["reused"] else "")
                        + f" — {k['full']} full, {k['methods']} methods, "
                        + f"{k['concept']} concept, {k['related']} related"
                        + (f" (scoped: {', '.join(cs_summary['scoped_ids'])})"
                           if cs_summary["scoped_ids"] else ""))
    so_summary = None
    llm2 = None
    if args.second_opinion:
        # Wrapped: at this point the judging above is already PAID FOR, and
        # analysis.json isn't on disk yet — a bad second-opinion model string
        # (or a missing `claude` CLI) must not take the whole run down with it.
        try:
            llm2 = LLMClient(model=args.second_opinion, api_key=args.api_key,
                             api_base=args.api_base)
            so_summary = second_opinion.run(analysis["text_claims"], llm2,
                                            workers=args.concurrency)
        except Exception as e:
            logger.warning(f"Second-opinion pass failed (verdicts unaffected): {e}")
        if so_summary is not None:
            flags = so_summary["fp_flags"] + so_summary["strict_flags"]
            logger.info(f"Second opinion ({llm2.model}): {so_summary['checked']} checked"
                        + (f", {so_summary['reused']} kept from the previous run"
                           if so_summary["reused"] else "")
                        + (f" — DISAGREES on {', '.join(flags)} "
                           f"({len(so_summary['fp_flags'])} supported challenged, "
                           f"{len(so_summary['strict_flags'])} unsupported challenged)"
                           if flags else " — agrees with every verdict"))

    arb_summary = None
    llm_arb = None
    if args.arbiter:
        # Wrapped like the second opinion: judging is already paid for — a bad
        # arbiter model string or a missing key must not take the run down.
        try:
            llm_arb = LLMClient(model=args.arbiter,
                                api_key=arbiter.resolve_key(args.arbiter))
            arb_summary = arbiter.run(analysis["text_claims"], sources, llm_arb,
                                      workers=args.concurrency)
        except Exception as e:
            logger.warning(f"Arbiter pass failed (verdicts unaffected): {e}")
        if arb_summary is not None:
            logger.info(
                f"Arbiter ({args.arbiter}): {arb_summary['checked']} flagged claim(s) "
                f"checked"
                + (f", {arb_summary['reused']} kept from the previous run"
                   if arb_summary["reused"] else "")
                + f" — actions {arb_summary['actions']}"
                + (f"; proof may exist for {', '.join(arb_summary['proof_may_exist'])}"
                   if arb_summary["proof_may_exist"] else "")
                + (f"; conflicting evidence on {', '.join(arb_summary['conflicts'])}"
                   if arb_summary["conflicts"] else ""))
        # Amber resolution (owner 2026-07-14): a NOT-PROVEN-AS-WRITTEN card
        # whose arbiter ruled "supported" WITH gate-verified proof quotes gets
        # the amber badge cleared (display-only; the verdict field was already
        # supported). Ambers the arbiter could not dissolve stay — and mean more.
        if arb_summary is not None:
            res_summary = arbiter.resolve_ambers(analysis["text_claims"])
            if res_summary["eligible"]:
                logger.info(
                    f"Arbiter amber resolution: {len(res_summary['resolved'])}"
                    f"/{res_summary['eligible']} 'not proven as written' flag(s) cleared"
                    + (f" ({', '.join(res_summary['resolved'])})"
                       if res_summary["resolved"] else "")
                    + (f"; still amber: {', '.join(res_summary['held'])}"
                       if res_summary["held"] else ""))
                arb_summary["amber_resolved"] = res_summary["resolved"]
        # Arbiter rescue (owner 2026-07-12): the arbiter's gate-verified proof
        # windows are re-judged by the PRIMARY judge; a unanimous positive
        # flips the false unsupported (method="arbiter_rescue") — the arbiter
        # fetches, the primary judge decides. --no-arbiter-rescue keeps the
        # old chips-only behavior.
        if arb_summary is not None and not args.no_arbiter_rescue:
            try:
                rescue_summary = arbiter.rescue(analysis["text_claims"], sources,
                                                llm, workers=args.concurrency)
                if rescue_summary["attempted"]:
                    logger.info(
                        f"Arbiter rescue: {rescue_summary['attempted']} attempted — "
                        + (f"FLIPPED to supported: {', '.join(rescue_summary['flipped'])}"
                           if rescue_summary["flipped"] else "no flips")
                        + (f"; held unsupported (judge not unanimous): "
                           f"{', '.join(rescue_summary['held'])}"
                           if rescue_summary["held"] else ""))
                    if arb_summary is not None:
                        arb_summary["rescued"] = rescue_summary["flipped"]
                    if rescue_summary["flipped"]:
                        # matcher.run computed the headline totals before the
                        # rescue — recount so the summary matches the verdicts.
                        t = analysis["coverage"]["totals"]
                        t["supported"] = sum(1 for c in analysis["text_claims"]
                                             if c["verdict"] == "supported")
                        t["unsupported"] = sum(1 for c in analysis["text_claims"]
                                               if c["verdict"] == "unsupported")
            except Exception as e:
                logger.warning(f"Arbiter rescue failed (verdicts unaffected): {e}")
        # "Partly proven" badge (task #1 step 3): for claims STILL unsupported
        # after rescue whose arbiter holds gate-verified proofs, one small call
        # maps each proof to the claim part it proves; a proven CORE part (with
        # gaps remaining) earns the slate badge. Display-only, never a verdict.
        if arb_summary is not None:
            try:
                pm_summary = arbiter.partial_map(analysis["text_claims"], llm_arb,
                                                 workers=args.concurrency)
                if pm_summary["checked"] or pm_summary["reused"]:
                    logger.info(
                        f"Partly-proven mapping: {pm_summary['checked']} claim(s) mapped"
                        + (f", {pm_summary['reused']} kept from the previous run"
                           if pm_summary["reused"] else "")
                        + (f" — badge on {', '.join(pm_summary['badged'])}"
                           if pm_summary["badged"] else " — no badges")
                        + (f"; proven parts not central enough on "
                           f"{', '.join(pm_summary['held'])}"
                           if pm_summary["held"] else ""))
                    arb_summary["partly_proven"] = pm_summary["badged"]
            except Exception as e:
                logger.warning(f"Partly-proven mapping failed (verdicts unaffected): {e}")

    analysis["metadata"] = {
        "text_file": os.path.abspath(args.text),
        "sources_dir": os.path.abspath(args.sources),
        "output_dir": os.path.abspath(args.output_dir),
        "model": llm.model,
        "decompose": do_decompose,
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "marker_errors": marker_errors,
        "processing_time_seconds": round(time.time() - start, 1),
        "source_hashes": source_hashes,
        # Which library extracted the PDF text (task #71). Source files are
        # compared by BYTES, so a changed extractor is otherwise invisible to
        # the reuse check even though every source sentence may have moved.
        "source_reader": source_decomposer.source_reader_id(),
    }
    # Which instruction text produced this run (task #44): fingerprints of
    # every prompt file as frozen at run start, plus which were actually
    # loaded. On an incremental run the reused verdicts were produced under
    # the PREVIOUS run's prompts — reuse only happened because every prompt it
    # used is byte-identical now, so its "used" names are carried forward to
    # keep the next comparison complete (a mostly-incremental run loads few
    # prompts itself).
    prompts_block = prompt_store.metadata_block()
    if prev_analysis is not None and reuse_map:
        prev_used = (prev_analysis.get("metadata", {}).get("prompts")
                     or {}).get("used") or []
        prompts_block["used"] = sorted(set(prompts_block["used"]) | set(prev_used))
    analysis["metadata"]["prompts"] = prompts_block

    # Actual spend (owner ask, 2026-07-11): what THIS run really used, per
    # model — the estimator's numbers above were a pre-run prediction.
    actual_usage = llm_client.usage_summary()
    if actual_usage:
        analysis["metadata"]["llm_usage"] = actual_usage
    if prev_analysis is not None and (reuse_map or prev_info):
        analysis["metadata"]["incremental"] = {
            "reused": len(reuse_map), "changed": len(prev_info),
            "previous_timestamp": prev_analysis.get("metadata", {}).get("timestamp"),
        }
    if own_summary is not None:
        analysis["metadata"]["own_split"] = {
            "counts": own_summary["counts"], "fact_ids": own_summary["fact_ids"],
            "unparsed": own_summary.get("unparsed", 0),
        }
    if cs_summary is not None:
        analysis["metadata"]["citation_scope"] = {
            "counts": cs_summary["counts"], "scoped_ids": cs_summary["scoped_ids"],
            "unparsed": cs_summary.get("unparsed", 0),
        }
    if arb_summary is not None:
        analysis["metadata"]["arbiter"] = {
            "model": args.arbiter, "checked": arb_summary["checked"],
            "actions": arb_summary["actions"],
            "proof_may_exist": arb_summary["proof_may_exist"],
            "conflicts": arb_summary["conflicts"],
            "rescued": arb_summary.get("rescued", []),
            "amber_resolved": arb_summary.get("amber_resolved", []),
        }
    if so_summary is not None:
        analysis["metadata"]["second_opinion"] = {
            "model": llm2.model, "checked": so_summary["checked"],
            "fp_flags": so_summary["fp_flags"],
            "strict_flags": so_summary["strict_flags"],
            "skipped_failed": so_summary.get("skipped_failed", []),
        }
    # Task #37 — outage honesty: the result file itself states whether any
    # model request failed during this run, so nothing has to scan every claim
    # (or the terminal log) to notice a broken run. failed_calls sums the
    # judge, second-opinion, and arbiter clients; claims_affected lists every
    # claim carrying a judge_error or checks_failed marker. The benchmark
    # scorers refuse to score a run where this block is non-zero.
    analysis["metadata"]["llm_failures"] = {
        "failed_calls": sum(getattr(x, "failed_calls", 0)
                            for x in (llm, llm2, llm_arb) if x is not None),
        "claims_affected": sorted(
            c["id"] for c in analysis["text_claims"]
            if c.get("judge_error") or c.get("checks_failed")),
    }
    analysis["sources"] = [
        {"paper_id": pid, "key": s.get("key"), "filename": s.get("filename"),
         "title": s.get("title"), "num_claims": len(s.get("claims", []))}
        for pid, s in sources.items()
    ]

    # Keep the previous run readable for comparisons before overwriting it.
    if os.path.exists(analysis_path):
        try:
            shutil.copy2(analysis_path, os.path.join(args.output_dir, "analysis_prev.json"))
        except Exception as e:
            logger.warning(f"Could not archive the previous analysis.json: {e}")
        # An old deep_check.json describes the verdicts we are about to replace,
        # and its comments are keyed by positional claim id — so it must not sit
        # beside the new verdicts (task #57, 2026-08-19). Set it aside the same
        # way as the analysis; nothing is deleted, one generation is kept.
        try:
            still_valid = deep_check_store.load_valid(args.output_dir, analysis)[1]
            archived = deep_check_store.archive_previous(args.output_dir)
            if archived:
                logger.info(
                    "The deep-check comments from the previous run were set aside as "
                    f"{os.path.basename(archived)}, because the claims have just been "
                    "judged again. Run deep_check.py on this folder to get fresh "
                    "comments.")
                if still_valid.get("total") and not still_valid.get("dropped"):
                    logger.info(
                        "Those comments still match every claim and verdict in this "
                        f"run ({still_valid['usable']} of them), so nothing was lost: "
                        f"copy {deep_check_store.PREV_FILENAME} back to "
                        f"{deep_check_store.FILENAME} to keep them.")
        except Exception as e:
            logger.warning(f"Could not archive the previous deep_check.json: {e}")
    with open(analysis_path, "w", encoding="utf-8") as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)
    logger.info(f"Wrote analysis: {analysis_path}")
    # The run's verdicts are safely in analysis.json now — the save-as-you-go
    # journal has done its job and would only shadow future edits if kept.
    ckpt_writer.close()
    try:
        os.remove(ckpt_path)
    except OSError:
        pass

    # Optional PROV-O export — pure, no LLM calls.
    if args.provenance_export:
        try:
            p = provenance_export.export_file(args.output_dir)
            logger.info(f"Wrote provenance: {p}")
        except Exception as e:
            logger.warning(f"Provenance export failed (verdicts unaffected): {e}")

    # Optional argument-structure passes (argument map + crux + independence).
    # Additive and experimental: any failure logs and leaves the core run intact.
    assessment = None
    if args.argument_map:
        assessment = {}
        try:
            # Edge inference is ONE batched call (nothing to fan out or cap).
            amap = argument_map.build_map(analysis, llm, cache_dir=args.output_dir)
            argument_map.write_map(amap, args.output_dir)
            assessment["argument_map"] = amap
            logger.info(f"Argument map: {len(amap.get('nodes', []))} nodes, "
                        f"{len(amap.get('edges', []))} edges")
        except Exception as e:
            logger.warning(f"Argument-map build failed: {e}")
        try:
            dedup_path = os.path.join(args.output_dir, "dedup.json")
            dedup = None
            if os.path.exists(dedup_path):
                with open(dedup_path, encoding="utf-8") as f:
                    dedup = json.load(f)
            indep = evidence_independence.assess_independence(analysis, dedup=dedup,
                                                              cache_dir=args.output_dir)
            evidence_independence.write_independence(indep, args.output_dir)
            assessment["independence"] = indep
            logger.info("Evidence independence: "
                        f"{indep.get('summary', {}).get('n_clusters')} independent cluster(s)")
        except Exception as e:
            logger.warning(f"Independence assessment failed: {e}")
        try:
            cx = crux.find_cruxes(assessment.get("argument_map") or {"nodes": [], "edges": []},
                                  analysis=analysis, independence=assessment.get("independence"))
            crux.write_cruxes(cx, args.output_dir)
            assessment["crux"] = cx
            logger.info(f"Cruxes: top {len(cx.get('cruxes', []))} by {cx.get('method')}")
        except Exception as e:
            logger.warning(f"Crux ranking failed: {e}")

    viewer_path = os.path.join(args.output_dir, "viewer.html")
    viewer.generate(analysis, viewer_path, title=f"Verification — {os.path.basename(args.text)}",
                    source_texts=source_texts, assessment=assessment)
    # v2 comparison period (docs/VIEWER_V2_DESIGN.md): every run also writes the
    # redesigned viewer next to v1 — same data, shared review marks, no LLM cost.
    from modules.papertrail import viewer_v2
    viewer_v2.generate(analysis, os.path.join(args.output_dir, "viewer_v2.html"),
                       title=f"Verification — {os.path.basename(args.text)}",
                       source_texts=source_texts, assessment=assessment)

    t = analysis["coverage"]["totals"]
    # Break "unsupported" down by cause — "28 unsupported" reads as 28 failed
    # judgments when some are just missing source files. Uncited text is its own
    # verdict now ("own" — the author's original claims).
    uns = [c for c in analysis["text_claims"] if c["verdict"] == "unsupported"]
    n_missing = sum(1 for c in uns if c["reason"].startswith("source_file_missing"))
    n_judged = len(uns) - n_missing
    logger.info(f"Done in {analysis['metadata']['processing_time_seconds']}s — "
                f"{t['supported']} supported, {t['unsupported']} unsupported "
                f"({n_judged} judged against sources, {n_missing} missing source file), "
                f"{t.get('own', 0)} your own (uncited), {t['omitted']} unused source points "
                f"(not errors — viewer shows the most relevant)")
    if so_summary is not None and (so_summary["fp_flags"] or so_summary["strict_flags"]):
        logger.info(f"Second-opinion flags to review in the viewer: "
                    f"{', '.join(so_summary['fp_flags'] + so_summary['strict_flags'])}")
    # A verdict minted while model calls were dying is not a finding. Say so
    # louder than the per-call log lines, and promise the (true, thanks to
    # rerun.reusable) cheap fix: a plain re-run retries exactly these claims.
    lf = analysis["metadata"]["llm_failures"]
    if lf["failed_calls"] or lf["claims_affected"]:
        affected = lf["claims_affected"]
        detail = (f"{len(affected)} claim(s) could not be fully checked — they are "
                  f"marked in the viewer, a rejected verdict on them may be an "
                  f"artifact of the outage rather than the sources, and re-running "
                  f"the same command retries JUST these claims: "
                  f"{', '.join(affected[:12])}{'…' if len(affected) > 12 else ''}"
                  if affected else
                  "no verdict was affected, but a small tag pass was skipped on "
                  "some claims; a re-run fills those in")
        print(f"\nWARNING: {lf['failed_calls']} request(s) to the model failed "
              f"during this run (rate limit / quota / outage; see the log above). "
              f"{detail}", file=sys.stderr)

    if actual_usage:
        for mdl, u in actual_usage.items():
            cost_str = f"${u['cost_usd']:.4f}" if u["cost_usd"] else "$0 (or unknown pricing)"
            cached = u.get("cached_prompt_tokens", 0)
            cached_str = f" (of which {cached:,} cached)" if cached else ""
            # claude-code reports no per-attempt usage; show its logical calls.
            n_calls = u["calls"] or sum(p["calls"] for p in u.get("by_purpose", {}).values())
            print(f"Actual usage [{mdl}]: {n_calls} calls, "
                  f"{u['prompt_tokens']:,} in{cached_str} / {u['completion_tokens']:,} out tokens, {cost_str}")
        # Task #20: where the calls went. One row per purpose, costliest first —
        # this table is what phase 2 (context trimming) will be planned from.
        rows = []
        for mdl, u in actual_usage.items():
            for pname, p in u.get("by_purpose", {}).items():
                rows.append((p["cost_usd"], p["calls"], pname, mdl, p))
        if rows:
            rows.sort(key=lambda r: (-r[0], -r[1], r[2]))
            many_models = len(actual_usage) > 1
            print("Where the calls went (per purpose, costliest first):")
            for cost, _calls, pname, mdl, p in rows:
                label = f"{pname} [{mdl}]" if many_models else pname
                print(f"  {label:<32} {p['calls']:>5} calls  "
                      f"{p['prompt_tokens']:>11,} in / {p['completion_tokens']:>9,} out  "
                      f"${cost:.4f}")
        # Estimate-vs-actual in one line (working-style review lesson 4,
        # approved 2026-08-09): estimates ran 2-3x off in both directions and
        # every gap was reconciled by hand after the fact. `point` is the
        # pre-run point estimate for the core judging only — the add-on passes
        # were announced as excluded, so name that in the comparison.
        if point is not None:
            total_actual = sum(u["cost_usd"] for u in actual_usage.values())
            if point > 0:
                print(f"Estimate check: predicted ${point:.2f} before the run "
                      f"(core judging only, add-on passes excluded) — the whole "
                      f"run actually cost ${total_actual:.4f}, i.e. "
                      f"{total_actual / point:.1f} times the estimate.")
            else:
                print(f"Estimate check: predicted $0.00 before the run — the "
                      f"whole run actually cost ${total_actual:.4f}.")

    viewer_abs = os.path.abspath(viewer_path)
    print(f"\nReview file (open in any browser — no server needed):\n  {viewer_abs}")
    if args.open:
        webbrowser.open("file://" + viewer_abs)


if __name__ == "__main__":
    main()
