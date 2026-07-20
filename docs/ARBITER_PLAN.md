# Light-touch arbiter — implementation plan (drafted 2026-07-12; BUILT + VALIDATED same day)

## VALIDATION RESULTS (2026-07-12 afternoon — all steps below executed)
Shipped as `modules/papertrail/arbiter.py` + `--arbiter` (commits 43093d1,
2a4684f). Suite 779 OK.
1. **Owner-ruled texts** (fresh runs, `data/arbval_{pots,forager}`): forager t1
   split exactly per the owner/grader ruling (Lee = author-fix, verified Ache
   proofs quoted); pots t7 returned the EXACT "beneath hearths or doorways"
   proof sentence (the gate-v2 watch row, caught live); pots t6 tool-fetch with
   3 verified proofs + **1 hallucinated quote dropped by the verbatim gate in
   the wild**; every owner-clean row (forager t0/t2/t3, pots t5) correctly
   never escalated. CAVEAT: pots t1 — the arbiter flagged the end-grain
   component the owner ruled common knowledge (prompt exemption didn't hold;
   same call Sonnet+grader made in r4; stays a dismissible chip — calibration
   material for prompt v2).
2. **Leniency bait** (`data/arbval_firstcheck`, WiCE b2 incremental): all 6
   refuted rows → author-fix, **0 false rescues**, 0 quotes dropped; t2 got a
   verified CONFLICT sentence; the arbiter independently caught the essex
   date-class false-unsupported ("looks proven", WiCE agrees) + milwaukee.
   Aldermoor escalated via conflict_candidate (multi-source per-source
   evidences are judged not-supporting by construction) and correctly returned
   supported/no-conflict — known trigger-volume cost, right answer.
3. **Full 6-block gate GREEN with --arbiter on** (fresh --full runs):
   24/24 + 5/5 + 12/12 + 8/8 + 2/2 + 2/2 — verdicts untouched by
   construction, scorers unaffected by the new field. Paper1's arbiter run
   flagged "proof may exist" on t68 (a known strictness case) and conflicts
   on t6/t23/t42 (owner: worth a look).
4. **Actual cost** (litellm-metered): $0.28 TOTAL for all nine validation
   runs; paper1 (44 claims, 60 sources — the realistic big case) $0.18;
   small texts $0.004–0.024 each.
5. **Owner gate remains**: review `data/arbval_*/viewer.html` → ship ruling +
   the default-on decision + where the common-knowledge line sits.

---

## What it is
A tier-2 pass by a strong-but-cheap model (default `deepseek/deepseek-v4-flash`)
that re-reads ONLY the claims the run itself flags as contested, with the
grader-style big-context prompt, and renders its finding on the card as
chips/commentary — **nudge, never a veto** (house rule; verdict field and all
gates untouched).

Evidence base (all committed):
- `docs/GEMINI_FAILURE_BREAKDOWN_2026-07-12.md` — the trigger set catches
  15/15 owner/Fable-confirmed verdict-level gemini failures post-fix-A;
  0 confirmed failures escape it; volume 30–60% of claims.
- `docs/PRINTING_SIX_JUDGE_TABLE.md` addendum — v4-flash tracks Fable 5/6 on
  grader actions, names the owner's exact missing components, quotes are
  verifiable; the verbatim gate caught the one hallucinated quote (Fable's).

## Trigger set (a claim is escalated iff any of)
1. `verdict == unsupported` (and not `source_file_missing`) — the
   false-unsupported class (r5 t1/t3, printing t6).
2. `verdict == supported` AND (post-audit `covering.uncovered` non-empty OR
   `partial_support`) — the core-amber / NOT-PROVEN class (r5 t0, r6 t1,
   printing t4).
3. Any displayed evidence sentence judged not-supporting on a supported claim
   — the conflict-candidate class (printing t5).

Never escalated: clean supported-full rows (the 0-false-support record says
gemini needs no help there) and `own` claims.

## Arbiter call (per escalated claim, 1 call)
- Prompt: derived from `config/prompts/pt_owner_grader_v2.txt` — claim +
  SHOWN evidence block (`loop_round.shown_block` logic moves into the module)
  + `relevant_section` source context (§20k-word cap), plus TWO additions:
  (a) the owner's common-knowledge exemption (r4-t1: do not flag components
  a general reader needs no citation for; mirrors the pick-verify grey rule);
  (b) an explicit conflict question when trigger 3 fired ("does any shown
  sentence CONTRADICT the claim?").
- Output JSON: `{action: supported | tool_fetch | author_fix,
  missing_subclaim, proof_sentences[], conflict: {sentence, why} | null,
  why}`.
- **Verbatim quote gate (deterministic, mandatory)**: each proof sentence is
  normalized (lowercase, alnum-only, ligature folding ﬁ/ﬂ/ﬀ) and must appear
  as a substring of the cited sources' normalized text; unverifiable quotes
  are DROPPED and counted (`quotes_dropped`) — never displayed. An arbiter
  result whose every quote drops is shown without quotes and marked
  low-confidence.

## Rendering (display-only)
- unsupported + arbiter `tool_fetch` → blue chip **"proof may exist"** + the
  verified quotes ("the arbiter found these sentences in <source>"). Feeds
  the review loop / a later verdict-path round; v1 changes no verdict.
- unsupported + arbiter `author_fix` → grey note "arbiter concurs: <missing
  component>" (strengthens the card, no change).
- supported-with-gaps + arbiter `author_fix` → the NOT-PROVEN badge gains the
  arbiter's named missing component (verbatim from `missing_subclaim`).
- supported-with-gaps + arbiter `supported` → grey "arbiter: gaps look
  minor" note (chip only; the amber stays — ambers are the trigger layer and
  must not be self-erasing).
