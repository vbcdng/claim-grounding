#!/usr/bin/env bash
# Build the friends/judge release zip — STRICT INCLUDE-LIST packaging.
#
# Everything that ships is named explicitly below; nothing is swept in by
# glob-the-repo. This is deliberate: config/ holds live API key files and
# data/ holds runs over copyrighted source PDFs, so an exclude-based approach
# is one forgotten pattern away from leaking a key. A safety gate at the end
# fails the build if any key file, key-shaped string, cache dir, or run
# output makes it into the staging tree anyway.
#
# Usage: ./make_release_zip.sh [out.zip] [example_project_dir]
#   out.zip             default: claim-grounding-alpha.zip
#   example_project_dir default: data/printing_press_reformation_project
#                       (pass "" to build without a bundled example)
set -euo pipefail
cd "$(dirname "$0")"

OUT="${1:-claim-grounding-alpha.zip}"
EXAMPLE_DIR="${2-data/printing_press_reformation_project}"

fail() { echo "PACKAGING FAILED: $1" >&2; exit 1; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
DEST="$STAGE/claim-grounding"
mkdir -p "$DEST"

# --- entry points + user-facing docs ---
cp verify_my_text.py import_claude_research.py import_paper.py \
   download_sources.py ingest_downloads.py find_replacement_sources.py \
   deep_check.py \
   requirements.txt INPUT_FORMAT.md LOCAL_MODELS.md LICENSE "$DEST/"
# One entry doc (2026-07-17, owner): the release README replaces the dev
# README + QUICKSTART + WIZARD_GUIDE — install, keys, example, wizard, the
# conversion prompt and result-reading all live in it. CONVERT_MY_TEXT_PROMPT
# still ships standalone (referenced from code and handy to copy from).
cp docs/RELEASE_README.md "$DEST/README.md"
cp docs/CONVERT_MY_TEXT_PROMPT.md "$DEST/CONVERT_MY_TEXT_PROMPT.md"
cp docs/RESEARCH_WRITE_PROMPT.md "$DEST/RESEARCH_WRITE_PROMPT.md"
cp docs/release_assets/viewer_screenshot.png "$DEST/viewer_screenshot.png"

# --- code ---
mkdir -p "$DEST/modules/papertrail"
cp modules/__init__.py "$DEST/modules/"
cp modules/papertrail/*.py "$DEST/modules/papertrail/"

# --- config: prompts + model defaults ONLY. Never anything matching *api_key*. ---
mkdir -p "$DEST/config/prompts"
cp config/gemini_config.json "$DEST/config/"
cp config/prompts/*.txt "$DEST/config/prompts/"

# --- Claude Code slash commands + the docs they reference ---
mkdir -p "$DEST/.claude/commands" "$DEST/docs"
cp .claude/commands/*.md "$DEST/.claude/commands/"
cp docs/REPAIR_PLAYBOOK.md docs/MODEL_OPTIONS.md "$DEST/docs/"
# /walkthrough cites docs/WALKTHROUGH_OWNER_TODO.md ("items 8-14"); ship the
# distilled, release-safe version of that log under the cited path so the
# reference resolves (source: docs/release_assets/WALKTHROUGH_ORIGIN.md).
cp docs/release_assets/WALKTHROUGH_ORIGIN.md "$DEST/docs/WALKTHROUGH_OWNER_TODO.md"

# --- bundled example project (input artifacts only, no run outputs) ---
# No bundled PDFs (2026-07-17, owner): testers fetch the sources themselves
# with download_sources.py; the report regenerated below lists every missing
# source with a link + save-as filename, so a failed download can be done by
# hand. Small page-text .txt sources stay (cheap, and not re-fetchable
# deterministically).
if [ -n "$EXAMPLE_DIR" ] && [ -d "$EXAMPLE_DIR" ]; then
  mkdir -p "$DEST/example"
  cp -r "$EXAMPLE_DIR/." "$DEST/example/"
  find "$DEST/example" -name '*.pdf' -delete
  rm -f "$DEST/example/download_report.md"
  venv/bin/python download_sources.py \
    --manifest "$DEST/example/sources_manifest.json" --report-only \
    >/dev/null \
    || fail "could not regenerate the example's download_report.md"
fi

# ---------- safety gate: fail loudly rather than ship a leak ----------

# 1) no key files by name
find "$DEST" -iname '*api_key*' -print -quit | grep -q . \
  && fail "a file matching *api_key* was staged"

# 2) no key-shaped material inside any staged text file
#    (Google AIza..., OpenAI/DeepSeek/OpenRouter sk-...)
grep -rIlE 'AIza[0-9A-Za-z_-]{30,}|sk-[A-Za-z0-9_-]{20,}' "$DEST" \
  && fail "key-shaped string found in the file(s) above"

# 3) no caches / venv / git / logs
find "$DEST" \( -name '__pycache__' -o -name '*.pyc' -o -name 'venv' \
  -o -name '.git' -o -name '*.log' \) -print -quit | grep -q . \
  && fail "cache/venv/git material was staged"

# 4) no run outputs in the bundled example (it must be inputs-only),
#    and no PDFs (testers download the sources themselves)
if [ -d "$DEST/example" ]; then
  find "$DEST/example" \( -name 'analysis*.json' -o -name 'viewer.html' \
    -o -name 'review.json' -o -name 'embeddings' -o -name 'source_claims' \
    -o -name '*.pdf' \) \
    -print -quit | grep -q . \
    && fail "run outputs or PDFs found inside the bundled example"
fi

# 5) nothing from the owner-only benchmark/gate material
find "$DEST" \( -name '*ground_truth*' -o -name 'coverage_check*' \
  -o -name 'regression_check*' \) -print -quit | grep -q . \
  && fail "benchmark/gate material was staged"

# ---------- build ----------
(cd "$STAGE" && zip -qr out.zip claim-grounding)
mv "$STAGE/out.zip" "$OUT"

echo "Built $OUT ($(du -h "$OUT" | cut -f1))"
echo "--- contents (top 2 levels) ---"
unzip -l "$OUT" | awk '{print $4}' | grep -v '^$' \
  | awk -F/ 'NF<=3' | sort -u
