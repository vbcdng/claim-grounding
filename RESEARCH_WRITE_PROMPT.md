# Research-and-write prompt (for deep-research tools)

Use this when you want a deep-research tool — **Claude Science, Elicit, Perplexity, GPT deep research, or any capable model** — to *write a new cited text* that will drop cleanly into the claim-grounding tool, rather than converting a draft you already have. (To convert an existing draft instead, use `CONVERT_MY_TEXT_PROMPT.md`. And if you work in Claude Code, the `/checked-research` skill shipped in `.claude/skills/` runs the research, these writing rules, and the check as one loop — see `AGENT_LOOP_PROMPT.md`.)

## How the checker reads your text (why these rules exist)

The tool splits your text into claims **at the citation markers** and checks each claim against the real source file. So two things decide whether you get a clean result: **one marker per cited sentence**, and **every marker points to a source that genuinely says that claim**. The prompt below bakes both in.

Everything else in the prompt comes from checking real published papers with this tool and studying every wrong or shaky verdict. The single most common false alarm on real papers is a citation that only backs a *method* or a *concept* while the rest of the sentence is the writer's own result — the checker reads the whole sentence as something the source must prove. The second most common problem is a sentence that packs in more than one source's worth of facts (a range gathered from several papers, a "most studies find…" claim, a list of alternatives) under a single citation. The rules below prevent both, plus the smaller cases we found.

## Pick your citation syntax by tool

- **Claude Science** — let it use its native pandoc `[@key]` citations and export the `.bib`. Our importer (`import_claude_research.py`) converts `[@key]` → `[[key]]` and builds the refs file for you. Use **Variant A** below.
- **Any other tool** (Elicit, Perplexity, GPT, plain LLM) — ask for `[[key]]` markers plus a `key = source` list directly. Use **Variant B** below.
- If a tool can't emit either cleanly, let it write normal prose with a bibliography, then run its output through `CONVERT_MY_TEXT_PROMPT.md`.

---

## Variant A — Claude Science (native `[@key]` + `.bib`)

```
Research the topic below and write an evidence-based text with citations.
This is a research-synthesis task: summarize what the published literature
reports and attribute each claim to its source, as input for a
citation-verification tool. Report the state of the evidence with citations,
whatever the subject — this is a literature summary, not personalized advice.

TOPIC: <your topic and any angle/length you want>

How to cite (this matters more than style — my text will be machine-checked
against the actual source PDFs):

WHERE the citations go:
1. Place a citation directly on EACH sentence that states something from a
   source — never group citations at the end of a paragraph, and never let one
   citation cover a run of sentences. One sourced sentence = one citation.
2. Every specific number gets its citation right next to it. If a sentence
   gives a range or several numbers that come from different papers, cite each
   paper next to its own number — never one citation for numbers from several
   sources.
3. If a sentence mixes a sourced fact with my own reasoning, cite only the
   sourced clause: "Using their pooled method [@zhong2019], the panic looks
   overblown" — the cite backs the method, not the conclusion.
4. Sentences that are your own framing, transition, or interpretation carry
   NO citation. Leave them uncited on purpose.

WHAT a cited sentence may say:
5. Every cited claim must match what the source actually says — its direction,
   magnitude, and hedges. Never turn a hedged finding ("may be associated")
   into a flat fact ("causes"); never widen a quantity word ("several
   countries" must not become "most countries"); never present one study's
   setting, or a model's built-in assumption, as a general fact; never combine
   two separate source facts into a conclusion the source never states; never
   drop a number, a range endpoint, or a qualifier the source includes.
6. If a citation only backs a method, a definition, or a comparison — not the
   whole sentence — say so in the wording: "following the method of
   [@asendorpf2013]", "as defined by [@key]", "in contrast to [@key]". Keep
   your own results and conclusions in separate sentences from such citations.
7. A claim about the research field in general ("most studies find…",
   "the literature suggests…") needs a citation to a review or survey paper,
   or at least two citations. One single study never supports a "most
   studies" sentence.
8. Do not attach a list of alternatives joined by "or" to a citation unless
   the cited source covers every alternative. If the source shows only one of
   the options, state that one option.
9. Start each cited sentence with its subject, not with a framing word:
   write "Individual forecasters' near-term performance was…", not
   "Tellingly, individual forecasters' near-term performance was…".
10. Put a definition of a term in its own uncited sentence, or leave it out —
    never fold a definition into a sentence that also carries a cited finding.
11. Use the source's own key terms for the central things in a cited sentence.
    If the source talks about "bentonite clay", do not refer to it only by a
    brand name the source barely uses.
12. PARAPHRASE in your own words — reordering, merging two source sentences,
    or using different wording for the same fact is fine and encouraged. But a
    direct quotation in quotation marks must appear verbatim in the source you
    cite for it — if you are not certain, paraphrase instead.
13. State a physical or factual claim without a citation only if essentially
    every reader knows it firsthand; when in doubt, cite it.
14. Prefer open-access sources with a downloadable PDF or a DOI/arXiv id, so
    the sources can actually be fetched and checked. Use real, existing papers
    only — never invent a citation or a result.

Export as markdown with pandoc [@key] citations plus a .bib bibliography.
```

