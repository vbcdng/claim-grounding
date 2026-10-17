#!/usr/bin/env bash
# Stability study (internal label CARD160): make the clean checkout every arm
# runs from.
#
#   bash benchmarks/card160/make_checkout.sh [dir] [commit]
#
#   dir     default <main checkout>/.claude/worktrees/card160_measured
#   commit  default 4899c40 — the development version of 2026-10-02 that the
#           study measures.
#
# Makes a DETACHED git worktree of that commit (no branch, nothing to commit
# into), then links in what the gate scripts read but git does not hold: venv/,
# data/, logs/ and the API key files under config/. Runs no model and changes
# no branch. Safe to run
# again: an existing folder at the right commit is only re-linked; a folder at
# any other commit, or with edited tracked files, is refused.
set -u
here="$(cd "$(dirname "$0")/../.." && pwd)"
main="$(git -C "$here" worktree list --porcelain | awk '/^worktree /{print $2; exit}')"
dir=${1:-$main/.claude/worktrees/card160_measured}
commit=${2:-4899c40}
want=$(git -C "$main" rev-parse --verify "$commit^{commit}") || { echo "ERROR: no commit $commit" >&2; exit 2; }

if [ -e "$dir" ]; then
  have=$(git -C "$dir" rev-parse HEAD 2>/dev/null || echo none)
  if [ "$have" != "$want" ]; then
    echo "ERROR: $dir exists but holds $have, not $want. Pick another folder." >&2
    exit 2
  fi
  if [ -n "$(git -C "$dir" status --porcelain --untracked-files=no)" ]; then
    echo "ERROR: $dir has edited tracked files; the measured code must be the commit as it is." >&2
    exit 2
  fi
  echo "checkout exists at $want: $dir"
else
  git -C "$main" worktree add --detach "$dir" "$want" || exit 2
fi

for name in venv data logs; do
  [ -e "$dir/$name" ] || ln -s "$main/$name" "$dir/$name"
done
for f in "$main"/config/google_api_key*.txt "$main"/config/openrouter_api_key.txt; do
  [ -f "$f" ] || continue
  [ -e "$dir/config/$(basename "$f")" ] || ln -s "$f" "$dir/config/$(basename "$f")"
done
echo "CARD160_CHECKOUT=$dir"
echo "commit: $(git -C "$dir" rev-parse --short HEAD)"
