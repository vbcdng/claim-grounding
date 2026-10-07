#!/usr/bin/env python3
"""Task #15 labeling panel runner — several models, one rubric, full sources.

Each model in the panel independently judges every row in a rows.jsonl file
(built by build_round1_rows.py) with the strict rubric v1 prompt, reading the
row's FULL source text. Output is one JSONL line per (row, model) pair.

Hard rules baked in (see docs/TASK15_LOOP.md round 0 and task #37):
 - A refused / failed call is recorded as {"answered": false} — NEVER as a
   verdict. The run summary reports the refusal count.
 - Every proof quote is checked verbatim against the source text
   (whitespace/quote-mark/case normalized); the result is recorded per part
   as quote_verified, and never silently dropped.
 - Resume-safe: pairs already present in the output file are skipped, so an
   interrupted run continues instead of re-billing.
 - No verdict is ever produced on partial evidence: a source that does not fit
   the model's reading window is recorded as no-answer, never truncated.

Two reading modes, recorded per verdict as `read_mode` (round 1's verdicts
predate the field and are read as "whole_source"):
 - "whole_source" (the default and the method of record): the model gets the
   entire source in one prompt.
 - "sectioned" (opt-in, --sectioned-when-too-long): only for rows whose source
   does not fit the model's window. The source is cut into numbered pieces with
   an overlap, each piece is scanned in its own call for sentences bearing on
   the claim IN EITHER DIRECTION (supporting, contradicting or qualifying), the
   verbatim-verified sentences are collected in source order, and the rubric
   judgment then runs over that collection. Every piece must be read: if any
   piece is refused after a retry, the row is a no-answer, because a judgment
   over some of the pieces is a judgment over part of the evidence. A sectioned
   verdict can miss proof that a whole-source read would have found, so it is
   weaker evidence for "unproven" than a whole-source verdict and the funnel
   labels it as such. It is NOT the method of record.
 - "piece_read" (opt-in, --piece-read-when-too-long): the same
   numbered pieces, but no summarising step. The claim is split into its
   checkable parts in one call, and then EVERY piece is asked the rubric's own
   question about those same parts, with each proof quote checked word for word
   against the piece it came from. A part is proven when any piece proves it,
   contradicted when any piece contradicts it, and "the source is silent" only
   when every piece answered and none found anything — so silence rests on the
   whole source, which is what the round-1.5 control found the scan-then-judge
   mode could not promise. Costs the same as "sectioned": one call per piece
   plus one. Records silence_trustworthy=True, which the funnel reads.

Usage:
  python3 benchmarks/labeler/panel_runner.py \
      --rows benchmarks/labeler/rounds/round1/rows.jsonl \
      --models claude-code/sonnet,gemini/gemma-4-31b-it,deepseek/deepseek-v4-flash \
      --out benchmarks/labeler/rounds/round1/verdicts.jsonl

`--limit N` judges only the first N rows (smoke test). `--dry-run` builds the
prompts and prints their sizes without calling anything; with
`--sectioned-when-too-long` it also prints how many pieces and scan calls each
oversized row would cost.
"""
import argparse
import json
import os
import re
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from modules.papertrail import long_source  # noqa: E402  (needs REPO on the path)

PROMPT_PATH = os.path.join(REPO, "benchmarks", "labeler", "prompts",
                           "pt_labeler_rubric_v1.txt")
SCAN_PROMPT_PATH = os.path.join(REPO, "benchmarks", "labeler", "prompts",
                                "pt_labeler_scan_v1.txt")
SPLIT_PROMPT_PATH = os.path.join(REPO, "benchmarks", "labeler", "prompts",
                                 "pt_labeler_split_v1.txt")
PIECE_READ_PROMPT_PATH = os.path.join(REPO, "benchmarks", "labeler", "prompts",
                                      "pt_labeler_piece_read_v1.txt")
RUBRIC_VERSION = "v1"
VALID_LABELS = {"pass", "fail_contradicted", "fail_unproven", "invalid"}
VALID_CLASSES = {"proven", "contradicted", "unproven", "tolerated"}
VALID_RELATIONS = {"supports", "contradicts", "about"}
MAX_ATTEMPTS = 2  # one retry on malformed JSON; a refusal (None) is final
# Rows whose prompt exceeds this are recorded as no-answer, NEVER truncated:
# a silently-cut source would judge the claim against part of the evidence.
DEFAULT_MAX_PROMPT_CHARS = 480_000  # ~120k tokens, fits a 128k context window

