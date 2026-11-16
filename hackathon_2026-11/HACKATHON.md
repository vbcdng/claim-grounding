# Hackathon page: sprint checker

Apart Research hackathon, November 2026. Work dates: 14 to 15 November 2026 (from the commit dates of the sprint checker folder).

## Task
Check claim pieces against their cited works with two branches, A (guards "supported") and B (guards "not supported").

## Models
- Branch A chain: Claude Haiku 5.5, Sonnet 5.5, Opus 5.5 (Claude Code subscription).
- Branch B chain: Gemma 4 31B, Jev, Claude Haiku 5.5, Sonnet 5.5, Opus 5.5.

## Benchmark (not published)
300 pieces: 150 supported, 150 not supported. Split into tuning (150) and held back (150), each 75 / 75.

## Results
Results are counts of claim pieces. "Wrongly passed" = an unsupported piece labelled supported; "wrongly rejected" = a supported piece labelled not supported; "undecided" = left for a person or a stronger model.

Held-back part (150 pieces, scored once after both chains were frozen; 95% Wilson ranges in brackets):

| Chain | Wrongly passed (of 75) | Wrongly rejected (of 75) | Undecided (of 150) |
| --- | --- | --- | --- |
| Claude Sonnet 5.5 alone | 20 (18.0%-37.6%) | 9 (6.4%-21.3%) | 8 |
| Branch A | 8 (5.5%-19.7%) | 9 (6.4%-21.3%) | 19 (3 failed calls) |
| Branch B | 19 (16.9%-36.2%) | 7 (4.6%-18.0%) | 20 (18 failed calls) |

Tuning part (150 pieces, the rules were written on these, so the numbers are optimistic): Sonnet alone 14 / 12 / 7; Branch A 4 / 12 / 19; Branch B 14 / 3 / 23 (Sonnet alone in branch B's run: 14 / 13 / 8).

Branch A cut wrongly passed pieces on the held-back part (8 against 20) at the cost of more undecided pieces. Branch B's tuning gain did not hold on the held-back part, most likely because its rules were fitted to the tuning pieces. The benchmark is small, so only Branch A's difference is close to clear.

## Links
- Main claim-grounding tool (needed to run this code): the root of this repository.
- Hackathon report: to be added.
