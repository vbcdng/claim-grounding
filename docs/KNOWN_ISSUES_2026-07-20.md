# Known issues — pre-deadline self-audit, 2026-07-20

On deadline day we ran a systematic three-part audit of this public repo
(silent-degradation paths in the code, fresh-machine setup traps, and a
judge's-eye reproduction of every claim in FOR_REVIEWERS). The serious
finding it produced was fixed the same day (see "Fixed today" at the
bottom). Everything else is listed here instead of being rushed in hours
before the deadline: **none of it affects the published numbers** — the
audit reproduced every headline figure offline from the checked-in data —
and all of it will be repaired after the competition judging ends.

If you hit something on this list, the workaround is next to it.

## Things a reviewer might actually hit

1. **First run of the README's "explicit command" on a brand-new machine.**
   The command includes `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`. Those
   variables also block the one-time (~440 MB) download of the local
   similarity model, so on a machine that has never run the tool the
   command fails with a raw HuggingFace error. **Workaround: drop the two
   `..._OFFLINE=1` variables for the first run** (they only exist to keep
   later runs from touching the network). The no-argument wizard does not
   have this problem.

2. **Two dead references in `benchmarks/wice_anchor/README.md`.** Its
   contamination-audit note cites `NIGHT_LOG_2026-07-12_accB.md` and
   `FIRST_CHECK_RUN.md` — internal working notes that are not part of this
   repo. The 12-row contamination list itself is in the JSONL and checks
   out; only the two pointers are dead.

3. **Reproducing the "58 rows" figure from Test 1.** The 58 counts rows
   where the final label found real **or partial** support for a claim the
   tool called unsupported. A script that counts only full "supported"
   labels gets 10. Same data, stricter denominator — the doc sentence will
   be clarified.

4. **The WiCE scorer prints a "FALSE-SUPPORT FAILURE" banner on 6 held-out
   batches.** That banner is the scorer doing its job: those rows are
   exactly the 3 base / 6 adjudicated false-supports the submission
   discloses. It is not a new failure you discovered.

5. **`--second-opinion` runs only: a fabricated "agrees" is possible.** If
   the second model's API call itself fails on a claim already judged
   unsupported, the failure is scored as agreement and no chip appears.
   The true cause is visible in the row's `second_opinion.reason` in
   `analysis.json` ("no LLM response"). The arbiter pass already handles
   this correctly (no response → no annotation); second-opinion will be
   brought to the same standard.

## Known gaps queued for repair (found in the same audit)

6. **Scanned or unreadable PDF sources degrade too quietly.** A source
   whose text layer is empty produces claims marked plain "unsupported —
   no_source_sentences" (styled like any other unsupported) instead of a
   distinct "could not be checked" state; in a multi-citation claim an
   unreadable source contributes nothing without any note on the card.

7. **Malformed citation markers are dropped silently.** `[[my key]]` (with
   a space) or `[[key]` doesn't match the marker syntax, so the citation is
   lost and the passage becomes an unchecked "own" claim with no warning.
   The wizard's marker check uses a looser pattern than the parser, so it
   cannot catch these either.

8. **A transient error can permanently skip one claim's covering-set
   audit.** The `covering_checked` bookkeeping is set even when the pass
   raised, so incremental re-runs never retry it for that claim
   (`--full` does).

9. **`--argument-map` failures can render as a clean result.** If some of
   the three assessment passes throw, the panel shows "no cruxes
   identified" / "sources look independent" instead of saying the pass
   failed; if all three throw, the panel is silently absent. Console
   warnings are the only signal. (Opt-in flag; verdicts unaffected.)

10. **Fresh-machine polish.** A failed similarity-model download has no
    friendly error (unlike the polished API-key preflight); a missing
    `claude` CLI under `--backend claude-code` exits with a raw traceback
    (the message inside it is correct); `--open` builds a `file://` URL
    that is wrong on Windows (the viewer path is always printed, so open
    it manually).

## Git history note (please read if you cloned early)

The public repo's history was rewritten once, on 2026-07-20, to remove the
extracted text of two paywalled sources that must not be redistributed
(finding F-1 of an external review). **If you cloned before 2026-07-20, or
`git pull` complains about diverged histories, re-clone — or run
`git fetch && git reset --hard origin/master`.** The history will not be
rewritten again; everything since lands as ordinary commits.

## Fixed today (2026-07-20, before the deadline)

- **Outage verdicts.** A model-API failure mid-run used to score the
  affected claims "unsupported", and incremental re-runs reused those
  verdicts forever. Now such claims are flagged (`judge_error`), the run
  ends with a warning listing them, the viewer chips them "not fully
  judged — API failed", and a plain re-run retries exactly those claims.
- README documents the default-on arbiter pass and its DeepSeek key.
- A dead reference at the top of `docs/PRINTING_SIX_JUDGE_TABLE.md`.