- conflict found → amber **"conflicting evidence?"** chip + the sentence,
  per the owner's printing-t5 ask (the crux wiring is a later round; the chip
  records the information so it is not lost).
- Claims with an author ruling in `verdict_feedback.json` are skipped
  (mirrors `--second-opinion`).

## Plumbing
- New module `modules/papertrail/arbiter.py` (injectable LLM, offline-
  testable); results cached per (claim-hash, model, prompt-sha) in the run
  dir, carried through incremental reruns like second-opinion chips.
- CLI: `--arbiter [model]` on `verify_my_text.py`, **opt-in v1**; bare flag =
  `deepseek/deepseek-v4-flash` (needs `DEEPSEEK_API_KEY`; under
  `--backend claude-code` a bare flag routes to `claude-code/sonnet`, $0 —
  same convention as `--second-opinion`).
- Estimator: one caveat line "(+ arbiter on up to N flagged claims — ~1 big
  call each, ≤ ~$X)" priced via `addon_worst_case`-style constants
  (~30k in / 500 out per call).
- `analysis.json`: `claim.arbiter = {action, missing_subclaim, proofs[],
  conflict, quotes_dropped, model}`; ARCHITECTURE §6 gains the section
  (nudge-never-veto family).

## Validation plan (pre-ship; NO eggs / NO violence texts — safety-layer rule)
1. **Owner-ruled neutral texts, fast to re-check**: fresh scratch runs of
   **pots** (`round_4` text) + **forager** (`round_6` text) on current master
   with `--arbiter`. Expected against the owner's existing rulings:
   - pots t6 / forager t3 (named-specific, now ambered by fix A) → escalated;
     arbiter must name Finland / Agta-Philippines.
   - forager t1 (core ambers) → escalated; arbiter must split it the grader's
     way (Lee-revision = author_fix, Ache = tool_fetch with verified quotes).
   - pots t3/t7 (unsupported; grader said proof exists) → escalated; arbiter
     quotes must pass the verbatim gate.
   - forager t0/t2, pots t5 (clean supported-full, owner-approved) → **must
     NOT be escalated at all** (trigger-set precision check).
   - pots t1 (common-knowledge ruling) → if escalated, arbiter must NOT flag
     the end-grain component (exemption test).
2. **Leniency / false-rescue bait (the eggs-t12 blind-spot test, on safe
   material)**: `benchmarks/synth_docs.py` engineered-failure docs (fresh
   seed) + the 6 WiCE b2 `not_supported` rows — every one triggers (all
   unsupported); the arbiter must NOT output `tool_fetch`/`supported` on
   rows whose proof genuinely does not exist. Target: 0 false rescues; any
   miss is a stop-and-report.
3. **Suite + gates**: full unittest suite; both `check_all.sh` layers on
   fresh `--full` runs WITH `--arbiter` on — verdicts must be untouched by
   construction (arbiter writes only the `arbiter` field), scorers must not
   choke on the new field. Cost: one gate ≈ $0.45 + arbiter pennies.
4. **Owner gate**: regenerated pots + forager viewers with arbiter chips →
   owner reviews (minutes; texts already known). Ship/no-ship + default-on
   decision are the owner's.

## Cost
Per text: trigger volume 30–60% of claims × ~30k tokens → **$0.01–0.08
(v4-flash)**; $0 in dev via claude-code/sonnet. Validation total ≈ $0.5–1
(dominated by the fresh gate runs, which were due at the next verdict-path
change anyway).

## Explicitly out of scope for v1 (later rounds, each needs an owner ruling)
- Verdict-path use of arbiter output (feeding component_rescue / flipping
  false-unsupporteds) — the natural round 2, gated by unanimity like rescue.
- Crux wiring for confirmed conflicts (IDEAS.md refute-or-crux).
- Default-on.
