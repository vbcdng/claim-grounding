# Agent-loop prompt (write → check → rewrite, run by one agent)

Use this when you work with an **agent tool** — Claude Code, or any
equivalent that can both use a language model and run commands. The
agent runs the whole loop alone. It researches and writes a cited text
(or converts a draft you already have), saves the input files, and
collects the sources. It then runs this repository's checker, reads
the verdicts, repairs the flagged sentences, and checks again.

This prompt is deliberately separate from the two prompts that shape
the text itself:

- `RESEARCH_WRITE_PROMPT.md` (same folder) — the writing rules for a
  NEW text. The agent is told below to read and obey it.
- `CONVERT_MY_TEXT_PROMPT.md` (same folder) — the conversion rules for
  a draft you already have. The prompt below covers this case too: fill
  in the DRAFT placeholder and the agent converts instead of writing.

This prompt governs the *process*, and those files govern the *text*.
Keep it that way when you edit either one.

**Status note.** We ran this full loop end to end in August 2026 with
Claude Code as the agent: it wrote a cited text, saved both input
files correctly on the first try, and the rewrite-from-feedback step
turned two rightly-flagged claims into proven ones on the re-check.
The same test also caught the agent taking shortcuts — it saved its
own summaries as source files instead of the real documents, and it
swapped in its own research routine without saying so. The rules below
now name both as failures. So the loop works, but read the agent's
final report critically, and spot-check that the files in `sources/`
really are the documents they claim to be. Testing with agent tools
other than Claude Code is still open.

**Claude Code shortcut.** If your agent tool is Claude Code, you do not
need to paste this prompt at all: the repository ships the same loop as
a packaged skill. A skill is a saved instruction sheet Claude Code
loads when you type its name. Open Claude Code in the repository folder
and type `/checked-research` followed by your topic; the session then
runs research, the two-file save, source collection, the check, and one
repair cycle, under the same safety rules as the prompt below (the
skill file is `.claude/skills/checked-research/SKILL.md`). One thing
the skill cannot change: the research step runs on whatever model your
Claude Code session uses, so pick the session model first. The status
note above applies to the skill too.

## How to use it

1. Open your agent tool in a folder that contains (or can reach) this
   repository, installed as the README describes.
2. Copy the prompt below, fill in the three placeholders in angle
   brackets, and give it to the agent.
3. Stay reachable: the agent is told to ask you before anything paid
   and to hand the final decisions back to you.

---

```
You are an agent with shell access. Your job is to produce a cited text
whose every citation has been machine-verified against its real source,
using the claim-grounding tool in this repository.

REPOSITORY: <path to the claim-grounding folder>
TOPIC: <the topic, angle, and rough length you want>
DRAFT: <path to an existing draft to convert, or the word "none">
WORKING FOLDER: <where to put the project, e.g. data/my_project>

Follow these steps in order. Do not skip the verification or reporting
steps, and do not go beyond one repair cycle.

STEP 1 — GET THE TEXT (two cases, depending on DRAFT).

If DRAFT is "none", write a new text. Read RESEARCH_WRITE_PROMPT.md in
the repository and follow its Variant B rules exactly: they define
where citation markers go, what a cited sentence may claim, and the
output format. Research the TOPIC with your own research capability
and write the text. Only cite sources you have actually read.

If DRAFT names a file, convert that draft instead of writing. Read
CONVERT_MY_TEXT_PROMPT.md in the repository and follow it exactly: it
defines how to place the [[key]] markers on the draft's existing
citations, how to build the reference list, and which warnings you
must report. Do not rewrite the draft's prose beyond what that file
allows, and never add a citation the draft did not make.

In both cases: never invent a source or a citation.

STEP 2 — SAVE THE TWO INPUT FILES.
Write the text to <working folder>/my_text.md with the [[key]] markers
in place. Write the reference list to
<working folder>/my_text.md.refs.txt, one line per source in the form
key = filename (for example: rubin2014 = rubin2014.pdf).

STEP 3 — COLLECT THE SOURCE FILES.
Put every cited source into <working folder>/sources/ under the exact
filename from the refs file. Download only sources that are legally
free to access (open-access papers, public agency pages). Never bypass
a paywall. After each download, open the file and confirm it is the
real source, not an error page or an abstract-only stub. If a source
cannot be fetched, do NOT substitute a different one silently: list it
for the human and either drop the claims that cite it or leave them to
be marked "source file missing" by the checker.

STEP 4 — RUN THE CHECK.
From the repository folder run:

  venv/bin/python verify_my_text.py \
    --text <working folder>/my_text.md \
    --sources <working folder>/sources \
    --output-dir <working folder>/run --yes

If you are running inside Claude Code, add: --backend claude-code
(free, but slow — expect roughly half an hour to an hour for a short
text; let it finish). Otherwise the tool uses its default free-tier
model if a key is configured. Ask the human before using any paid
model. Do not edit any file in the repository, and never edit the
source files.

STEP 5 — READ THE VERDICTS.
Read <working folder>/run/analysis.json (or the viewer.html cards).
For every claim that is unsupported or carries a warning flag, read
the card's evidence: which part has no proof, and what the source
actually says.

STEP 6 — REPAIR, ONCE.
Rewrite ONLY the flagged sentences, following the same writing rules
from step 1. Permitted repairs: weaken the claim to what the source
genuinely says, split an overloaded sentence, move a citation to the
clause it really backs, or delete the claim. Never fix a red verdict
by swapping in a citation to a source that does not state the claim,
and never add a citation to a sentence that is your own framing. Keep
a list of every edit and the reason for it.

STEP 7 — CHECK AGAIN.
Re-run the exact command from step 4 with the same --output-dir. The
re-run is incremental: unchanged claims keep their verdicts, only the
edited ones are re-judged.

STEP 8 — STOP AND REPORT.
One repair cycle is the limit (the repository's repair playbook
explains why: further automated cycles optimize the text toward the
judge instead of toward the truth). Give the human: the verdict counts
before and after, every edit you made with its reason, every source
you could not fetch, and every claim that is still unsupported — those
remaining claims are the human's decision, not yours. Report failures
honestly, including any model calls that failed during the runs.
```

---

## Notes

- The one-cycle limit in step 8 is the same guardrail the repository's
  own repair command follows (`docs/REPAIR_PLAYBOOK.md`, guardrail 4).
- If your agent tool is Claude Code, the repair step can also use the
  shipped `/apply-review` command instead of step 6. Mark the cards in
  the viewer, download `review.json`, and run `/apply-review`. The
  prompt's step 6 exists so that agents WITHOUT that command can still
  repair safely.
- The checker never needs the agent's research notes — only the two
  input files and the sources folder. Everything else the agent
  produces is for the human.
