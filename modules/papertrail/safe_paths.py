"""
Path-safety helpers for anything built out of externally-supplied names.

Why this exists (security review 2026-08-31, task #70, findings 1 and 3)
-----------------------------------------------------------------------
Citation keys arrive from files the author did not write: a `.bib` from a
research export, `[@key]` markers in imported markdown. Those keys become
filenames (`sources/<key>.pdf`) and web links in the viewer. Before this
module they were used verbatim, and two things followed:

  * `os.path.join(sources_dir, "/tmp/x.pdf")` returns `/tmp/x.pdf` — Python
    discards the base whenever the second part is absolute. A key of
    `/tmp/pwned`, or the relative `x/../../../.bashrc`, therefore wrote the
    downloaded bytes anywhere the process could write.
  * `html.escape()` does not touch a URL scheme, so a `javascript:` address
    coming from a metadata field stayed clickable in a generated page.

Two defenses, deliberately independent — sanitize the name at the seam where
it enters (`safe_key`), and refuse the write if the final path still lands
outside the intended directory (`resolve_inside`). Either alone would close
finding 1; both together survive a future caller that forgets the first.

`safe_key`'s charset is not a new invention: it is exactly what
`text_decomposer.MARKER_RE` (`[A-Za-z0-9_-]+`) already requires of a
`[[key]]` marker. A key containing `:` or `.` used to produce a marker the
claim splitter could not read, so sanitizing also repairs that older mismatch.

No LLM calls, no I/O, stdlib only.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Optional

# Everything the rest of the tool accepts in a citation key. Kept in step with
# text_decomposer.MARKER_RE — widen both or neither.
_UNSAFE_RUN_RE = re.compile(r"[^A-Za-z0-9_-]+")

# Schemes a generated page may link to. Anything else (javascript:, data:,
# vbscript:, file:) is dropped rather than rendered as a live link.
_SAFE_URL_SCHEMES = ("http", "https")


def safe_key(key: str) -> str:
    """Reduce a citation key to the charset the whole pipeline accepts.

    Runs of unusable characters collapse to a single underscore, and leading or
    trailing separators are trimmed so a key can never begin with '-' (which a
    command-line program would read as an option) or be an absolute path. A key
    that survives with nothing left falls back to a hash of the original, so two
    different unusable keys never silently become one entry.
    """
    cleaned = _UNSAFE_RUN_RE.sub("_", key or "").strip("_-")
    if not cleaned:
        digest = hashlib.sha1((key or "").encode("utf-8")).hexdigest()[:8]
        cleaned = f"ref_{digest}"
    return cleaned


def resolve_inside(base_dir: str, filename: str) -> Optional[str]:
    """Absolute path for `filename` inside `base_dir`, or None if it escapes.

    The backstop for finding 1: call this instead of trusting a path built with
    os.path.join, and treat None as "refuse the write". Symlinks are resolved
    first, so a symlink planted inside the sources folder cannot be used to
    step outside it either.
    """
    base = os.path.realpath(base_dir)
    target = os.path.realpath(os.path.join(base, filename))
    if target == base or not target.startswith(base + os.sep):
        return None
    return target


def safe_link(url: str) -> str:
    """Return `url` if it is an ordinary web address, else "".

    Finding 3: HTML-escaping protects the surrounding markup but leaves the
    scheme alone, so `javascript:...` stays executable when clicked. Callers
    building an href/src attribute pass the value through here first.
    """
    value = (url or "").strip()
    if not value:
        return ""
    # A value with no scheme at all (a relative path like "sources/x.pdf") is
    # fine; only an explicit scheme needs checking. Ignore a colon that appears
    # after a '/' — that is a path or query, not a scheme separator.
    head = value.split("/", 1)[0]
    if ":" not in head:
        return value
    scheme = head.split(":", 1)[0].lower()
    return value if scheme in _SAFE_URL_SCHEMES else ""
