#!/usr/bin/env bash
# Release smoke test — run BEFORE any copy of this repo goes to anyone.
#
# Implements the published practice of testing the ARTIFACT, not the dev tree
# (pytest "Good Integration Practices": test the installed/distributed copy;
# check-manifest: a distribution must contain everything the code needs).
# Born from the first-tester incident of 2026-07-20: a trimmed zip crashed on
# a runtime file that only existed in the dev repo.
#
# Usage: benchmarks/release_smoke.sh <artifact>    (a directory or a .zip)
#        PYTHON=/path/to/python overrides the interpreter for the test suite
#        (defaults to "python3"; deps like torch are needed for the suite —
#        point PYTHON at a venv that has them).
#
# What it does, all offline (no network, no API keys):
#   1. Copies/unzips the artifact into a temp dir WITH A SPACE in the path
#      (real first-tester condition; catches unquoted-path bugs).
#   2. Runs the unit test suite from inside the copy — with
#      tests/test_runtime_resources.py in the suite this proves the copy is
#      runtime-complete.
#   3. Runs --help on every CLI entry point with a BARE python3 (no deps) —
#      proves argparse works on a machine with nothing installed.
#   4. Runs verify_my_text.py --estimate on a packaged example, for BOTH
#      backends (api + claude-code) — exercises input parsing end to end.
# Exits non-zero on the first failure.

set -u
ARTIFACT="${1:?usage: release_smoke.sh <artifact dir or zip>}"
PYTHON="${PYTHON:-python3}"
FAIL=0

WORK="$(mktemp -d)/release smoke"   # space is deliberate
mkdir -p "$WORK"
trap 'rm -rf "${WORK%/*}"' EXIT

echo "== 1. unpack into: $WORK"
if [[ -d "$ARTIFACT" ]]; then
    cp -r "$ARTIFACT" "$WORK/copy"
elif [[ "$ARTIFACT" == *.zip ]]; then
    unzip -q "$ARTIFACT" -d "$WORK/copy"
else
    echo "FAIL: artifact is neither a directory nor a .zip"; exit 1
fi
cd "$WORK/copy"

echo "== 2. unit suite inside the copy ($PYTHON)"
if [[ -d tests ]]; then
    if ! "$PYTHON" -m unittest discover -s tests -p 'test_*.py' -q 2>&1 | tail -3; then
        echo "FAIL: test suite failed inside the artifact"; FAIL=1
    fi
    "$PYTHON" -m unittest discover -s tests -p 'test_*.py' -q >/dev/null 2>&1 || FAIL=1
else
    echo "FAIL: artifact ships without tests/ — the suite cannot vouch for it"
    FAIL=1
fi

echo "== 3. CLI --help on bare python3"
for cli in verify_my_text.py import_paper.py import_claude_research.py \
           download_sources.py ingest_downloads.py find_replacement_sources.py \
           deep_check.py; do
    if [[ -f "$cli" ]]; then
        if python3 "$cli" --help >/dev/null 2>&1; then
            echo "   ok  $cli"
        else
            echo "   FAIL $cli --help"; FAIL=1
        fi
    fi
done

echo "== 4. offline --estimate on a packaged example, both backends"
EX=""
for cand in examples/bentonite examples/chimpanzee_validation; do
    [[ -f "$cand/my_text.md" ]] && EX="$cand" && break
done
if [[ -n "$EX" ]]; then
    for extra in "" "--backend claude-code"; do
        if "$PYTHON" verify_my_text.py --text "$EX/my_text.md" \
              --sources "$EX/sources" --references "$EX/my_text.md.refs.txt" \
              --output-dir "$WORK/est" --estimate $extra >/dev/null 2>&1; then
            echo "   ok  estimate ${extra:-(api default)}"
        else
            echo "   FAIL estimate ${extra:-(api default)}"; FAIL=1
        fi
    done
else
    echo "   FAIL: no runnable example packaged (examples/*/my_text.md)"; FAIL=1
fi

echo
if [[ $FAIL -eq 0 ]]; then echo "RELEASE SMOKE: PASS"; else echo "RELEASE SMOKE: FAIL"; fi
exit $FAIL