# Sectioned reading (opt-in). 40k characters is ~10k tokens, comfortably under
# the ~50k-character ceiling round 1 measured on the free Google seat; the 2k
# overlap means any sentence shorter than 2k characters appears whole in at
# least one piece even when a cut lands in the middle of it.
DEFAULT_PIECE_CHARS = long_source.DEFAULT_PIECE_CHARS      # 40,000
DEFAULT_PIECE_OVERLAP = long_source.DEFAULT_PIECE_OVERLAP  # 2,000
MAX_SCAN_QUOTES = 12  # matches the scan prompt's own cap
SCAN_ATTEMPTS = 2     # one retry per piece: an unread piece kills the verdict
MAX_PARTS = 8         # the splitting prompt's own cap; more is a malformed split
# Piece-read mode's per-piece answers, before they are merged into the
# rubric's four classes.
VALID_PIECE_VERDICTS = {"proven", "contradicted", "tolerated", "not_stated_here"}
DIGEST_HEADER = (
    "(This source was too long to read in one sitting, so it was read in "
    "{n_pieces} numbered pieces. Below are the sentences a first reading pass "
    "found in those pieces that bear on the claim, copied word for word and in "
    "the order they appear in the source. Sentences not listed here were read "
    "and judged not to bear on the claim. If a part of the claim has no "
    "sentence here, treat that part as unproven — do not assume a proof exists "
    "somewhere else in the source.)")


def normalize(text):
    """Whitespace-collapse, unify quote marks and dashes, casefold — the same
    leniency a human eyeballing 'is this quote really in the source' applies."""
    text = re.sub(r"[‘’‚‛']", "'", text)
    text = re.sub(r"[“”„‟\"]", '"', text)
    text = re.sub(r"[‐-―−-]", "-", text)
    text = re.sub(r"\s+", " ", text)
    return text.casefold().strip()


def quote_in_sources(quote, sources):
    """Return the name of the source containing the quote, else None.
    Falls back to an alphanumeric-only comparison so PDF hyphenation and
    line-break artifacts don't fail an honestly-copied sentence."""
    if not quote:
        return None
    nq = normalize(quote)
    for s in sources:
        if nq and nq in normalize(s["text"]):
            return s["name"]
    bare_q = re.sub(r"[^a-z0-9]", "", nq)
    if len(bare_q) >= 20:
        for s in sources:
            if bare_q in re.sub(r"[^a-z0-9]", "", normalize(s["text"])):
                return s["name"]
    return None


def build_prompt(template, row):
    src_blocks = []
    for s in row["sources"]:
        src_blocks.append(f"--- SOURCE: {s['name']} ---\n{s['text']}")
    return (template
            .replace("{CLAIM}", row["claim_text"])
            .replace("{CONTEXT}", row.get("context") or "(none provided)")
            .replace("{SOURCES}", "\n\n".join(src_blocks)))


def validate_verdict(obj):
    """Return (cleaned_dict, None) or (None, reason)."""
    if not isinstance(obj, dict):
        return None, "response is not a JSON object"
    label = obj.get("strict_label")
    if label not in VALID_LABELS:
        return None, f"strict_label {label!r} not one of {sorted(VALID_LABELS)}"
    parts = obj.get("parts")
    if not isinstance(parts, list) or not parts:
        return None, "parts missing or empty"
    cleaned_parts = []
    for p in parts:
        if not isinstance(p, dict) or p.get("classification") not in VALID_CLASSES:
            return None, f"bad part entry: {p!r}"
        cleaned_parts.append({
            "part": str(p.get("part") or ""),
            "classification": p["classification"],
            "quote": p.get("quote") if isinstance(p.get("quote"), str) else None,
            "source": p.get("source") if isinstance(p.get("source"), str) else None,
        })
    return {"strict_label": label, "parts": cleaned_parts,
            "hard_note": obj.get("hard_note") if isinstance(obj.get("hard_note"), str) else None}, None


def split_into_pieces(text, piece_chars=DEFAULT_PIECE_CHARS,
                      overlap_chars=DEFAULT_PIECE_OVERLAP):
    """Cut a long source into overlapping pieces, in order.

    Deterministic and lossless: piece k starts `piece_chars - overlap_chars`
    after piece k-1 started, so every character of the source is inside at
    least one piece and any sentence shorter than the overlap survives whole
    in at least one piece even if a cut falls inside it.

    Task #105 moved the cutting itself into modules/papertrail/long_source.py
    so the tool and this panel cut a long source the same way; the geometry is
    unchanged, so every piece count round 1.5 recorded still holds."""
    return long_source.split_into_pieces(text, piece_chars, overlap_chars)


