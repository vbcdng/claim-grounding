# Printing press — six-judge comparison per claim (2026-07-12)

Same protocol as `docs/EGGS_FIVE_JUDGE_TABLE.md`, on the author's terminal-test
text (`import_claude_research` of the printing-press/Reformation synthesis,
8 sources, all fetched incl. iyigun2008 manually). Run artifacts (gitignored):
`data/loop_rounds/round_printing/` (project + app + columns + table.md).
Cost: gemini run $0.02 (already done); all six extra columns $0
(claude-code CLI + local CPU models).

| # | claim | 1. Gemini (production) | 2. MiniCheck (suff) | 3. Qwen-4B checker (suff) | 4. Sonnet (suff) | 5. Opus — true per source? | 6. Fable grader — expected outcome | 🧑 Author |
|---|---|---|---|---|---|---|---|---|
| t1 | Eisenstein: print let heterodox doctrines escape the containment that snuffed out earlier heresies | ✅ sup (full) | ❌ 0.054 | ✅ | ❌ | ✅ | 🔍 tool-fetch (quotes the containment proof) | ⚠ verdict_wrong — "needs better support sentences, or rewrite in case the source does not contain proper support" |
| t2 | Econometric teeth: no advantage by 1530, +43.6 pp by 1560, ≥29 pp by 1600 (Rubin) | ❌ uns | ❌ 0.056 | ❌ | ❌ | ❌ | ✍️ author-fix — missing the specific econometric figures | ⚠ "needs more supporting sentences or rewrite the text" |
| t3 | Print cities grew faster after 1500; Brethren regions pre-primed (Dittmar, Akçomak) | ❌ uns | ❌ 0.163 | ❌ | ❌ | ✅ | 🔍 tool-fetch (quotes Dittmar's growth finding) | ✓ checked, no comment (agrees with app) |
| t4 | Distance from Wittenberg a major determinant (Cantoni) | ✅ sup (**proof_state: partial** → badge "NOT PROVEN AS WRITTEN"; uncovered: "reform was safer once powerful neighboring states had committed first") | ❌ 0.011 | ❌ | ❌ | ✅ | 🔍 tool-fetch (quotes the distance + strategic-neighborhood sentences) | ⚠ verdict_wrong — "I would probably say unsupported. Can we use NOT PROVEN AS WRITTEN in the viewer as a prompt for looking for the supporting sentence in the source and THEN decide (un)supported?" |
| t5 | Literacy below ~20% through the period; one-cause reduction cautioned against (Eskelson, Becker) | ✅ sup (full covering, **partial_support** chip) | ❌ 0.483 | ✅ | ❌ | ✅ | 🔍 tool-fetch (quotes both literacy sentences) | ⚠ verdict_wrong — "very interesting — the system also finds a CONFLICTING claim (1871 literacy differences explain economic development). This should refute the claim, or if enough support exists elsewhere, mark it as a crux — a system we have not devised yet. Save this information." |
| t6 | Ottoman pressure reduced Christian-state conflict exactly when Protestantism consolidated; room to survive "regardless of print" (Iyigun) | ❌ uns | ❌ 0.05 | ❌ | ❌ | ✅ | ✍️ author-fix — missing: "regardless of print" | ⚠ "the app's reason is WRONG — Iyigun's Shaw (1976) and Coles (1968) quotes perfectly support the Ottoman-pressure claim; only 'regardless of print' is unsupported" |

Own claims (t0 structural header, t7 opinion) — author checked t7 clean.

## Legend
- Cols 2–4 judge ONLY the sentences the viewer shows ("sufficiency", the author
  standard); col 2 is MiniCheck-Flan-T5-Large support probability (≥0.5 = ✅),
  col 3 the SemanticCite-Qwen3-4B checker (ollama), col 4 Sonnet with the full
  shown block (covering + spans).
- Col 5 (Opus) reads the top source chunk directly, ignoring the tool's picks.
- Col 6 (Fable, grader-v2 prompt) sees shown evidence + a ~20k-word source
  section and names the expected outcome + missing components + proof quotes.
  🔍 tool-fetch = "the proof exists in the source, the tool didn't show it";
  ✍️ author-fix = "the text overreaches the source".

## Takeaways

1. **The eggs pattern reproduces on fresh material**: claims mostly TRUE per
   source (Opus 5/6, Fable finds proof quotes in 4/6) while the DISPLAYED
   sentences almost never suffice (Sonnet 0/6, MiniCheck 0/6). The author's
   marks again track the sufficiency columns, not the truth columns —
   including overturning two of gemini's three ✅ (t1, t4) for thin shown
   evidence. The author standard IS sufficiency-of-what-is-shown.
2. **t6 = concrete false-unsupported miss**: the Shaw/Coles passages in
   iyigun2008 prove the Ottoman-pressure component (author + Opus + Fable all
   found them) but retrieval surfaced only one not-supporting sentence, and
   the app's reason text denies precisely what the source says. Goes to the
   judge/retrieval false-unsupported evidence pile (with essay-t3 / pots-t7
   re-anchor item). The "regardless of print" component is genuinely
   unsupported — Fable's author-fix names exactly the component the author
   named.
3. **t4: author endorses the third-state WORKFLOW** ("NOT PROVEN AS WRITTEN →
   prompt to look in the source → then decide supported/unsupported"). The
   badge itself was already on the card (round-8 fix B); what the author
   describes is the deferred P4 full version (third state as an actionable
   intermediate, not just display). New data point for that author decision.
4. **t5: NEW feature class — contradiction handling** (author ask, saved to
   IDEAS.md): a shown not-supporting sentence can be an actual COUNTER-claim
   (Becker's 1871 literacy→development finding vs the claim's framing). Author:
   it should refute, or with support elsewhere become a crux. Today the card
   just labels it "judged NOT supporting" — no contradiction concept exists
   anywhere in the pipeline.
5. **Open-weight columns**: MiniCheck is uniformly stricter than every other
   judge (max 0.483) — usable as a cheap "needs more evidence" tripwire, not
   as a verdict. The Qwen checker (t1/t5 ✅) lands between MiniCheck and
   Sonnet and agreed with the author's clean rows.
6. **Fable grader on 6/6 rows named provable proof quotes or the exact missing
   component the author independently named** — strongest column-vs-author
   alignment in the table; worth remembering it graded 0/6 "supported"
   (strict on shown evidence, like the author).

## Addendum: DeepSeek graders on the same rows (author ask, 2026-07-12 afternoon)

Same grader-v2 prompt, same six claims, three DeepSeek models via API
(columns/grader_ds_{reasoner,chat,v4flash}.json; total cost ≈ $0.15–0.25 for
~534k input tokens). Question: can a cheap strong model replace Fable/Opus as
the tier-2 grader (`IDEAS`/system-design discussion: gemini tier-1 + big-read
grader tier-2)?

| row | fable | ds-reasoner | ds-chat | ds-v4-flash |
|---|---|---|---|---|
| t1 | tool-fetch | tool-fetch | tool-fetch | tool-fetch |
| t2 (author: figures unproven) | author-fix ✓figures | author-fix ✓figures | tool-fetch ✗ | author-fix ✓figures |
| t3 | tool-fetch | author-fix ("primed" synthesis) | author-fix | author-fix |
| t4 | tool-fetch | tool-fetch | author-fix (names the uncovered neighbor component) | tool-fetch |
| t5 | tool-fetch | author-fix | author-fix | tool-fetch |
| t6 (author: "regardless of print") | author-fix ✓exact | author-fix ✓exact | author-fix ✓exact | author-fix ✓exact |

Findings:
1. **Action agreement with fable: v4-flash 5/6, reasoner 4/6, chat 3/6** — and
   every divergence is remedy-level (tool-fetch vs author-fix), never
   fail-vs-supported. **No grader output "supported" on any row; zero
   leniency/false-support observed** (weak test of the eggs-t12 blind spot —
   no baited supported rows — but nothing alarming).
2. **All four named the author's exact t6 missing component** ("regardless of
   print"), and all four found real Ottoman-pressure proof passages the
   pipeline never showed. On t2 only ds-chat missed the unproven figures.
3. **Verbatim proof-quote audit** (normalize + search all source text):
   DeepSeek quotes were 100% verifiable once PDF-ligature folding was applied
   (3 initial flags were ﬁ/reflow artifacts; 1 reasoner quote partial-match).
   **The single genuinely unverifiable quote in the whole table was FABLE's**
   (t3, a Becker/Woessmann sentence not present in our akcomak2016 copy) —
   i.e., the verbatim gate is necessary for EVERY grader model and it works.
4. The t3/t5 divergences are actually informative: the DeepSeek models flag
   the author's interpretive syntheses ("primed to exploit new ideas") as
   author-side overreach where fable says fetch-more-evidence — a defensible,
   arguably author-aligned reading (author marked t5 too).

**Conclusion**: a DeepSeek tier-2 grader is viable. deepseek-v4-flash at
~$0.09/$0.18 per M ($0.02–0.15 per text) tracked fable best on actions AND
named the author components; ds-reasoner is the careful-reader alternative at
a few × the cost. Design requirement either way: the deterministic verbatim
quote gate (with ligature folding) before any quote is shown. Next validation
step before building: repeat on a text with known-supported rows (eggs) to
test the false-support blind spot directly.
