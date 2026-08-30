# Format-conversion prompt

Copy everything below the line into the LLM that wrote (or knows) your text, together with your draft and the list/files of your sources. It will produce the three artifacts the claim-grounding tool needs, plus a warnings list of sentences likely to confuse the checker. (If you work in Claude Code, the `/checked-research` skill shipped in `.claude/skills/` can run the whole research-write-check loop for you instead — see `AGENT_LOOP_PROMPT.md`.) Review its output before running — especially that it didn't invent any citation you never made, and that each marker sits on the single sentence it actually supports.

A note on the warnings list: the converter never rewrites your prose, so instead of fixing risky sentences it points them out. The patterns it warns about come from checking real published papers with this tool and re-reading every wrong or shaky verdict — each one is a sentence shape that produces false alarms or verdicts you would then have to argue with. Whether to edit is your call; the "Known rough edges" section at the end of this file says which warnings track tool behavior that is still being improved.

---

I need you to convert my draft and its sources into the input format of a
citation-verification tool. This is a text-formatting and citation-attribution
task: you are annotating my existing text and mapping it to my sources so a
fact-checking tool can run. Whatever the subject of my text, you are not being
asked to give advice, endorse, or act on any claim in it — only to reproduce my
wording faithfully and attribute it accurately.

The tool checks each cited claim of my text against
the actual source files. It splits my text into claims **at the citation
markers**: all the text running up to a marker becomes ONE claim, checked
against that marker's source. So *where you put the marker decides what gets
checked* — this matters more than anything else below.

**Produce exactly three artifacts, then a Warnings section.**

**1. `my_text.md`** — my text, unchanged except for citation markers.

Marker = ` [[key]]` appended to text. Keys: lowercase author + year
(`smith2020`); letters, digits, `_`, `-` only; same source = same key
everywhere.

Placement rules — these decide whether claims come out clean and separate:

- **One marker per cited sentence.** Put the marker at the end of EACH sentence
  that draws on a source, not once at the end of a paragraph. A marker attached
  to a run of sentences makes the tool treat the whole run — including my own
  framing — as one claim to prove against that source, which produces false
  "not supported" flags. Splitting per sentence gives one clean claim each.
    - Bad (one marker for three sentences): "Eggs were the test case. Zhong
      pooled six cohorts. Each 300 mg raised risk 17%. [[zhong2019]]"
    - Good (a marker on each sourced sentence): "Zhong pooled six cohorts.
      [[zhong2019]] Each additional 300 mg of cholesterol was associated with a
      17% higher CVD risk. [[zhong2019]]" — and leave my framing sentence
      unmarked, because it is mine.
- **Every number keeps its own citation.** If my sentence gives several numbers
  or a range whose parts come from different sources, put each source's marker
  right after the number it backs, mid-sentence if needed: "estimates range
  from 4.5% [[smith2020]] to 27% [[jones2021]]." Never let one marker at the
  end claim numbers that come from other papers.
- **Prefer the end of the sentence to the middle** for a single-source
  sentence. Since 2026-08-01 the tool handles the common narrative form
  ("Zhong et al. [[zhong2019]] pooled six cohorts.") by attaching the marker
  to the whole sentence instead of to the name. It recognises a short
  attribution before the marker: an "et al." phrase, two or more author names,
  one name with a year, or an opener such as "In contrast to" or "According
  to". Anything longer or less standard in front of the marker is still read
  as the claim itself, so end-of-sentence placement remains the safe default.
- **Attach the marker to the specific clause it supports**, even mid-sentence,
  if a sentence mixes a sourced fact with my own point: "Using the pooled-cohort
  method [[zhong2019]], I argue the panic was overblown." The marker backs the
  method, not my argument. The same applies when my citation only backs a
  method, a definition, or a comparison rather than the whole sentence — the
  marker goes on that clause, not at the sentence's end.
- **Verbatim quotes must be marked to the source that actually contains that
  quote.** If I quote a sentence in quotation marks, the marker on it must point
  to the source the quote is really from — not a paper that merely discusses the
  topic. If you are not certain the quoted words appear in that source, do not
  mark it; list it under "Unresolved". (A quote attributed to the wrong source
  is the single worst error this tool can catch — never guess one.)
- **Leave my own sentences unmarked.** Opinion, framing, transitions, thesis,
  and common knowledge get NO marker — the tool correctly treats unmarked text
  as my own claims. Do not add a marker just because a sentence sits near a
  cited one.