def plan_pieces(row, piece_chars=DEFAULT_PIECE_CHARS,
                overlap_chars=DEFAULT_PIECE_OVERLAP):
    """(source name, piece index, piece count, piece text) for the whole row,
    in source order — the exact list of scan calls a sectioned read costs."""
    plan = []
    for s in row["sources"]:
        pieces = split_into_pieces(s["text"], piece_chars, overlap_chars)
        for i, piece in enumerate(pieces, 1):
            plan.append((s["name"], i, len(pieces), piece))
    return plan


def validate_scan(obj):
    """Return (list of {quote, relation}, None) or (None, reason)."""
    if not isinstance(obj, dict):
        return None, "response is not a JSON object"
    found = obj.get("found")
    if found is None or not isinstance(found, list):
        return None, "'found' missing or not a list"
    out = []
    for item in found[:MAX_SCAN_QUOTES]:
        if not isinstance(item, dict):
            return None, f"bad entry in 'found': {item!r}"
        quote = item.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            continue
        relation = item.get("relation")
        if relation not in VALID_RELATIONS:
            relation = "about"
        out.append({"quote": quote.strip(), "relation": relation})
    return out, None


def scan_piece(client, scan_template, row, source_name, piece_no, n_pieces,
               piece_text, model):
    """One scan call over one piece. Returns (candidates, None) or
    (None, reason) — a refused or malformed piece is a failure, never an
    empty result, because 'nothing found here' and 'never read' must not look
    the same to the judging step."""
    prompt = (scan_template
              .replace("{CLAIM}", row["claim_text"])
              .replace("{SOURCE_NAME}", source_name)
              .replace("{PIECE_NO}", str(piece_no))
              .replace("{N_PIECES}", str(n_pieces))
              .replace("{PIECE}", piece_text))
    from modules.papertrail.llm_client import extract_json
    last_reason = "call refused or failed (client returned nothing)"
    for _ in range(SCAN_ATTEMPTS):
        raw = client.call(prompt, temperature=0.0, max_output_tokens=4000,
                          purpose="benchmark_labeler_scan",
                          claim_id=f"{row['row_id']}#p{piece_no}")
        if raw is None:
            continue
        found, err = validate_scan(extract_json(raw))
        if found is None:
            last_reason = f"malformed scan answer: {err}"
            continue
        kept = []
        for item in found:
            if quote_in_sources(item["quote"], [{"name": source_name,
                                                 "text": piece_text}]):
                kept.append({**item, "source": source_name,
                             "piece": piece_no})
        return {"candidates": kept, "returned": len(found)}, None
    return None, last_reason


def sectioned_judge(client, template, scan_template, row, model,
                    piece_chars=DEFAULT_PIECE_CHARS,
                    overlap_chars=DEFAULT_PIECE_OVERLAP,
                    progress=None):
    """Judge a row whose source is too long to read whole, by reading the
    source in numbered pieces first. Returns the verdict fields to merge into
    the output record, or a no-answer record when any piece could not be read.
    """
    plan = plan_pieces(row, piece_chars, overlap_chars)
    stats = {"read_mode": "sectioned", "pieces": len(plan),
             "scan_calls": 0, "scan_quotes_returned": 0,
             "scan_quotes_kept": 0, "piece_chars": piece_chars,
             "piece_overlap": overlap_chars}
    by_source = {}
    for source_name, piece_no, n_pieces, piece_text in plan:
        result, reason = scan_piece(client, scan_template, row, source_name,
                                    piece_no, n_pieces, piece_text, model)
        stats["scan_calls"] += 1
        if result is None:
            # One unread piece means the judgment would rest on part of the
            # evidence. That is exactly what this harness refuses to do.
            return {**stats, "answered": False,
                    "reason": (f"sectioned read incomplete: piece {piece_no} of "
                               f"{n_pieces} of {source_name} was never read "
                               f"({reason}) — no verdict on part of a source")}
        stats["scan_quotes_returned"] += result["returned"]
        stats["scan_quotes_kept"] += len(result["candidates"])
        by_source.setdefault(source_name, []).extend(result["candidates"])
        if progress:
            progress(f"  {model} {row['row_id']} piece {piece_no}/{n_pieces} "
                     f"of {source_name}: {len(result['candidates'])} sentences kept")

    digest_sources = []
    for s in row["sources"]:
        name = s["name"]
        n_pieces = len(split_into_pieces(s["text"], piece_chars, overlap_chars))
        seen, lines = set(), []
        for c in by_source.get(name, []):
            key = normalize(c["quote"])
            if key in seen:
                continue
            seen.add(key)
            lines.append(c["quote"])
        body = "\n\n".join(lines) if lines else \
            "(the reading pass found no sentence in this source that bears on the claim)"
        digest_sources.append({
            "name": name,
            "text": DIGEST_HEADER.format(n_pieces=n_pieces) + "\n\n" + body})
    stats["digest_chars"] = sum(len(d["text"]) for d in digest_sources)

    digest_row = dict(row, sources=digest_sources)
    prompt = build_prompt(template, digest_row)
    from modules.papertrail.llm_client import extract_json
    last_reason = "no response from the model"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        raw = client.call(prompt, temperature=0.1, max_output_tokens=8000,
                          purpose="benchmark_labeler_panel_sectioned",
                          claim_id=row["row_id"])
        if raw is None:
            return {**stats, "answered": False,
                    "reason": "call refused or failed (client returned nothing)"}
        verdict, err = validate_verdict(extract_json(raw))
        if verdict is not None:
            # Quotes are still checked against the FULL original source, not
            # against the digest, so a quote invented during the digest step
            # cannot pass here either.
            for p in verdict["parts"]:
                p["quote_verified_in"] = quote_in_sources(p["quote"], row["sources"])
                p["quote_verified"] = p["quote_verified_in"] is not None
            return {**stats, "answered": True, **verdict, "attempts": attempt}
        last_reason = f"malformed answer: {err}"
    return {**stats, "answered": False, "reason": last_reason}


