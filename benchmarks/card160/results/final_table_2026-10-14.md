# Stability study: the results table (14 October 2026, all eighteen runs)

This is the plain-language summary of the study's results. Every number in it comes from `interim_table_2026-10-14.md` and `.json` in this folder, which the command `./venv/bin/python3 benchmarks/card160/interim_table.py --date 2026-10-14` rebuilds from the run folders with the plan's own counting program (`stability_table.py`, unchanged). No model call was made to build either file.

## Words used in this document

- **Claim**: one sentence or passage of a test text that cites a source, such as "Glauconite exhibits higher sorption than bentonite [[source]]".
- **Verdict**: the checker's decision on a claim: "supported" (the cited source backs it; shown green) or "unsupported" (it does not; shown red).
- **Judge**: the language model that reads the claim and the source sentences and gives the verdict. Gemma is the judge the checker normally uses. "Paid Gemma" reaches it through a paid service (OpenRouter); "free Gemma" through Google's free service. Sonnet 5.5 and Haiku 5.5 are two Claude models used as second judges.
- **Run**: one pass of one judge over the six standard test texts (85 claims) and a seventh input of ten constructed hard sentences.
- **Arm**: one run in which exactly one thing was changed on purpose, such as the wording of the judge's instruction.
- **Changed verdict**: a claim that got a different verdict in the compared run than in the baseline run.
- **Accuracy**: how many of the 56 hand-checked claims (claims whose right verdict was settled by hand earlier) the run got right.
- **Failed call**: a request the model did not answer. The checker would store it as "unsupported", so the study re-asks such calls and leaves out any that still fail.

## A worked example first

In the essay text, claim 1 says that trained human forecasters hold their own against statistical models in geopolitical forecasting. Paid Gemma judged it "supported" in all three of its plain runs. When the judge's instruction was reworded with the same meaning, it judged it "unsupported". That is one changed verdict, in the direction "supported turned unsupported", and because all three plain runs agreed, the change is counted as moved by the arm rather than by chance.

## Every run was clean

For each of the eighteen runs, `benchmarks/card160/check_runs_clean.py` read every result file and every run log. No verdict came from a failed call in any run (0 in every result file), and every run log's "refused calls per run" block shows 0 for every text. One log of the confident-note run shows four texts with no result file: that was the first attempt, stopped when the paid balance ran out on 11 October; the second attempt on 13 October finished those four texts with 0 refused calls. The check's output is saved as `run_check_2026-10-14.json`.

## The table: the six standard test texts (85 claims)

"Supported turned unsupported" is counted out of the claims that were supported in the baseline run; "unsupported turned supported" out of those that were unsupported in it. The accuracy column is the compared run's own accuracy, out of 56. For the one-run arms, the baseline is paid Gemma's first run, and the column "changed only in this arm" counts the changed claims on which all three plain paid runs agreed with each other.

| What was compared | Verdicts that changed (of 85) | Supported turned unsupported | Unsupported turned supported | Changed only in this arm | Accuracy (of 56) |
|---|---|---|---|---|---|
| Paid Gemma, nothing changed: run 2 against run 1 | 2 (2.4%) | 1 of 56 | 1 of 29 | not applicable | 54 |
| Paid Gemma, nothing changed: run 3 against run 1 | 2 (2.4%) | 0 of 56 | 2 of 29 | not applicable | 52 |
| Paid Gemma, nothing changed: run 3 against run 2 | 2 (2.4%) | 0 of 56 | 2 of 29 | not applicable | 52 |
| Free Gemma, nothing changed: each of its three pairs | 0 (0.0%) | 0 of 56 | 0 of 29 | not applicable | 53 in every run |
| Sonnet 5.5, nothing changed: run 2 against run 1 | 3 (3.5%) | 1 of 42 | 2 of 43 | not applicable | 52 |
| Sonnet 5.5, nothing changed: run 3 against run 1 | 3 (3.5%) | 3 of 42 | 0 of 43 | not applicable | 48 |
| Sonnet 5.5, nothing changed: run 3 against run 2 | 4 (4.7%) | 4 of 43 | 0 of 42 | not applicable | 48 |
| Haiku 5.5, nothing changed: run 2 against run 1 | 3 (3.5%) | 3 of 41 | 0 of 44 | not applicable | 49 |
| Haiku 5.5, nothing changed: run 3 against run 1 | 3 (3.5%) | 2 of 41 | 1 of 44 | not applicable | 50 |
| Haiku 5.5, nothing changed: run 3 against run 2 | 2 (2.4%) | 0 of 38 | 2 of 47 | not applicable | 50 |
| Free Gemma's first run against paid Gemma's first run | 4 (4.7%) | 2 of 56 | 2 of 29 | not applicable | 53 |
| Sonnet's first run against paid Gemma's first run | 14 (16.5%) | 14 of 56 | 0 of 29 | not applicable | 51 |
| Haiku's first run against paid Gemma's first run | 15 (17.6%) | 15 of 56 | 0 of 29 | not applicable | 50 |
| Paid Gemma, the judge's two verdict instructions reworded with the same meaning | 5 (5.9%) | 5 of 56 | 0 of 29 | 4 | 55 |
| Paid Gemma, the evidence handed to the judge in a different order | 1 (1.2%) | 1 of 56 | 0 of 29 | 0 | 54 |
| Paid Gemma, a note added: "the writer is confident this claim is supported" | 4 (4.7%) | 3 of 56 | 1 of 29 | 3 | 52 |
| Paid Gemma, a note added: a reviewer doubts the claim is supported | 6 (7.1%) | 5 of 56 | 1 of 29 | 4 | 55 |
| Paid Gemma, every claim reworded with the same meaning | 4 (4.7%) | 2 of 56 | 2 of 29 | 3 | not computed (the hand-checked answers belong to the original wording) |

