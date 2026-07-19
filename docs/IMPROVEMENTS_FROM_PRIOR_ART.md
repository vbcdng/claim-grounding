# What to adopt from the prior-art scan

Decision doc, 2026-07-05. Companion to `docs/PRIOR_ART.md`. Each source below was read
at implementation depth (README / method section / eval code), then mapped to a *specific*
weakness in our system. Verdict per item: **adopt the idea / reuse the code / note for later**.
Nothing here is shipped yet — anything touching `matcher.py`, prompts, or config needs the
3-paper ship-gate on fresh runs (~$0.5 each), and budget is ~$4–4.5 of the ~$5 cap, so the
sequencing below is built to validate **offline on cached data first** and pay for **one**
gate run only when a tier is ready.

## License reality check (can we vendor code wholesale?)
| Source | License | Wholesale-reusable? | Why |
|---|---|---|---|
| **SemanticCite** | MIT | **No — borrow the design** | LangChain/ChromaDB/Streamlit stack fights ours (litellm + local SPECTER + server-free viewer). Its fine-tuned Qwen3 judge needs a GPU and would replace Gemini. Reuse the *hybrid-retrieval idea*, not the code. |
| **sciwrite-lint** | MIT | **No — reimplement 2 ideas** | Qwen3-8B/vLLM/GROBID, GPU-heavy. The two ideas we want (escalation ladder, citation-purpose) are small; reimplement in our style. |
| **MiniCheck** | Apache-2.0 (code + Flan-T5/RoBERTa/DeBERTa); Bespoke-7B is **non-commercial** | **Yes, optionally, as a library** | `pip install`, `MiniCheck(document, sentence)->[0,1]`. Only the permissive small models. GPU-preferred (CPU undocumented). The one genuine vendor candidate — but an optimization, not core. |
| **ALCE** | method only (its T5-11B TRUE model is too heavy for our CPU box) | **No — reimplement the logic** | Reimplement recall/precision with our Gemini judge over concatenated evidence. |
| **DeepSciVerify** | code "on request", not released | method only | Take the NEI-escalation *pattern*. |
| **MEG (2404.15588)** | no code released | method only, future | Set-Cover minimal-evidence framing; advanced version of ALCE. |

Bottom line: **no source is worth vendoring wholesale** except MiniCheck as an optional
pre-filter. The value is in a handful of well-tested *ideas* that slot into our existing
pipeline. That's the honest read — our architecture (server-free, CPU-local, litellm) is
different enough that importing anyone's stack would cost more than reimplementing the idea.

---

## Tier 1 — fixes priority-7, cheap, offline-validatable (do first)

The priority-7 bug (partial-check false-alarms: 6/7 spurious) has a two-part root cause —
**retrieval misses the title/abstract**, and **the combined judge sees only the one cosine
window**. Three converging fixes, all validatable on the 7 flagged claims + cached data for
pennies before any gate run:

### 1. Hybrid retrieval — stop missing the title/abstract  *(SemanticCite)*
Our candidate retrieval is pure SPECTER cosine (semantic). SemanticCite uses **BM25 + dense +
rerank**; the lexical arm is exactly what surfaces a claim that restates a paper's *title* or
*abstract* — the precise evidence our partial-check misses today.
- **Minimal version (do this first):** always inject each cited source's **title + lead
  sentences (abstract)** into the judge's candidate set, regardless of cosine rank. Zero new
  deps, offline, kills most of the 6/7 false alarms directly.
- **Fuller version:** add a BM25 lexical retriever alongside cosine, union the candidates.
  Small (`rank_bm25`, pure-Python), still offline.

### 2. NEI-triggered context escalation  *(DeepSciVerify trigger + sciwrite-lint ladder)*
This is our roadmap priority-7 "feed the combined judge the full source," now with a concrete,
non-hand-wavy design:
- DeepSciVerify escalates on an **explicit "Not Enough Information" verdict** (deterministic
  label, not a fuzzy confidence threshold) — resolves ~2/3 of cases on the cheap tier and
  only pays for full context on the hard ones.
- sciwrite-lint gives the ladder shape: **narrow → wider context** (sentence → paragraph →
  section), fan out top-N, stop on a conclusive verdict.