# ---------- piece-read mode ----------
#
# Why this mode exists, in one worked example. Row retreat:b039 claims a 2026
# study of PLOS research articles found a data reuse rate of 43 percent. Read
# whole, the free Google model called the claim true as written. Read with the
# scan-then-judge mode of round 1.5, the same model on the same source said the
# source is silent on part of the claim — because the scanning pass, whose job
# is to copy out the sentences that bear on the claim, did not copy the
# sentence carrying the figure, and the judging step never saw it. The loss was
# in the summarising step, not in the cutting: that source is one piece, so no
# cut happened at all.
#
# Piece-read mode removes the summarising step. The claim is split into parts
# once, and then every numbered piece is asked the rubric's own question about
# those same parts directly. A part is proven when any piece proves it with a
# quote found word for word in that piece, contradicted when any piece
# contradicts it, and only "the source is silent" when every piece answered and
# none of them found anything. Cost is the same as scan-then-judge: one call
# per piece plus one, so 29 calls for the 1,036,530-character doctoral thesis.


def _parts_block(parts):
    return "\n".join(f"{i}. {p}" for i, p in enumerate(parts, 1))


def validate_split(obj):
    """Return (list of part strings, None) or (None, reason)."""
    if not isinstance(obj, dict):
        return None, "response is not a JSON object"
    parts = obj.get("parts")
    if not isinstance(parts, list) or not parts:
        return None, "'parts' missing or empty"
    out = []
    for p in parts[:MAX_PARTS]:
        if isinstance(p, str) and p.strip():
            out.append(p.strip())
    if not out:
        return None, "no usable part text in 'parts'"
    return out, None


def split_claim_parts(client, split_template, row):
    """One call, no source text: the claim's checkable parts. Returns
    (parts, None) or (None, reason) — a failed split is a no-answer for the
    row, never a guessed single part, because judging one blob is exactly what
    the rubric forbids."""
    prompt = (split_template
              .replace("{CLAIM}", row["claim_text"])
              .replace("{CONTEXT}", row.get("context") or "(none provided)"))
    from modules.papertrail.llm_client import extract_json
    last_reason = "call refused or failed (client returned nothing)"
    for _ in range(MAX_ATTEMPTS):
        raw = client.call(prompt, temperature=0.0, max_output_tokens=2000,
                          purpose="benchmark_labeler_split",
                          claim_id=row["row_id"])
        if raw is None:
            continue
        parts, err = validate_split(extract_json(raw))
        if parts is not None:
            return parts, None
        last_reason = f"malformed split answer: {err}"
    return None, last_reason


