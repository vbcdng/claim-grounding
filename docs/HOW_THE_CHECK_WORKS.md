# How the check works

The README explains how to run this tool. This page explains what the
tool actually does between the moment you start a run and the moment a
verdict appears. You do not need anything on this page to use the tool.
It exists so you can judge for yourself how much to trust a verdict.

## Words used in this document

- **Claim** — one cited statement in your text, ending at a citation
  marker.
- **Citation marker** — the `[[key]]` tag in your text that names which
  source a claim cites.
- **Source** — a file, usually a PDF or a text file, that a citation
  points at.
- **Model** — a language model: a program that reads text and answers
  questions about it. The tool asks models over the internet, except
  one small comparison model that runs on your own computer.
- **Verdict** — the tool's judgment on one claim: supported,
  unsupported, or "own" (your uncited words, shown but not checked).
- **Card** — one claim's box in the HTML report, showing its verdict
  and the evidence behind it.
- **Chip** — a small colored label on a card that adds a warning or a
  note. A chip never changes the verdict.

## The example this page follows

Suppose your text contains this sentence, and `sources/larsen2019.pdf`
is the paper it cites:

> The trial followed 1,200 nurses for ten years and found no effect on
> heart disease. [[larsen2019]]

The sentence, the paper, and its author name are invented for this
page. The rest of this page follows that one sentence through every
step.

## Step 1 — split your text into claims

The tool reads your text and cuts it at the citation markers. Each
passage that ends at a marker becomes one claim, tied to the source the
marker names. The example sentence becomes one claim citing
larsen2019. Text with no marker becomes an "own" claim. Own
claims are shown in the report but never checked, because there is no
source to check them against.

## Step 2 — read the sources

The tool extracts the text of every source file and splits it into
numbered sentences. A scanned PDF contains pictures of pages instead of
text, so this step finds nothing in it. That problem and its workaround
are described in the known-issues page.

## Step 3 — find candidate evidence

A source can have thousands of sentences, and asking a model to read
all of them for every claim would be slow and expensive. So the tool
first reduces how much there is to read. The comparison model on your
own computer turns
every sentence into a list of numbers, built so that sentences with
similar meaning get similar numbers. This is the one-time large
download from the install step, and it needs no internet afterwards.

The tool compares the claim's numbers with every source sentence's
numbers and keeps the closest matches. For the example claim, that
would pick out the paper's sentences about how many people were
followed, for how long, and what was found.

## Step 4 — the judgment

A language model now reads the claim together with the candidate
sentences. This page calls that model the judge from here on. The
judge answers whether the sentences prove the claim as written, and
gives its reason. One shortcut exists: a source sentence that is
nearly word-for-word identical to the claim is accepted without asking
the judge at all.

"As written" is the strict part. The example claim has four separate
parts: it was a trial, 1,200 nurses, ten years, and no effect on heart
disease. If the paper followed the nurses for eight years, the claim as
written is not supported, even though most of it is right. The judge's
answer, with the quoted sentences, is what the card later shows.

## Step 5 — when the first look finds nothing, read more

Sometimes the proof exists but was worded so differently that step 3
did not select it. So a rejection is never final after one look. The
tool cuts the source's full text into large pieces and picks the six
pieces closest to the claim. Then it asks the judge again, up to three
times, and uses the answer given by the majority. When the first two
answers agree, the third is skipped.

There is one more retry. The claim is split into its separate parts,
and the tool searches for each part on its own in the source's full
text. If every part is found, the claim is judged once more on the
found passages together. Only a unanimous "supported" changes the
verdict, and the card then records which retry changed it.

## Step 6 — extra checks that warn but never decide

After the verdicts are decided, several smaller checks add chips. Each one is
a warning for you to weigh, never a verdict change.

- **Partial support.** Every supported cited claim is re-read as a
  whole. If one real part of its content appears in none of the cited
  sources, the card gets a "partial support?" chip. The tool also names
  which of your other sources might contain the missing part.
- **Proof display.** For a supported claim, the tool maps each part of
  the claim to the sentence that proves it. A part with no proving
  sentence shown gets a visible note on the card, beginning "Coverage
  gap". Parts that are common knowledge are not counted as gaps.
- **Citation purpose.** For an unsupported cited claim, one small model
  call asks what the citation was for. A citation can back a whole
  passage, or only a named fragment of it — for example a method the
  authors borrowed. The second kind is shown as its own class in the
  report instead of being counted as a plain rejection.
- **Citation needed.** An uncited sentence that states a checkable fact
  gets a "citation needed?" chip.

## Step 7 — the arbiter re-reads the flagged claims

The arbiter is a second model, deliberately a different one than the
judge. It re-reads only the claims
that ended flagged, meaning rejected or supported with warnings. It
gets much more of the source text than the judge did. Every finding it
reports must include a quote, and the tool verifies that the quote
really appears in the source, word for word. A finding without a real
quote is discarded.

The arbiter can change what you see in two ways. First, when it finds
proof for a rejected claim, the original judge re-reads exactly the
passages the arbiter found. Only a unanimous "supported" changes the
verdict, and the card records that the arbiter's finding caused it.

Second, the arbiter can clear a warning. When a supported
card says "not proven as written" and the arbiter finds the missing
sentences, the warning is cleared. The card then shows who cleared it.
A warning the arbiter does not clear is strong evidence of a real gap.
It means a second model, given the whole source, found no proof
either.

## Step 8 — what you get at the end

The run writes an HTML report you can open in any browser, plus a
machine-readable file (`analysis.json`) with every verdict and its
evidence. Cards appear in the order of your text. If you edit your text
and run again into the same output folder, only the edited claims are
re-judged. The unchanged ones keep their verdicts and cost nothing.

## Why you can check the checker

Models make mistakes, so no verdict on a card asks to be believed
blindly. Every supported claim shows the exact source sentences used
as its proof, and every rejection says which part was not found. You
can
open the source at the cited spot from the card and read it yourself.
The known limits are also written down. The problems are in
`docs/KNOWN_ISSUES.md`, the planned improvements in `ROADMAP.md`, and
the measured accuracy in the README's "How accurate is it" section.

## How this page connects to the rest

The README tells you how to install and run the tool and how to read
the report. This page told you what happens inside a run. The
known-issues page lists what is broken and how to work around it, and
the roadmap lists what gets improved next.