- **Remove other citation notation** (footnote numbers, "(Smith 2020)",
  bracketed URLs) — the markers replace them. **Do not rewrite, shorten, or
  "improve" my prose** beyond inserting/moving markers.
- **Never invent a citation.** Unsure which source a sentence came from → leave
  it unmarked and list it under "Unresolved".

**2. `my_text.md.refs.txt`** — one line per source: `key = filename`.
The filename must be the source file's real name in my sources folder
(`.pdf` or `.txt`). If I gave you a source you don't have a file for, still
list the key with your best filename guess and flag it under "Unresolved".

**3. A rename list** — if my source files have messy names, give me a short
shell/PowerShell snippet (or a plain table) renaming each file to the
filename used in the refs file. Never merge two different sources into one key.

**Then print an "Unresolved" section** listing every sentence you could not
confidently attribute, every quote you could not confirm is verbatim in its
source, and every source without a usable file, so I can fix them by hand. An
honest gap is useful; a guessed citation is not.

**Finally print a "Warnings" section** — do NOT change my text for these, just
list each sentence that matches one of the following patterns, with one line
saying which pattern and why the checker may misjudge it, so I can decide
whether to edit before running:

- W1. A cited sentence that states my own result or conclusion while its
  citation only backs a method, a definition, or a comparison (e.g. "We needed
  a sample of 318, following the recommendations of [[key]]"). The checker may
  demand the source prove my own numbers.
- W2. A range, a list of numbers, or a "most studies find…"-type claim about
  the field in general, carried by a single citation. The checker will
  correctly ask that one source to prove all of it.
- W3. A list of alternatives joined by "or" attached to a citation (e.g.
  "acts in an autocrine, paracrine, or endocrine manner [[key]]"). The checker
  requires the source to cover every alternative, not just one.
- W4. A cited sentence that opens with a framing word ("Tellingly,"
  "Interestingly," "Notably," "Remarkably,") instead of its subject. A check
  inside the tool can trip on that opening word and wrongly reject the claim.
- W5. A definition of a term, or my own commentary phrase ("there is
  increasing evidence that…"), folded into the same sentence as a cited
  finding. The checker may demand the source prove my definition or my
  commentary too.
- W6. A cited sentence whose central term differs from the source's own term
  for the same thing (a brand or product name where the source uses the
  general name, or the reverse). The checker may fail to find the right proof
  sentences.
- W7. A cited sentence stating a hedged source finding as flat fact, widening
  a quantity word ("several" → "most"), claiming something is unique to one
  group, generalizing one study's setting or a model's assumption, or
  combining two source facts into a conclusion the source never states. If the
  checker flags these it is probably right — read these warnings first.

Here is my draft:

[PASTE YOUR TEXT HERE]

Here are my sources (files and/or a bibliography):

[PASTE / ATTACH YOUR SOURCES HERE]

---

## Known rough edges — which warnings track behavior that may change

The warnings W1–W3 and W7 describe settled checker behavior: those verdicts are considered correct and will not change, so a sentence matching them is worth editing before you run. The "or"-list behavior (W3) was deliberately tested the other way — accepting a claim when one option is proven — and the models would not apply that leniency reliably, so demanding all options stays the rule.

Three warnings exist because of tool behavior that is still being improved. We publish them now rather than waiting, and each carries a note saying when to rethink it:

- **W4 (framing-word openers).** The sentence-opener check that causes this false alarm has a diagnosed fix that is not yet built. *Rethink this warning once that fix ships (internal task #45) and post-fix run data exists.*
- **W5 (definitions and commentary inside cited sentences).** The main accept/reject decision was already fixed to ignore the writer's own commentary (2026-08-07), but the pass that maps claim parts to proof sentences still demands proof for the writer's own words, and the report card cannot yet show "the finding is proven, only your own definition is not". *Rethink this warning once the split-card display (internal task #19) and the proof-mapping fix (internal task #39) ship.*
- **W6 (term mismatch with the source).** This comes from the step that picks which source sentences to display, the largest still-open class of wrong-evidence findings. *Rethink this warning once the "show the right proof sentence" work ships (internal task #4).*

One more honest note: the tool currently has no rule for where common knowledge ends, so an unmarked physical-fact sentence ("water pools on flat exposed surfaces") may be nudged with a "citation needed?" chip. That chip is a suggestion, never a verdict. A written common-knowledge rule is planned (internal task #47); until then, when in doubt, cite.