def validate_piece_read(obj, n_parts):
    """Return ({part number: {verdict, quote}}, None) or (None, reason).
    A missing part is a failure: a piece that answered only some parts has not
    been read for the others, and that must not look like 'nothing here'."""
    if not isinstance(obj, dict):
        return None, "response is not a JSON object"
    answers = obj.get("answers")
    if not isinstance(answers, list):
        return None, "'answers' missing or not a list"
    out = {}
    for item in answers:
        if not isinstance(item, dict):
            return None, f"bad entry in 'answers': {item!r}"
        try:
            no = int(item.get("part_no"))
        except (TypeError, ValueError):
            return None, f"part_no is not a number: {item.get('part_no')!r}"
        if not 1 <= no <= n_parts:
            return None, f"part_no {no} is outside 1..{n_parts}"
        verdict = item.get("verdict")
        if verdict not in VALID_PIECE_VERDICTS:
            return None, f"verdict {verdict!r} not one of {sorted(VALID_PIECE_VERDICTS)}"
        quote = item.get("quote")
        out[no] = {"verdict": verdict,
                   "quote": quote.strip() if isinstance(quote, str) and quote.strip()
                   else None}
    missing = [n for n in range(1, n_parts + 1) if n not in out]
    if missing:
        return None, (f"parts {', '.join(str(m) for m in missing)} of {n_parts} "
                      f"were not answered")
    return out, None


def read_piece(client, piece_template, row, parts, source_name, piece_no,
               n_pieces, piece_text):
    """One call asking the rubric's question about every part against ONE
    piece. Returns (answers, None) or (None, reason). Every quote is checked
    word for word against this piece; a quote that is not there is dropped and
    the part falls back to 'not stated in this piece', so an invented proof can
    never survive."""
    prompt = (piece_template
              .replace("{CLAIM}", row["claim_text"])
              .replace("{PARTS}", _parts_block(parts))
              .replace("{SOURCE_NAME}", source_name)
              .replace("{PIECE_NO}", str(piece_no))
              .replace("{N_PIECES}", str(n_pieces))
              .replace("{PIECE}", piece_text))
    from modules.papertrail.llm_client import extract_json
    last_reason = "call refused or failed (client returned nothing)"
    for _ in range(SCAN_ATTEMPTS):
        raw = client.call(prompt, temperature=0.0, max_output_tokens=4000,
                          purpose="benchmark_labeler_piece_read",
                          claim_id=f"{row['row_id']}#p{piece_no}")
        if raw is None:
            continue
        answers, err = validate_piece_read(extract_json(raw), len(parts))
        if answers is None:
            last_reason = f"malformed piece answer: {err}"
            continue
        dropped = 0
        for no, ans in answers.items():
            if ans["verdict"] in ("proven", "contradicted", "tolerated"):
                if not quote_in_sources(ans["quote"], [{"name": source_name,
                                                        "text": piece_text}]):
                    dropped += 1
                    ans["verdict"] = "not_stated_here"
                    ans["quote_dropped"] = True
                    ans["quote"] = None
        return {"answers": answers, "quotes_dropped": dropped}, None
    return None, last_reason


# Which answer wins when different pieces say different things about one part.
# A contradiction is the most serious finding and is kept even when another
# piece proves the part, exactly as the rubric's own label table does; a proven
# part beats a tolerated one because it rests on a literal sentence; silence
# only wins when nothing was found anywhere.
_MERGE_ORDER = ["contradicted", "proven", "tolerated", "not_stated_here"]


def merge_piece_answers(parts, per_piece):
    """Put every piece's answers together into the rubric's four classes.

    `per_piece` is a list of (source name, piece number, {part number: answer}).
    Returns the rubric-shaped parts list. A part nothing found anywhere becomes
    "unproven", which is honest ONLY because the caller guarantees every piece
    was read."""
    merged = []
    for i, text in enumerate(parts, 1):
        best, best_rank = None, len(_MERGE_ORDER)
        for source_name, piece_no, answers in per_piece:
            ans = answers.get(i)
            if not ans:
                continue
            rank = _MERGE_ORDER.index(ans["verdict"])
            if rank < best_rank:
                best, best_rank = (source_name, piece_no, ans), rank
        if best is None or best[2]["verdict"] == "not_stated_here":
            merged.append({"part": text, "classification": "unproven",
                           "quote": None, "source": None, "found_in_piece": None})
            continue
        source_name, piece_no, ans = best
        merged.append({"part": text, "classification": ans["verdict"],
                       "quote": ans["quote"], "source": source_name,
                       "found_in_piece": piece_no})
    return merged