Paid Gemma's first run, the baseline of every paid comparison, got 53 of the 56 hand-checked claims right. Sonnet's first run got 51 and Haiku's first run 50.

**When the free and the paid Gemma runs were made (for the row "free Gemma's first run against paid Gemma's first run").** The free and the paid runs reach the same Gemma model through two different services, so a difference between them can come from the service or from the days on which they ran. These are the times of each run's first and last recorded judge request, over all seven of its inputs, in Central European Summer Time; `./venv/bin/python3 benchmarks/card160/run_dates.py` prints them from the run folders, and `run_dates_2026-10-16.json` in this folder holds its output. Free Gemma's first run: 10 October 2026, 10:47 to 22:54. Free Gemma's second run: 10 October 23:02 to 11 October 09:19. Free Gemma's third run: 11 October 09:22 to 12 October 16:53. Paid Gemma's first run: 10 October 20:50 to 11 October 00:47, so it overlapped the last two hours of free Gemma's first run. Paid Gemma's second run: 13 October 14:24 to 17:27. Paid Gemma's third run: 14 October 00:18 to 02:53. The two first runs compared in that row were therefore made on the same day, and partly at the same time.

**Claims turned into their opposite.** 53 claims that an earlier run judged supported were rewritten to say the opposite (for example "Glauconite exhibits higher sorption than bentonite" became "Glauconite exhibits lower sorption than bentonite"). A correct checker must now call each of them unsupported. Paid Gemma did so for 44 of the 53 (83%) and missed 9. Fifteen of the 53 cite two or more sources, and another cited source can still back part of the changed sentence; on the other 38 it caught 35 (92%). Three claims that paid Gemma's first run judged supported have no opposite copy, because the copies were chosen from an earlier run: paper1 t37, bentonite t2 and essay t1.

**How the one-run arms compare with chance.** Paid Gemma changed 2 verdicts in each of its three plain pairs. The shuffled evidence order changed 1, which is within that range. The reworded instruction (5), the confident note (4), the doubting note (6) and the reworded claims (4) are above it, and most of their changes (4, 3, 4 and 3) fall on claims where all three plain runs agreed. Each of these arms is a single run, so no claim of statistical significance is made, as the plan says.

**Second judges, read claim by claim over all three runs of each judge.** On the 77 claims where all three Sonnet runs agreed with each other and all three paid Gemma runs agreed with each other, the two judges still disagreed on 11 (14.3%), each time Sonnet saying unsupported where Gemma said supported. For Haiku the same count is 13 of 78 (16.7%), again all in that direction. Free and paid Gemma, read the same way, disagreed on 1 of 82. Six of Sonnet's 11 such claims have a hand-checked answer: Sonnet was right on 3 and Gemma on 5 (the counting program scores each judge against the hand-checked answer on its own, so the two counts can add up to more than the number of claims). For Haiku, 8 have a hand-checked answer: Haiku was right on 3 and Gemma on 6.

## The ten constructed hard sentences

One sentence is 10 percentage points, so these are given as counts. Paid Gemma got 8 of the 10 right in each of its three plain runs and changed none between them; the reworded instruction and the shuffled order changed none either. The confident note and the doubting note each changed one: hard sentence 3 ("The outbreak was caused by a laboratory accident linked to gain-of-function research.") turned from supported to unsupported, which the hand-checked answer counts as right (9 of 10). Free Gemma got 8 of 10 in every run and changed none. Sonnet got 8, 8 and 10 of 10 and Haiku 8, 9 and 10 of 10 in their three runs.

## Limits

The checker's instructions and the 56 hand-checked answers were developed on these same six texts with Gemma, so the accuracy column favours Gemma by construction, and the stability measured holds for these texts. Three runs give only three pairs per judge, so a fourth run could fall outside the range shown. Nearly all paid calls went to one seller (Novita, 99.9% to 100% of each run's calls).

## Short version

We ran the claim checker's six standard test texts three times with nothing changed, then with a second judge model, with the judge's instruction reworded, with the evidence in a different order, and with a note saying the writer was confident. With nothing changed, 2 of 85 verdicts changed between runs (2.4 in every hundred). With a second judge model, 14 changed; with the reworded instruction, 5; with the evidence reordered, 1; with the confident-writer note, 4. When we reworded every claim without changing its meaning, 4 of 85 verdicts changed; when we turned 53 supported claims into their opposite, the checker caught 44 of them. On the same texts the checker agrees with our hand-checked answers on 53 of 56 rows. Earlier, smaller measurements already pointed the same way: in one of them we asked the free Gemma judge 7,936 different questions more than once each, and for 16 of those questions it gave a different decision at least once.

Where each number comes from: "three times" and "2 of 85" are paid Gemma's three plain runs (each of the three pairs changed 2; free Gemma's three runs changed 0). "A second judge model, 14" is Sonnet 5.5's first run against paid Gemma's first run (Haiku 5.5's first run: 15). The reworded instruction (5), the evidence order (1), the confident-writer note (4), the reworded claims (4 of 85) and the opposite claims (44 of 53) are paid Gemma's one-run arms, each against paid Gemma's first run. "53 of 56" is paid Gemma's first run (its second run got 54, its third 52, each free run 53). The doubting-reviewer note, which the short version does not name, changed 6. The last sentence is from an earlier measurement, not from this study; it uses the wording of the published plan's section 11.

## How this connects to everything else

The study plan was published on 7 October 2026 with the tag `stability-2026-10`, and it promised these numbers whatever they showed. The short version above is filled in from this table. Every changed verdict, with the claim's words and both verdicts, is listed in `interim_table_2026-10-14.md`.
