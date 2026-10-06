"""One place that answers "this source does not fit one model call" (task #105).

The problem in one worked example. The doctoral thesis `hukantaival2016.pdf` is
1,036,530 characters. The free Google seat refuses any request over about
52,000 characters (llm_client._FREE_TIER_MAX_PROMPT_CHARS, measured live
2026-08-08), so the whole thesis can never be one call there. It is cut into 28
numbered pieces of 40,000 characters that share 2,000 characters with their
neighbours, and every piece is read. 28 pieces plus one claim-splitting call is
29 calls, all on the free quota, so the money cost is zero and the cost is time.

The general rule: a source that does not fit one call is read in numbered
overlapping pieces, EVERY piece is read, and "the source never says this" is
only reported when every piece answered. A finding (a proven or contradicted
part with a verbatim quote) is trustworthy from any single piece, because the
quote is checked word for word against the piece it came from. Silence is only
trustworthy when the whole source was covered.

Two callers share this module, which is why it exists at all:
 - benchmarks/labeler/panel_runner.py (the task #15 labeling panel), whose
   piece geometry was measured in round 1.5 and must not move: the default
   piece size and overlap here are byte-identical to the constants it used.
 - modules/papertrail/arbiter.py, which sends a large slice of the source to a
   second model and, before this module, sized that slice in words with no
   regard for the seat's real character ceiling — so on the free seat every
   long-source arbiter call was skipped without being sent.

Nothing here calls a model or touches the network.
"""
from typing import Any, Dict, List, Optional, Tuple

# Piece geometry. The default piece size is not a free choice: it is the free
# Google seat's 52,000-character ceiling minus the room a prompt needs for its
# instructions, the claim and the surrounding text (measured: the labeling
# panel's rubric prompt adds about 7,000 characters on top of the source, so
# 12,000 is that with margin). 52,000 - 12,000 = 40,000, which is exactly the
# piece size round 1.5 of task #15 measured, so results carry over unchanged.
DEFAULT_PIECE_CHARS = 40_000
DEFAULT_PIECE_OVERLAP = 2_000
DEFAULT_PROMPT_ROOM = 12_000
# A piece shorter than this is not worth its own call; below it the overlap
# would be most of the piece.
MIN_PIECE_CHARS = 4_000
# Above this many pieces a piece-by-piece read is not offered: with the free
# seat's pacing it would take longer than a night, and the largest source this
# project owns (1,294,440 characters) is 35 pieces, so the budget is not
# reached by anything on disk today.
DEFAULT_MAX_PIECES = 40

# The three answers size_class can give.
CLASS_ONE_CALL = "one_call"
CLASS_PIECES = "pieces"
CLASS_OVER_BUDGET = "over_budget"


def call_limit_for(client: Any) -> Optional[int]:
    """The largest prompt this client will actually send, in characters, or
    None when it has no known ceiling. Never guesses: it asks the client."""
    getter = getattr(client, "max_prompt_chars", None)
    if callable(getter):
        try:
            return getter()
        except Exception:      # a client stub without the method is fine
            return None
    if isinstance(getter, int) and getter > 0:
        return getter
    return None


def piece_size_for(call_limit: Optional[int],
                   prompt_room: int = DEFAULT_PROMPT_ROOM,
                   piece_chars: int = DEFAULT_PIECE_CHARS) -> int:
    """How many characters of source text one call may carry. With no known
    ceiling the caller's default piece size stands."""
    if not call_limit or call_limit <= 0:
        return piece_chars
    room = max(0, call_limit - prompt_room)
    return max(MIN_PIECE_CHARS, min(piece_chars, room))


def fits_one_call(n_chars: int, call_limit: Optional[int],
                  prompt_room: int = DEFAULT_PROMPT_ROOM) -> bool:
    """True when a source of this many characters, plus the room a prompt needs
    around it, is inside the seat's ceiling."""
    if not call_limit or call_limit <= 0:
        return True
    return n_chars + prompt_room <= call_limit


def piece_count(n_chars: int, piece_chars: int = DEFAULT_PIECE_CHARS,
                overlap_chars: int = DEFAULT_PIECE_OVERLAP) -> int:
    """How many pieces a source of this length is cut into. Counts without
    building the pieces, so a plan can be priced without holding the text."""
    if piece_chars <= 0:
        raise ValueError("piece_chars must be positive")
    overlap_chars = max(0, min(overlap_chars, piece_chars - 1))
    if n_chars <= piece_chars:
        return 1
    stride = piece_chars - overlap_chars
    k, start = 1, 0
    while start + piece_chars < n_chars:
        start += stride
        k += 1
    return k


def split_into_pieces(text: str, piece_chars: int = DEFAULT_PIECE_CHARS,
                      overlap_chars: int = DEFAULT_PIECE_OVERLAP) -> List[str]:
    """Cut a long source into overlapping pieces, in order.

    Deterministic and lossless: piece k starts `piece_chars - overlap_chars`
    after piece k-1 started, so every character of the source is inside at
    least one piece, and any sentence shorter than the overlap survives whole
    in at least one piece even when a cut falls inside it."""
    if piece_chars <= 0:
        raise ValueError("piece_chars must be positive")
    overlap_chars = max(0, min(overlap_chars, piece_chars - 1))
    if len(text) <= piece_chars:
        return [text]
    stride = piece_chars - overlap_chars
    pieces, start = [], 0
    while start < len(text):
        pieces.append(text[start:start + piece_chars])
        if start + piece_chars >= len(text):
            break
        start += stride
    return pieces