def piece_read_judge(client, split_template, piece_template, row, model,
                     piece_chars=DEFAULT_PIECE_CHARS,
                     overlap_chars=DEFAULT_PIECE_OVERLAP,
                     progress=None):
    """Judge a row whose source does not fit one call by asking every numbered
    piece the rubric's own question about the same fixed list of claim parts.
    Returns the verdict fields to merge into the output record, or a no-answer
    record when the split failed or any piece could not be read."""
    stats = {"read_mode": "piece_read", "piece_chars": piece_chars,
             "piece_overlap": overlap_chars, "split_calls": 1,
             "piece_read_calls": 0, "quotes_dropped": 0}
    parts, reason = split_claim_parts(client, split_template, row)
    if parts is None:
        return {**stats, "pieces": 0, "answered": False,
                "reason": f"the claim could not be split into parts ({reason}) — "
                          "no verdict on an unsplit claim"}
    stats["parts_asked"] = parts

    plan = []
    for s in row["sources"]:
        numbered, _p = long_source.plan_pieces(
            s["text"], claim=row["claim_text"], piece_chars=piece_chars,
            overlap_chars=overlap_chars, relevance_order=True)
        for piece_no, piece_text in numbered:
            plan.append((s["name"], piece_no, len(numbered), piece_text))
    stats["pieces"] = len(plan)

    per_piece = []
    for source_name, piece_no, n_pieces, piece_text in plan:
        result, reason = read_piece(client, piece_template, row, parts,
                                    source_name, piece_no, n_pieces, piece_text)
        stats["piece_read_calls"] += 1
        if result is None:
            # One unread piece means "the source is silent" would rest on part
            # of the evidence. That is exactly what this harness refuses.
            return {**stats, "answered": False,
                    "reason": (f"piece-by-piece read incomplete: piece {piece_no} "
                               f"of {n_pieces} of {source_name} was never read "
                               f"({reason}) — no verdict on part of a source")}
        stats["quotes_dropped"] += result["quotes_dropped"]
        per_piece.append((source_name, piece_no, result["answers"]))
        if progress:
            found = sum(1 for a in result["answers"].values()
                        if a["verdict"] != "not_stated_here")
            progress(f"  {model} {row['row_id']} piece {piece_no}/{n_pieces} "
                     f"of {source_name}: {found} of {len(parts)} parts answered "
                     f"with a finding")

    merged = merge_piece_answers(parts, per_piece)
    # Quotes are re-checked against the WHOLE original source, not against the
    # piece, so a quote that only looked right inside one piece cannot pass.
    for p in merged:
        p["quote_verified_in"] = quote_in_sources(p["quote"], row["sources"])
        p["quote_verified"] = p["quote_verified_in"] is not None
    from benchmarks.labeler.funnel import label_from_parts  # one copy of the arithmetic
    label = label_from_parts(merged)
    if label is None:
        return {**stats, "answered": False,
                "reason": "the merged parts do not force a label under rubric v1"}
    return {**stats, "answered": True, "strict_label": label, "parts": merged,
            "hard_note": (f"read piece by piece: {stats['pieces']} pieces of "
                          f"{piece_chars:,} characters, every piece read, so "
                          f"'the source is silent' rests on the whole source"),
            "silence_trustworthy": True, "attempts": 1}


