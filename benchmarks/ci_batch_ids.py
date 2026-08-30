"""Batch-qualified row ids for the Citation-Integrity benchmark.

Every converted batch numbers its rows from `cidev0001`, so the same `cidev`
number means a **different row** in a different batch. That is harmless while a
report covers one batch and dangerous the moment it covers two: a bare
`cidev0019` is then ambiguous, and a reader — or a summarizing model — will merge
two unrelated rows without noticing.

The globally unique key is `ci_id` (e.g. `dev/006_PMC3179858/PMC3142215_2`),
which the converter carries on every row and which is disjoint between batches.
`cidev` numbers stay as they are: frozen run directories and the `markers` inside
`analysis.json` reference them, so they can never be renumbered.

So: keep the `cidev` number as the within-batch id, and qualify it with the batch
tag whenever it leaves for a human — `pilot100:cidev0019`, `fresh50:cidev0026`.
`merge()` is the only supported way to combine rows from several batches, and it
qualifies by construction rather than trusting the caller to remember.

Pure: no API calls, no network, no disk access except the helpers that are handed
an already-loaded ground truth.
"""
import os
import re

_BATCH_PREFIXES = ("batch_dev_", "batch_")
_RUN_SUFFIX = re.compile(r"_run(?:_.*)?$")
SEP = ":"


def batch_tag(path):
    """Short, stable tag for a batch directory or one of its run directories.

    `batch_dev_pilot100` and `batch_dev_pilot100_run_gemma_0802` both give
    `pilot100`; `batch_dev_fresh50` gives `fresh50`. Derived from the directory
    name rather than a hand-kept alias table, so a batch added later needs no
    edit here.
    """
    name = os.path.basename(os.path.normpath(str(path)))
    name = _RUN_SUFFIX.sub("", name)
    for p in _BATCH_PREFIXES:
        if name.startswith(p):
            name = name[len(p):]
            break
    return name or "batch"


def qualify(tag, row_id):
    """`('pilot100', 'cidev0019')` -> `'pilot100:cidev0019'`. Idempotent."""
    row_id = str(row_id)
    if SEP in row_id:
        return row_id
    return f"{tag}{SEP}{row_id}"


def qualify_all(tag, row_ids):
    """Qualify an iterable, preserving order."""
    return [qualify(tag, r) for r in row_ids]


def unqualify(qid):
    """`'pilot100:cidev0019'` -> `('pilot100', 'cidev0019')`.

    An unqualified id gives `(None, id)` rather than raising, so this can be used
    to read older artifacts that predate the convention.
    """
    qid = str(qid)
    if SEP not in qid:
        return None, qid
    tag, _, row = qid.partition(SEP)
    return tag, row


def merge(by_tag):
    """Combine per-batch mappings into one dict keyed by qualified id.

    `by_tag` maps a batch tag (or a batch/run path — it is passed through
    `batch_tag`) to a mapping of row id -> anything. Raises `ValueError` if two
    inputs resolve to the same tag, which is the mistake this module exists to
    prevent: merging two batches under one name silently drops the collisions.
    """
    out, seen = {}, {}
    for raw_tag, mapping in by_tag.items():
        tag = batch_tag(raw_tag)          # idempotent on a plain tag
        if tag in seen:
            raise ValueError(
                f"two inputs resolve to batch tag {tag!r} ({seen[tag]!r} and "
                f"{raw_tag!r}); rows would collide silently — give them "
                f"distinct tags")
        seen[tag] = raw_tag
        for row_id, value in mapping.items():
            out[qualify(tag, row_id)] = value
    return out


def ci_id(gt_claims, row_id):
    """The globally unique id of a row, or None if the batch predates the field.

    `gt_claims` is the `claims` dict of a batch's `ci_ground_truth.json`.
    """
    return (gt_claims.get(str(row_id)) or {}).get("ci_id")


def check_disjoint(gt_claims_by_tag):
    """Verify the invariant `--exclude-used` is supposed to guarantee.

    Returns the list of `ci_id`s shared by two or more batches — empty when the
    batches are genuinely disjoint, which is the case the benchmark relies on.
    Bare `cidev` numbers are expected to collide and are not checked here.
    """
    seen, shared = {}, set()
    for tag, claims in gt_claims_by_tag.items():
        for row_id, row in claims.items():
            cid = (row or {}).get("ci_id")
            if cid is None:
                continue
            if cid in seen and seen[cid] != tag:
                shared.add(cid)
            seen.setdefault(cid, tag)
    return sorted(shared)
