# Card 390: labelling the pieces of a wrong claim (decide step, then check step)

Each input line is one benchmark claim whose whole-claim label is not_supported. It holds: the claim, the old label's kind (contradicted / differs_in_detail / unproven), the old note, the old checker's reason, the old parts (each part of the claim with its classification "proven" / "differs_in_detail" / "contradicted" / "unproven" and a verbatim quote from the source), the source file paths, and the pieces the claim was cut into.

The old label was made by a decider model and checked by a second model reading the whole source; it is the evidence. The pieces are a finer cut of the same claim.

## Decide step (output file `label_NN.decide.jsonl`)

For every piece, one JSON line: {"piece_id", "label", "kind", "parts_used", "quote", "reason"}.
- label "supported": the old parts this piece restates are all "proven", and the piece adds nothing the quotes do not cover.
- label "not_supported": the piece restates at least one part classified differs_in_detail, contradicted or unproven. kind = the worst of those (contradicted > differs_in_detail > unproven).
- label "unsure": the piece restates something no old part covers, or the parts do not settle it. Never guess.
- parts_used: the old part texts the piece restates. quote: the old quote that decides it (or null). reason: one or two plain sentences saying why.
- Rules of the old labels hold: a tighter number than the source is differs_in_detail; who made the source is not a part.
- Certainty (corrected 13 Nov after the first decide run, rule 3 of the card 389 table): the cutter took the writer's hedge word out of the piece text and stored it in the piece's "certainty" ("stated", "likely", "possible") and "certainty_cue". Judge the piece AT that certainty. A piece with certainty "possible" or "likely" is supported when the source says it is possible (or more). Only a piece with certainty "stated" is differs_in_detail when the source only says "may", "could", "might" or "suggests".
You MAY open a source file (Read with offset/limit, or Grep for a few words of the piece) when the parts do not settle a piece; say so in the reason.

## Check step (output file `label_NN.check.jsonl`)

Read the decide file and the input. For every piece, one JSON line: {"piece_id", "reason_holds", "own_label", "problem"}.
- Whether each quote is really in the source is checked by a program (check_quotes in merge_labels.py), not by you; you need no search tool.
- Check the decider's reason: do the old parts and the quote say what the reason claims, does the piece really restate the parts named, and does the label follow the rules above, the certainty rule included (the decide run was made before the certainty rule was corrected, so a hedged piece labelled not_supported only because the source hedges is a disagreement)?
- reason_holds true/false; own_label = your own label (supported / not_supported / unsure); problem = one sentence when you disagree, else "".
