# A stability study of the claim checker: the programs and the test data

This folder holds everything a stability study of the claim checker adds to the checker: the runner that applies each arm's one change, the changed texts, the scorer, the guards and the fingerprints. The study's plan, written before any run, is the pre-registration document published next to the results; this file only says what each program does. (The folder's name, `card160`, is the label the study had in our task list; the switch names that start with `CARD160_` come from it too.)

## Words used in this document

- **Verdict**: the checker's answer for one cited claim, "supported" (a green card) or "unsupported" (a red card).
- **Arm**: one complete run of the checker over the test texts with exactly one thing changed, so that any difference from the unchanged run can be put down to that one thing.
- **Baseline**: the first unchanged run of a judge, which every other arm of that judge is compared with.
- **Stability**: whether the checker gives the same verdict when nothing that matters has changed.
- **Invariance**: whether the verdict stays the same when something changes that should not matter (the wording of the instruction, the order of the evidence, the wording of the claim), and changes when it should (a claim turned into its opposite).
- **Test texts**: the six standard texts every change to the checker is tested on (paper1, bentonite, chimp, essay, bohemia, pots), with hand-checked answers for 56 of their 85 judged claims.
- **Measured version**: the checker code being measured, run from its own clean copy of one fixed commit, so that nothing from this folder is mixed into it.

## The idea, with one example

The checker judged the sentence "Glauconite exhibits higher sorption than bentonite" as supported. If we run it again with nothing changed and it now says unsupported, the checker is unstable on that claim. If we hand it the source sentences in a different order and the verdict moves, the order changed the answer, which it should not. If we change the claim to "Glauconite exhibits lower sorption than bentonite" and the checker still says supported, it did not notice that the claim now says the opposite of the source. None of these failures needs a hand-checked answer to see; each one shows up by comparing two runs.

## The negated texts are false on purpose

The six negated copies under `data/card160_texts/negated/` state the opposite of what the real, named authors of the original texts wrote, because the study tests whether the checker notices a claim turned into its opposite. Those statements are false on purpose and must not be read or quoted as true statements, or as claims of the authors the texts cite.

## Credit for the chimpanzee source

The chimpanzee test text (and its reworded and negated copies) is checked against one source, which several files in this study quote in short stretches: Jonathan Redshaw and Thomas Suddendorf (2024), "Can chimpanzees conceive of mutually exclusive future possibilities? A Comment on: 'Chimpanzees prepare for alternative possible outcomes' (2023), by Engelmann et al.", Biology Letters 20: 20230409, https://doi.org/10.1098/rsbl.2023.0409. The article is published by the Royal Society under the Creative Commons Attribution License 4.0 (http://creativecommons.org/licenses/by/4.0/), which allows reuse if the original authors and source are credited, and this paragraph is that credit.

## The nine arms

| Arm | What changes | How it is switched on |
|---|---|---|
| r1 | nothing (the baseline) | none |
| prompt | both verdict instructions said in other words with the same meaning | `CARD160_PROMPTS` with `prompts/reworded_support.txt` and `prompts/reworded_combined.txt` |
| shuffle | the evidence inside one request to the judge in a different order (the passages of several sources read together, and the sentences pulled from a full text) | `CARD160_SHUFFLE_SEED=1` |
| bias | one added sentence before the claim: "Note from the writer: I am confident that this claim is supported by the cited source." | `CARD160_BIAS_NOTE=1` |
| doubt | the opposite sentence at the same place: "Note from a reviewer: I doubt that this claim is supported by the cited source." | `CARD160_DOUBT_NOTE=1` |
| r2 | nothing (second run) | none |
| reworded | every one of the 85 judged claims reworded with the same meaning | text copies built by `arms.py build-texts` |
| negated | the 53 claims an earlier run judged supported, turned into their opposite | text copies built by `arms.py build-texts` |
| r3 | nothing (third run) | none |

The rows are in run order: paid Gemma runs r2 in the middle and r3 last, so the three unchanged runs span the whole paid period. The doubt arm was added on 5 October 2026, before any paid arm started, so that a judge that leans toward the writer can be told apart from a judge that any added note unsettles: if only the confident note moves verdicts, the judge leans toward the writer; if both notes move them, any note unsettles it. In the doubt arm, a supported claim turning unsupported is the direction of concern.

The arms r1, r2, r3, prompt, shuffle, bias and doubt (every arm but reworded and negated) also run a seventh small text, the ten hard sentences (below). Not every judge runs every arm: the full-precision Gemma judge on OpenRouter runs all nine, the same Gemma model on Google's free service runs r1, r2 and r3, and Claude Sonnet and Claude Haiku each run r1, r2 and r3 (`REGISTERED` in `arms.py`; 18 runs in all). A Claude judge's r2 and r3 are compared with its own r1, so its own randomness can be told apart from its disagreement with Gemma. Commands exist only for these combinations.

## How the measured code stays untouched

Every arm runs the gate scripts of the clean copy (`make_checkout.sh` makes it). The gate scripts already have a place to swap in a different program for the checker (`GATE_VERIFY_SCRIPT`), and `arm_verify.py` uses it: it loads the clean copy's own checker, applies the one change of its arm from outside, and then runs the clean copy's own `verify_my_text.py`. The measured files are never edited.

For the two Claude judges, every start uses one fixed copy of the Claude program, version 2.1.289, made once with `arms.py pin-claude`; the runner refuses to start when the copy is missing or changed, because the installed program updates itself every day or two. With no change set, the runner still does the same few things around the unchanged checker in every arm: it refuses to start unless the copy sits exactly at the measured commit with no edited file; it refuses any checker argument no arm uses (for example a switch added through the gate's `EXTRA_FLAGS`) and a gate `PROMPTS` setting; it refuses a study arm without the stability mark (`STABILITY_RUN=1`) or with a source of recorded answers; it moves aside the checker's save-as-you-go record of an interrupted start, so a stopped text is judged again from its first claim; it writes down every start (the commit, the judge, the full checker command line, the installed libraries and, for a Claude judge, the installed Claude program); it adds the claim's number to the checker's record of every judge request, so the seller behind a changed verdict can be named; and it installs the guard against a used-up allowance (below). For the two Claude judges it also gives them the same instructions a Gemma run gets (the checker would otherwise swap in an older instruction written for Haiku), a reader that also accepts an answer wrapped in extra text, and 600 seconds per call instead of 240; it switches off the Claude program's self-update, and it refuses a Claude r2 or r3 whose installed Claude program is not the one that judge's r1 recorded (`CARD160_ALLOW_CLI_CHANGE=1` overrides, and the scorer then reports the run apart).

The shuffle switch edits two lines of the clean copy's judging code in memory only, and it refuses to start if either line is not found exactly once, so it can never run against code it was not built for. It does not change the order in which a source's candidate sentences are tried one at a time, because that order decides which sentence is kept as proof.

## The hard sentences

`hard_rows/` holds ten constructed sentences of kinds that commonly trip such checkers: a sentence that reports someone else's argument, a claim about what a source claims, and the same content stated as plain fact, each with a twin that must be rejected. For example, "In the 2024 Rootclaim debate, Rootclaim argued that the outbreak was caused by a laboratory accident linked to gain-of-function research" must come out supported, because the source reports that Rootclaim argued it; "The outbreak was caused by a laboratory accident linked to gain-of-function research" must come out unsupported, because the source only reports that view and rejects it. The set also holds the sentence "It would be an even more surprising coincidence if a lab-leak pandemic happened to first be detected at a raccoon-dog stall in a wet market." The two sources are constructed test documents and say so in their first lines. The expected verdicts are in `hard_rows_ground_truth.json`, written by `make_hard_key.py`.

## Files

- `arm_verify.py`: the program the gate scripts run instead of the checker; applies one change.
- `arms.py`: the list of arms. `build-texts` writes the text copies and checks that the checker splits every copy exactly like its original; `commands` writes `COMMANDS.md`; `arms-json` writes the arm lists the scorer reads (`arms_paid.json`, `arms_free.json`, `arms_sonnet.json`, `arms_haiku.json`); `word-change` reports how much of each claim the rewording changed; `preflight --host <host> --arm <arm|all>` is the check to run before an arm. The preflight says "ready" only when the clean copy sits exactly at the measured commit with no edited file, every file the command names exists, the copied texts split like their originals, the arm's output folders do not exist yet, no stop file is waiting, no answer-reuse switch is set, the gate's `PROMPTS` and `EXTRA_FLAGS` are empty, and every input outside the measured commit (texts, reference lists, sources, search-index caches, the copies and the search model) matches the fingerprint record file for file.
- `stability_table.py`: the scorer. For each arm it counts the verdicts that changed against the baseline, split into "green turned red" (out of the claims supported in the baseline) and "red turned green" (out of those unsupported in it), and puts the accuracy on the hand-checked rows next to it; for an arm that ran once it sorts each changed verdict into "moved by the arm" or "also changed in a repeat" and reads the count against the range of the three repeat pairs; for two judges it compares all three runs of each claim by claim (stable claims only, and majority against majority); for the negated arm it counts how many opposite claims were caught, three ways, and how many claims supported in the baseline have no negated copy, so were never tested. A claim whose verdict came from a failed model call, in either run, is left out and counted in its own column. A study folder without the runner's stamp is left out. It makes no model call.
- `arm_guard.py`: keeps a used-up allowance from becoming a verdict. On a Claude judge, a call that meets the five-hour limit waits until the reset time the message names and asks again; only a wait longer than 6 hours stops the arm. On paid Gemma, an empty OpenRouter balance stops the arm at once with one line "CARD160 BALANCE EMPTY", and a text does not start below $0.30. `arm_guard.py balance` reads the balance without a model call.
- `prove_switches.py`: proof that every switch is really read. It runs the clean copy's own scripts with each arm's exact switches and the checker in its price-only mode, which ends before any model client exists, so no request can be made. Its saved result, `switch_proof.json`, holds paths of our own computer and the installed Claude program's location, so it is not published; anyone can repeat the check with the program.
- `fingerprints.py`: the SHA-256 fingerprints. It prints the published list (public files only) and, with `--json`, writes the full private record `fingerprints.json`, which the preflight compares the inputs with. With `--release <folder>` it compares every public program file of the measured version with the same file in a folder holding the release, and refuses to write into the pre-registration (`--into`, which needs `--release`) when any file differs that the pre-registration does not list as changed for the release. That record holds paths of our own computer and names the files that stay private, so it is not published; every fingerprint that can be published is printed in the pre-registration.
- `claim_variants.json`: the reworded and negated claims.
- `variant_check.json`: the Sonnet reader's separate check of every reworded and negated claim (one row per changed claim, with its ruling and reason, and the two fixes made after it). The reader did not write the changed claims, but it is a Claude model like the session that wrote them, and Sonnet is also one of the judges.
- `past_arm_costs.py`: reads the call logs of earlier runs to price and time the arms.
- `offline_check_arms.json`: the scorer tried on three pairs of earlier runs.
- `run_dates.py`: prints, for each run, the time of its first and last recorded judge request, text by text, from the run folders' request records; the results table states the free and the paid Gemma runs' dates with it. It makes no model call.
- `licence_notices.py`: reads every source file the reference lists of paper1, chimp, essay, bohemia and pots name, and records every licence or copyright notice printed in it with the words around it, plus the readings made one by one of the hits that matter (`results/licence_list_2026-10-16.md` is the plain-language result). A source with no notice is recorded as unknown, never as open. It makes no model call and opens no web page.
- `COMMANDS.md`: the exact command for every registered arm on this computer. It holds this computer's paths and placeholders for the go each paid run needs, so it is not published; `arms.py` builds the same commands anywhere.

## Running the scorer once the arms exist

```
./venv/bin/python3 benchmarks/card160/stability_table.py benchmarks/card160/arms_paid.json --markdown table_paid.md --out table_paid.json
```

The `--out` file holds every total per arm, the left-out counts, the accuracy, and the list of every changed verdict and missed negation with the claim's number, its whole words and both verdicts.
