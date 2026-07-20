"""
PaperTrail-style claim-grounding tool.

Verifies a user's own writing against the source documents it cites: for every
claim, surfaces the verbatim supporting sentence from the cited source, and flags
both unsupported claims and source claims the writing omitted.

Adapted from PaperTrail (arXiv:2602.21045): three-stage extract -> match pipeline
with a supported / unsupported / omitted verdict, using SPECTER embeddings for
matching (near-zero API cost) and an LLM tiebreak only for borderline cases.
"""