def judge_one(client, template, row, model,
              max_prompt_chars=DEFAULT_MAX_PROMPT_CHARS,
              sectioned=None, scan_template=None, progress=None,
              piece_read=None, split_template=None, piece_template=None):
    """One (row, model) judgment. Returns the output record (always — a
    refusal becomes answered=False, never a verdict).

    `sectioned`, when given, is a dict {"piece_chars": int, "overlap": int}:
    a row over the cap is then read in numbered pieces instead of becoming a
    no-answer. Without it the behaviour is unchanged.

    `piece_read`, the same shape, chooses the piece-read mode instead: every
    numbered piece answers the rubric's own question about one fixed list of
    claim parts, with no summarising step in between. When both are given,
    piece_read wins, because it is the stronger reading of the two."""
    prompt = build_prompt(template, row)
    base = {"row_id": row["row_id"], "model": model,
            "rubric_version": RUBRIC_VERSION, "prompt_chars": len(prompt),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if max_prompt_chars and len(prompt) > max_prompt_chars:
        if piece_read:
            if split_template is None:
                with open(SPLIT_PROMPT_PATH) as f:
                    split_template = f.read()
            if piece_template is None:
                with open(PIECE_READ_PROMPT_PATH) as f:
                    piece_template = f.read()
            return {**base, **piece_read_judge(
                client, split_template, piece_template, row, model,
                piece_chars=piece_read.get("piece_chars", DEFAULT_PIECE_CHARS),
                overlap_chars=piece_read.get("overlap", DEFAULT_PIECE_OVERLAP),
                progress=progress)}
        if sectioned:
            if scan_template is None:
                with open(SCAN_PROMPT_PATH) as f:
                    scan_template = f.read()
            return {**base, **sectioned_judge(
                client, template, scan_template, row, model,
                piece_chars=sectioned.get("piece_chars", DEFAULT_PIECE_CHARS),
                overlap_chars=sectioned.get("overlap", DEFAULT_PIECE_OVERLAP),
                progress=progress)}
        return {**base, "read_mode": "whole_source", "answered": False,
                "reason": (f"source too long: prompt is {len(prompt):,} characters, "
                           f"over the {max_prompt_chars:,} cap — never truncated, "
                           "needs a longer-context model or a human plan")}
    base["read_mode"] = "whole_source"
    last_reason = "no response from the model"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        raw = client.call(prompt, temperature=0.1, max_output_tokens=8000,
                          purpose="benchmark_labeler_panel", claim_id=row["row_id"])
        if raw is None:
            # A refused or failed call is "no answer" — final, no retry here:
            # llm_client already retried transport errors internally.
            return {**base, "answered": False,
                    "reason": "call refused or failed (client returned nothing)"}
        from modules.papertrail.llm_client import extract_json
        verdict, err = validate_verdict(extract_json(raw))
        if verdict is not None:
            for p in verdict["parts"]:
                p["quote_verified_in"] = quote_in_sources(p["quote"], row["sources"])
                p["quote_verified"] = p["quote_verified_in"] is not None
            return {**base, "answered": True, **verdict,
                    "attempts": attempt}
        last_reason = f"malformed answer: {err}"
    return {**base, "answered": False, "reason": last_reason}


def load_rows(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def filter_rows(rows, row_ids):
    """Keep only the named rows, in the file's own order. An id that matches
    nothing is an error rather than a silently smaller run."""
    wanted = [r.strip() for r in row_ids if r.strip()]
    kept = [row for row in rows if row["row_id"] in wanted]
    missing = sorted(set(wanted) - {row["row_id"] for row in kept})
    if missing:
        raise SystemExit(f"--only names rows that are not in the rows file: "
                         f"{', '.join(missing)}")
    return kept


def load_done(out_path):
    done = set()
    if os.path.exists(out_path):
        with open(out_path) as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    done.add((rec["row_id"], rec["model"]))
    return done


def run_panel(rows, models, out_path, client_factory=None, template=None,
              progress=print, max_prompt_chars=DEFAULT_MAX_PROMPT_CHARS,
              sectioned=None, scan_template=None, piece_read=None,
              split_template=None, piece_template=None):
    """Core loop, injectable for offline tests: client_factory(model) must
    return an object with .call(prompt, ..., purpose=, claim_id=) -> str|None."""
    if template is None:
        with open(PROMPT_PATH) as f:
            template = f.read()
    if sectioned and scan_template is None:
        with open(SCAN_PROMPT_PATH) as f:
            scan_template = f.read()
    if piece_read and split_template is None:
        with open(SPLIT_PROMPT_PATH) as f:
            split_template = f.read()
    if piece_read and piece_template is None:
        with open(PIECE_READ_PROMPT_PATH) as f:
            piece_template = f.read()
    if client_factory is None:
        from modules.papertrail.llm_client import LLMClient

        def client_factory(m):
            # Provider key-file fallback (LLMClient's own fallback is
            # gemini-only; mirrors arbiter.resolve_key, plus openrouter):
            key_path = None
            for prefix, env, path in (
                    ("deepseek/", "DEEPSEEK_API_KEY",
                     os.path.join(REPO, "config", "deepseek_api_key.txt")),
                    ("openrouter/", "OPENROUTER_API_KEY",
                     os.path.join(REPO, "config", "openrouter_api_key.txt"))):
                if m.startswith(prefix) and not os.environ.get(env) \
                        and os.path.exists(path):
                    key_path = path
            return LLMClient(model=m, api_key=key_path)
    done = load_done(out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    counts = {"judged": 0, "refused": 0, "skipped": 0}
    with open(out_path, "a") as out:
        for model in models:
            client = client_factory(model)
            for row in rows:
                if (row["row_id"], model) in done:
                    counts["skipped"] += 1
                    continue
                rec = judge_one(client, template, row, model,
                                max_prompt_chars=max_prompt_chars,
                                sectioned=sectioned,
                                scan_template=scan_template,
                                progress=progress,
                                piece_read=piece_read,
                                split_template=split_template,
                                piece_template=piece_template)
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                if rec["answered"]:
                    counts["judged"] += 1
                    read_mode = rec.get("read_mode", "whole_source")
                    mode = "" if read_mode == "whole_source" \
                        else f" [{read_mode}, {rec.get('pieces')} pieces]"
                    progress(f"{model} {row['row_id']}: {rec['strict_label']}{mode}")
                else:
                    counts["refused"] += 1
                    progress(f"{model} {row['row_id']}: NO ANSWER ({rec['reason']})")
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True)
    ap.add_argument("--models", required=True,
                    help="comma-separated model names, one per panel seat")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="",
                    help="comma-separated row ids: judge only these rows")
    ap.add_argument("--max-prompt-chars", type=int,
                    default=DEFAULT_MAX_PROMPT_CHARS,
                    help="rows over this become no-answer, never truncated; 0 = no cap")
    ap.add_argument("--sectioned-when-too-long", action="store_true",
                    help="read an over-cap source in numbered pieces instead of "
                         "recording it as no-answer (opt-in; weaker than a "
                         "whole-source read and marked as such in the output)")
    ap.add_argument("--piece-read-when-too-long", action="store_true",
                    help="read an over-cap source piece by piece, asking every "
                         "piece the rubric's own question about one fixed list "
                         "of claim parts. No summarising step, so "
                         "'the source is silent' rests on the whole source. "
                         "Wins over --sectioned-when-too-long when both are given.")
    ap.add_argument("--piece-chars", type=int, default=DEFAULT_PIECE_CHARS,
                    help="characters per piece in sectioned reading")
    ap.add_argument("--piece-overlap", type=int, default=DEFAULT_PIECE_OVERLAP,
                    help="characters shared between neighbouring pieces")
    ap.add_argument("--dry-run", action="store_true",
                    help="build prompts, print sizes, call nothing")
    a = ap.parse_args()

    rows = load_rows(a.rows)
    if a.only:
        rows = filter_rows(rows, a.only.split(","))
    if a.limit:
        rows = rows[:a.limit]
    models = [m.strip() for m in a.models.split(",") if m.strip()]
    sectioned = {"piece_chars": a.piece_chars, "overlap": a.piece_overlap} \
        if a.sectioned_when_too_long else None
    piece_read = {"piece_chars": a.piece_chars, "overlap": a.piece_overlap} \
        if a.piece_read_when_too_long else None

    if a.dry_run:
        with open(PROMPT_PATH) as f:
            template = f.read()
        total = 0
        over = 0
        scan_calls = 0
        for row in rows:
            n = len(build_prompt(template, row))
            total += n
            line = f"{row['row_id']}: prompt {n:,} chars (~{n // 4:,} tokens)"
            if a.max_prompt_chars and n > a.max_prompt_chars:
                over += 1
                if piece_read:
                    pieces = len(plan_pieces(row, a.piece_chars, a.piece_overlap))
                    scan_calls += pieces
                    line += (f" — OVER CAP, piece-by-piece read: {pieces} pieces "
                             f"+ 1 claim-splitting call")
                elif sectioned:
                    pieces = len(plan_pieces(row, a.piece_chars, a.piece_overlap))
                    scan_calls += pieces
                    line += (f" — OVER CAP, sectioned read: {pieces} pieces "
                             f"+ 1 judging call")
                else:
                    line += " — OVER CAP, would be recorded as no-answer"
            print(line)
        print(f"TOTAL per model: {total:,} chars (~{total // 4:,} tokens); "
              f"x {len(models)} models = ~{total * len(models) // 4:,} input tokens")
        print(f"{over} of {len(rows)} rows are over the "
              f"{a.max_prompt_chars:,}-character cap.")
        if piece_read:
            print(f"Piece-by-piece reading of those rows costs {scan_calls} piece "
                  f"calls + {over} claim-splitting calls per model "
                  f"({scan_calls + over} calls, ~{a.piece_chars // 4:,} tokens each "
                  "for the piece calls).")
        elif sectioned:
            print(f"Sectioned reading of those rows costs {scan_calls} scan calls "
                  f"+ {over} judging calls per model "
                  f"({scan_calls + over} calls, ~{a.piece_chars // 4:,} tokens each "
                  "for the scans).")
        return 0

    counts = run_panel(rows, models, a.out,
                       max_prompt_chars=a.max_prompt_chars,
                       sectioned=sectioned, piece_read=piece_read)
    print(f"done: {counts['judged']} judged, {counts['refused']} NO-ANSWER, "
          f"{counts['skipped']} already present (resumed)")
    if counts["refused"]:
        print("WARNING: refusals above are recorded as no-answer, not as verdicts; "
              "check them before quoting any number from this run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
