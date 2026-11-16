# Card 390: how to cut one benchmark claim into pieces (the answer key's method)

Source of the method: docs/CONVERSION_RECORD_FORMAT_2026-09-03.md (rules A to G), the method of the answer key data/conversion_key/key_local.jsonl. This is the short form for the cutting helpers.

Input line: {"row_id", "claim", "text_before", "cited": [source names]}. `claim` is the writer's text carrying the citation (one or more sentences). `text_before` is the writer's text just before it (may be empty), used ONLY to fill in a pointer word.

Output: one JSON line per input line, same order, same row_id:
{"row_id": ..., "entry_type": "converted" | "ambiguous" | "cannot_be_converted",
 "reason": "<only for cannot_be_converted / ambiguous: one sentence>",
 "readings": [ {"label": "<only when ambiguous: what this reading means>",
                "pieces": [ {"text", "certainty", "certainty_cue", "attribution", "attribution_open",
                             "cited_key", "covers", "kind", "needs_computation", "leans_on_earlier",
                             "filled_from_before"} ] } ]}

Rules for a piece:
1. One declarative sentence ending in a full stop; one checkable claim. Split a sentence at every fact a source could confirm separately ("X is cheap, fast and accurate" gives three pieces). A cause-and-effect thesis: each fact its own piece, plus one piece for the relation (rule A8).
2. Stand-alone: no pointer word (this, these, those, such, they, them, their, it when it points back, he, she, his, her, here, there, the former, the latter, the same). Replace the pointer with the thing it means, taken from the claim itself or from text_before. If you used text_before, set "leans_on_earlier": true and "filled_from_before": "<the words you took>". If the piece cannot be made to stand alone even with text_before, keep the writer's words, set "leans_on_earlier": true and "filled_from_before": "".
3. No hedge word inside the piece. Take it out and record it: certainty "stated" (no softening), "likely" (suggests, indicates, appears, probably, likely), "possible" (may, might, could, perhaps, possibly). certainty_cue = the word(s) from the claim, or "" when stated. Quantity and condition words stay inside ("most", "in mice", "about 30%", "can").
4. Keep identifying qualifiers (years in names, places, the population studied). Keep every number of the claim in some piece; if a number can only be checked by arithmetic (adding, a ratio, a percentage the source does not state), put it in the piece and write "needs_computation": "<what to compute>", else null.
5. attribution: "credited_to_cited_source" when the claim presents the piece as what the cited work found or said; "stated_by_writer" for the writer's own framing, opinion, plan, remark about themself or conclusion. "attribution_open": true when a fair reader could read it either way. When unsure between the two, prefer credited and set attribution_open true. A clause about the writer ("I am not aware", "I have not reviewed") is stated_by_writer.
6. cited_key: one of the row's "cited" names for a credited piece (the one that backs it; the first name if you cannot tell); "" for a writer's own piece.
7. covers: a list of exact substrings (copied character for character) of the claim that the piece restates.
8. kind: "fact" (a source could confirm or deny it), "opinion" (a judgement or recommendation), "pointer" (only sends the reader elsewhere: "see the appendix", "here is X's summary:"), "announcement" (says what the text will do). A sentence describing what the cited work does ("The book examines ...", "The study measured ...") is a FACT about that work, not an announcement.
9. Change no meaning. Do not judge whether the piece is true. Do not drop a part of the claim: cut everything, the door decides later what is skipped.
10. ambiguous: when the claim has two readings the words do not settle, give each reading's full set of pieces with a label. cannot_be_converted: only when there is no claim at all (a question, a bare heading), with a reason; then "readings": [].

Example (invented). Claim: "Although it may be slower than method B, method A is cheaper and gives the same accuracy." cited ["source01"] gives pieces:
- "Method A is slower than method B." certainty possible, cue "may", credited, kind fact.
- "Method A is cheaper than method B." stated, credited, fact.
- "Method A gives the same accuracy as method B." stated, credited, fact.