## Variant B — other tools (`[[key]]` markers + refs list)

```
Research the topic below and write an evidence-based text with citations.
This is a research-synthesis task: summarize what the published literature
reports and attribute each claim to its source, as input for a
citation-verification tool. Report the state of the evidence with citations,
whatever the subject — this is a literature summary, not personalized advice.

TOPIC: <your topic and any angle/length you want>

Output format (my text will be machine-checked against the actual source files,
so follow this exactly):

TEXT: flowing prose. After EACH sentence that states something from a source,
append a marker ` [[key]]` (e.g. ` [[smith2020]]`).

WHERE the markers go:
- One marker per sourced sentence — never group markers at the end of a
  paragraph, never let one marker cover several sentences.
- Two sources for one sentence: two markers ` [[a]] [[b]]`.
- Every specific number gets its marker right next to it. A range or several
  numbers drawn from different papers: each paper's marker sits next to its
  own number — never one marker for numbers from several sources.
- If a sentence mixes a sourced fact with my own point, put the marker right
  after the sourced clause, even mid-sentence.
- Sentences that are my own framing/transition/interpretation get NO marker.
- Keys: lowercase author+year, letters/digits/_/- only; same source = same key.

WHAT a marked sentence may say:
- Every marked claim must match what the source actually says — direction,
  magnitude, and hedges. Never turn a hedged finding into a flat fact, never
  widen a quantity word ("several" must not become "most"), never present one
  study's setting or a model's assumption as a general fact, never combine two
  separate source facts into a conclusion the source never states, never drop
  a number, range endpoint, or qualifier the source includes.
- A marker that only backs a method, definition, or comparison must be worded
  that way ("following the method of [[key]]", "as defined by [[key]]") and
  the writer's own results kept in separate sentences.
- A claim about the field in general ("most studies find…") needs a review
  paper or at least two markers — one single study is never enough.
- No lists of alternatives joined by "or" on a marked sentence unless the
  source covers all of them; state the one option the source shows.
- Start each marked sentence with its subject, not a framing word like
  "Tellingly," or "Interestingly,".
- Definitions of terms go in their own unmarked sentence, never inside a
  marked sentence.
- Use the source's own key terms for the central things in a marked sentence.
- Paraphrase freely (reorder, merge, reword). A direct quotation ("...") must
  be verbatim in the source cited for it; if unsure, paraphrase instead.
- A physical or factual claim stays unmarked only if essentially every reader
  knows it firsthand; when in doubt, cite it.
- Prefer open-access sources with a downloadable PDF or DOI/arXiv id. Use
  real, existing papers only — never invent a citation or a finding.

Then a REFERENCES block, one line per key:
  key = Full citation (Title, authors, year, DOI/arXiv/URL if available)

End with an "Unresolved" note listing any claim you could not confidently
attribute and any source you could not find a real reference for.
```

---

## Where each rule comes from

The rules in the prompt fall into two groups.