def size_class(n_chars: int, call_limit: Optional[int],
               piece_chars: int = DEFAULT_PIECE_CHARS,
               overlap_chars: int = DEFAULT_PIECE_OVERLAP,
               max_pieces: int = DEFAULT_MAX_PIECES,
               prompt_room: int = DEFAULT_PROMPT_ROOM) -> str:
    """Which of the three reading situations this source is in."""
    if fits_one_call(n_chars, call_limit, prompt_room):
        return CLASS_ONE_CALL
    size = piece_size_for(call_limit, prompt_room, piece_chars)
    if piece_count(n_chars, size, overlap_chars) <= max_pieces:
        return CLASS_PIECES
    return CLASS_OVER_BUDGET


def read_plan(n_chars: int, call_limit: Optional[int],
              piece_chars: int = DEFAULT_PIECE_CHARS,
              overlap_chars: int = DEFAULT_PIECE_OVERLAP,
              max_pieces: int = DEFAULT_MAX_PIECES,
              prompt_room: int = DEFAULT_PROMPT_ROOM) -> Dict[str, Any]:
    """How this source will be read, and what that costs in calls.

    `silence_trustworthy` is the field that matters downstream: it is True only
    when the plan reads every piece, because an answer of "the source never
    says this" from a partial read may only mean the proof was in a piece
    nobody read."""
    size = piece_size_for(call_limit, prompt_room, piece_chars)
    cls = size_class(n_chars, call_limit, piece_chars, overlap_chars,
                     max_pieces, prompt_room)
    if cls == CLASS_ONE_CALL:
        return {"size_class": cls, "source_chars": n_chars, "pieces": 1,
                "piece_chars": n_chars, "piece_overlap": 0,
                "calls": 1, "reads_whole_source": True,
                "silence_trustworthy": True,
                "call_limit": call_limit}
    pieces = piece_count(n_chars, size, overlap_chars)
    return {"size_class": cls, "source_chars": n_chars, "pieces": pieces,
            "piece_chars": size, "piece_overlap": overlap_chars,
            # one call per piece plus one call that splits the claim into parts
            "calls": pieces + 1,
            "reads_whole_source": cls == CLASS_PIECES,
            "silence_trustworthy": cls == CLASS_PIECES,
            "call_limit": call_limit,
            "max_pieces": max_pieces}


def rank_piece_indices(claim: str, pieces: List[str]) -> List[int]:
    """Piece positions ordered most-relevant first, by the project's one
    lexical relevance formula (IDF-weighted overlap of the claim's own words,
    matcher._lex_scores). Reading order only: every piece is still read, but a
    run cut short by a crash or a quota has then seen the best pieces first.
    Ties keep document order, so the ordering is deterministic."""
    if not pieces:
        return []
    from . import matcher      # imported here: keeps this module import-light
    scores = matcher._lex_scores(claim, pieces)
    return sorted(range(len(pieces)), key=lambda i: (-scores[i], i))


def plan_pieces(text: str, claim: str = "", call_limit: Optional[int] = None,
                piece_chars: int = DEFAULT_PIECE_CHARS,
                overlap_chars: int = DEFAULT_PIECE_OVERLAP,
                prompt_room: int = DEFAULT_PROMPT_ROOM,
                relevance_order: bool = False
                ) -> Tuple[List[Tuple[int, str]], Dict[str, Any]]:
    """The numbered pieces of one source plus its plan.

    Returns [(piece number counting from 1, piece text)] and the read_plan
    dictionary. Piece numbers always describe the piece's place in the source,
    so a record stays readable whatever order the pieces were read in.

    A caller that names a piece size but no seat ceiling is taken at its word:
    the piece size it asked for becomes the operative limit, so asking for
    pieces of 5,000 characters really cuts at 5,000."""
    limit = call_limit if call_limit else piece_chars + prompt_room
    plan = read_plan(len(text), limit, piece_chars, overlap_chars,
                     prompt_room=prompt_room)
    pieces = split_into_pieces(text, plan["piece_chars"], plan["piece_overlap"]) \
        if plan["pieces"] > 1 else [text]
    numbered = list(enumerate(pieces, 1))
    if relevance_order and claim and len(numbered) > 1:
        order = rank_piece_indices(claim, pieces)
        numbered = [numbered[i] for i in order]
    return numbered, plan


def best_section(claim: str, texts: List[str], budget_chars: int,
                 chunks: Optional[List[Tuple[str, List[int]]]] = None) -> str:
    """The most claim-relevant contiguous stretch of a source that fits a
    character budget.

    Used where one call must carry as much of a long source as the seat allows
    and reading every piece is not on offer (the arbiter). Grows outwards from
    the single most lexically relevant chunk while the budget holds, so the
    result is one continuous stretch of the source rather than a scatter."""
    joined = " ".join(t for t in texts if t)
    if budget_chars <= 0 or len(joined) <= budget_chars:
        return joined
    if not chunks:
        return joined[:budget_chars]
    from . import matcher
    lex = matcher._lex_scores(claim, [ch[0] for ch in chunks])
    best = max(range(len(chunks)), key=lambda i: (lex[i], -i))
    lo = hi = best
    used = len(chunks[best][0])
    while lo > 0 or hi < len(chunks) - 1:
        grew = False
        if lo > 0 and used + len(chunks[lo - 1][0]) + 1 <= budget_chars:
            lo -= 1
            used += len(chunks[lo][0]) + 1
            grew = True
        if hi < len(chunks) - 1 and used + len(chunks[hi + 1][0]) + 1 <= budget_chars:
            hi += 1
            used += len(chunks[hi][0]) + 1
            grew = True
        if not grew:
            break
    return " ".join(ch[0] for ch in chunks[lo:hi + 1])[:budget_chars]