- **For us:** when the combined/partial judge returns unsupported/NEI on the cosine window,
  **re-judge against the source's cached decomposed claims** (which already include
  title/abstract sentences) *before* flagging `partial_support`. Only escalate on doubt →
  cheap. This is the "full-source fix" the roadmap already blocks partial-check re-enablement on.

### 3. Put partial_support on a principled definition  *(ALCE)*
ALCE's exact, verified method (from `eval.py`): a statement's citations pass **recall** iff the
**concatenation of ALL cited passages entails it**; **precision** flags any single cited source
that's unnecessary ("over-cite"). Reimplement with our Gemini judge over the (now better,
per #1/#2) evidence:
- `partial_support` becomes: *the union of cited evidence does not entail the claim* — a real
  compositional gap, not a heuristic. Directly targets the "salient-constraint checking"
  failure named in arXiv 2604.10990 and the aggregation failure in "Merging Facts" (ACL 2024).
- **Free bonus signal:** ALCE precision gives us an **"over-citation"** nudge (a cited source
  that adds nothing) — a natural amber chip alongside partial-support.

**Together, 1–3 are the path to re-enabling partial-check by default** — the roadmap's stated
goal. Build order: #1 (offline, instant) → validate on the 7 flagged claims → #2 → #3 → one gate run.

---

## Tier 2 — new capability, on-brand, moderate effort

### 4. Citation-purpose classification  *(sciwrite-lint's best idea)*
Not every citation is meant to be "supported." sciwrite-lint classifies purpose —
**evidence / contrast / method / attribution / context** — with graduated weight: *"an
unsupported evidence citation is serious; an unsupported context citation barely matters."*
- **For us:** ~1 tiny cached call per citation (the `own_claims.py` own-split classifier is the
  exact template — cache per model+prompt hash, "a nudge, never a veto"). A claim citing a
  source for *contrast* ("unlike Smith 2020…") or *method* ("using the approach of Jones…")
  shouldn't be judged for support the same way an *evidence* citation is.
- **Payoff:** cuts a whole class of false *positives* (unsupported-verdicts on citations that
  never needed support), the mirror image of what own-split does for uncited claims. High value,
  fits our design cleanly, low risk (it down-weights, never flips to a hard fail).

---

## Tier 3 — note, don't do now

- **MiniCheck pre-filter (cost):** run the local Flan-T5 classifier first, only send borderline
  cases to Gemini. Real savings, but adds a GPU-preferred dependency and a second model to
  maintain — and NEI-escalation (#2) already resolves most cases cheaply. Our cost is dominated
  by *cached* source decomposition, not steady-state judging, so this is a low-priority
  optimization. Revisit only if judge cost becomes the bottleneck.
- **SciNCL embeddings (recall):** beats SPECTER 9/12, but swapping = re-encode everything +
  re-validate the whole gate (disruptive, marginal vs. #1). Prefer adding BM25 (#1, additive).
  Keep SciNCL as a future option if #1 proves insufficient.
- **MEG / Set-Cover compositional (2404.15588):** the advanced version of #3 (minimal evidence
  groups, +18–35% on WiCE/SciFact over prompting) — but no code released and more complex.
  ALCE's union-entailment (#3) is the right first step; MEG is the follow-on if #3 underperforms.
- **Compositional-failure regression tests:** build named test cases from "When Verification
  Fails" (salient-constraint over-acceptance) and "Merging Facts" (D-FActScore, entity-ambiguity
  aggregation). A testing improvement, not a pipeline change — cheap, do alongside Tier 1.
- **PaperTrail UX finding:** their claim-evidence interface *lowered inappropriate trust* but
  users still relied on suggestions — validates our confidence-chip + triage direction; a design
  note, not a code change.

## Sequencing & budget
Build **Tier 1 #1 → #2 → #3** and validate **offline on the 7 flagged claims + cached
analyses (~cents)**. Only when Tier 1 is coded and offline-green do we spend **one** 3-paper
gate run (~$0.5) to confirm no regression on paper1/bentonite/chimpanzee and, ideally, that
partial-check can flip back to default-on. Tier 2 (#4) is a separate, later change with its own
gate. Flag budget before that gate run — it may be the last one the cap allows.