**Rules that protect you from a deserved red flag.** When we ran this checker over real published papers and had a person re-read every red verdict, these patterns turned out to be genuine citation errors, and the checker was right to flag them: a hedged source statement turned flat, a quantity word widened ("several" written as "most"), a claim of uniqueness the source never makes, one study's result stated as a universal fact, two separate source facts combined into a conclusion the source never draws, and a dropped number or qualifier. Rule 5 covers all of these. Rules 2 and 7 cover the second big genuine-error class: a range, list, or field-wide claim resting on a single citation that only backs one piece of it.

**Rules that protect you from a false alarm.** The same re-reading found patterns where the writing was honest but the checker (or any careful reader) could not tell what the citation was supposed to cover. The biggest one: a sentence that states the writer's own result but carries a citation that only backs the method — rule 6 makes the citation's job explicit. The others: definitions folded into cited sentences (rule 10), "or"-lists the source only partly covers (rule 8), framing-word openers (rule 9), and terms the source itself does not use (rule 11).

Worked example of the biggest false-alarm pattern. Bad: "We surveyed 318 employees across three regions and found response rates fell sharply after 2015, following the approach of Asendorpf (2013)." The citation reads as if Asendorpf's paper proves the survey and the 2015 trend, which it does not — red flag. Good: "Following the sampling approach recommended by Asendorpf (2013), we surveyed 318 employees across three regions. Response rates fell sharply after 2015." The citation now backs exactly the one thing the source says, and the result is stated as the writer's own.

What you do NOT need to worry about: honest paraphrase. The checker was deliberately tested against stricter judging rules, and every stricter rule wrongly flagged fair paraphrases, so the shipped behavior stays lenient on wording. Reordering facts, merging two source sentences into one, using your own words or your own labels for the source's ideas, and adding your own counting words ("three main challenges are…") are all safe.

## Provisional rules — marked to revisit

The tool is still being improved, and three of the rules above exist to route around behavior that may change. Each is safe advice on its own, but the *need* for it should be re-checked later. We are publishing now rather than waiting, so each rule below carries a note saying what future change should trigger rethinking it.

- **Rule 9 (no framing-word openers).** Today a safety check inside the tool takes the first words of a cited sentence and looks for them in the source, so a sentence opening with "Tellingly," can be wrongly rejected. *Revisit this rule once the sentence-opener check is fixed (internal task #45) and there is data from runs after the fix.*
- **Rule 10 (definitions in their own sentence).** Today the tool shows one red card for a whole sentence even when only the writer's own inline definition lacks source support and the actual finding is proven. A split display ("this half checked, this half is your own words") is designed but not built. *Revisit this rule once the split-card display ships (internal task #19) and the proof-sentence mapping stops demanding proof for the writer's own words (internal task #39).*
- **Rule 11 (use the source's own key terms).** Today the step that picks which source sentences to show can miss the right ones when the writer's term and the source's term differ (a brand name versus the general material name), producing a wrong "no proof found". *Revisit this rule once the "show the right proof sentence" work ships (internal task #4).*
- **Rule 13 (when in doubt, cite it).** The tool has no rule yet for where common knowledge ends and citation-needing facts begin; today it leans toward flagging uncited factual statements. *Revisit this rule once a common-knowledge rule is written and tested (internal task #47).*

Rule 8 ("or"-lists) is settled, not provisional: it was tested whether the checker could accept "one proven option is enough", the models would not apply that leniency reliably, and the behavior was deliberately left as is. Cover all the options or name one.

## After the tool gives you the text

- **Get the source files.** The tool checks against real PDFs/txt, so you still need the actual sources in a folder. With a Claude Science export or a Variant-B references block that has DOIs/URLs, the downloader (`download_sources.py`) can fetch the open-access ones; anything paywalled you add by hand into the `inbox/` folder (`ingest_downloads.py`).
- **Skim the markers before running.** A few minutes of checking saves a run full of noise. Look for: does each marker sit on the one sentence it supports (not a paragraph)? Are your own framing sentences left unmarked? Does every number have its own citation next to it? Is every "following the method of…" citation worded that way? Do any cited sentences open with "Interestingly,"-style words?
- Then run the tool as usual (or the interactive wizard, which walks you through import → download → verify).
