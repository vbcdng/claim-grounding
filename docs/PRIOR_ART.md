# Prior art & competitive landscape

Research pass 2026-07-05. Question: who else grounds cited writing against its
sources? What's the closest analog, where's the genuine gap, and what does the
research say about doing it well? Method: four parallel web-research sweeps
(commercial products, open-source RAG/groundedness tooling, academic literature,
open-source GitHub analogs). The two closest open-source repos and the closest
commercial player were re-verified by direct fetch; classic academic works are
independently known-real. Items I could **not** verify are flagged inline.

## Bottom line
- **The core primitive — "LLM judges whether a claim is supported by a context" — is
  not novel.** It's the same idea in every RAG-eval library (RAGAS, TruLens, DeepEval,
  Phoenix, promptfoo) and in specialized fact-check models (MiniCheck, AlignScore, HHEM).
- **But "verify a *human-authored, cited document* against *its own declared
  bibliography*, claim-by-claim, in a persistent reviewer UI" is a small, real, and
  only-just-being-worked niche.** As of late 2025 / 2026 there's a visible *wave* of
  near-identical tools — we are not alone, but we are early and the space is not crowded.
- **Closest thing to us in open source: `sciwrite-lint` and `SemanticCite`** (both
  verified real). **Closest commercial: GroundedAI/Veracity** (publisher-facing).
  **Closest architecture in research: DeepSciVerify** (preprint, no product).
- **Our most defensible, apparently-unreplicated pieces**: the *server-free persistent
  reviewer viewer* with triage + repair-loop export; the *`omitted` verdict* (relevant
  source material you didn't cite); *`own` claims* as a first-class category;
  *incremental content-hash re-runs*.

---

## Closest analogs (verified real)

### sciwrite-lint  — the nearest functional twin (open source)
`github.com/authentic-research-partners/sciwrite-lint` · Python · MIT · ~23★ · active (2026).
A 23-check linter for scientific manuscripts. One of its checks is exactly our core:
downloads full text of cited papers (GROBID parsing, ~14 OA sources), and a **local LLM
judges whether each claim is supported by the actual source text**, with citation-purpose
classification (evidence/contrast/method) and graduated weighting. Also does reference
existence, retraction checking (60k+ entries), figure-vs-caption vision checks, and a
"SciLint Score."
- **Same as us**: decompose/parse sources → LLM-judge claim support against the cited paper.
- **Different from us**: no HTML/reviewer viewer (terminal/JSON), the claim-support check
  is one of 23 features rather than the whole tool, no described incremental-rerun/caching
  model, no multi-citation "component-complete" judging.

### SemanticCite — closest on the *verdict schema* (open source)
`github.com/sebhaan/semanticcite` · arXiv 2511.16198 · Python · **licence DEFECTIVE — README
claims MIT but no LICENSE file exists (GitHub API `license: null`, first-party 2026-07-05;
do NOT vendor — see `PRIOR_ART_REUSE.md`)** · ~23★ · v1.0.0 (Nov 2025).
"AI-powered citation verification with full-text analysis." Extracts claims from citations,
retrieves text via **hybrid dense + BM25** search, and classifies with a **fine-tuned Qwen3
judge** into **Supported / Partially Supported / Unsupported / Uncertain** + confidence +
evidence snippets.
- **Notable**: its "Partially Supported" class is essentially our `partial_support` flag —
  someone shipped that idea in Nov 2025. Worth reading how they scope it.
- **Different from us**: an infra/model contribution (retrieval + fine-tuned judge), no
  persistent reviewer UI / triage / repair-loop, not organized around citation markers in
  a drafted document the same way.

### GroundedAI / "Veracity" — closest *commercial* player  *(verified live)*
`groundedai.company` — automated citation verification for publishers & law: checks
references are "real, relevant, and appropriately used." Powers **Alchemist Review**
(`hum.works/review`), a peer-review assistant piloted with IEEE/OUP. Closed, enterprise.
- **Different from us**: **editor/publisher-facing** (screening *other people's*
  manuscripts at scale), not author-facing (grounding *your own* draft as you write).

### DeepSciVerify — closest *architecture* (research preprint, no product)
"DeepSciVerify: Verifying Scientific Claim–Citation Alignment via LLM-Driven Evidence
Escalation" (Sadeghi, Khajavi, Adhikari, Tessier), arXiv 2605.27710 *(verified)*. A
two-stage pipeline: judge against the abstract first, **escalate to full-text passages
only when needed** — 86.7 Micro-F1 on their SCitance benchmark, resolving 67% of cases
without full-text retrieval. Conceptually mirrors our cosine→judge→full-text-fallback
cascade (independent convergence — reassuring), and its "abstract first, escalate on doubt"
policy is a cheaper variant of our priority-7 full-source fix. Its sibling paper **CiteCheck**
(2605.27700, same author team) does *citation-existence/hallucination* detection — the other,
different problem.

---

## The adjacent field, by category (why the famous names are *not* competitors)

**RAG-evaluation libraries** — RAGAS (`faithfulness`), TruLens, DeepEval, Arize Phoenix,
promptfoo, LlamaIndex evaluators. All answer "is *this one generated answer* consistent
with the context *retrieved for it*," scored per pipeline run. No fixed author-supplied
bibliography, no citation markers as first-class objects, no persistence. The judging idea
overlaps; the *workflow* is a different problem (CI/observability, not document audit).

**Specialized fact-check models** (usable *inside* us as a cheaper judge, not competitors) —
**MiniCheck** (EMNLP 2024, Apache-2.0), **AlignScore**, **SummaC**, **Vectara HHEM**,
**Lynx** (Patronus). Small fine-tuned `(document, sentence) → [0,1]` classifiers: sub-second,
far cheaper per call than a general LLM judge, but single-context, no multi-source routing,
no reasoning trace. Note **Bespoke-MiniCheck-7B** and **Lynx** are **CC BY-NC (non-commercial)**
— a licensing constraint vs. our permissive stack. *Actionable: benchmark one as a cheap
first-pass filter before the LLM judge — directly relevant to our budget pressure.*

**Reference-existence / metadata checkers** (a *different* problem — "does the citation
exist / is the DOI real," not "does it support the claim") — RefChecker, Paperpile Citation
Checker, and the 2026 fabricated-citation audits (CiteAudit, reference-hallucination
detection). LLMs fabricate ~1% of citations in accepted papers — real problem, but not ours.

**Discovery / summarization / stance** — Scite (citation *stance* across ~1.6B statements),
Consensus & Elicit & SciSpace & ResearchRabbit (paper discovery / synthesis / chat-with-PDF),
Scholarcy (summarization), Turnitin/iThenticate (string-similarity plagiarism), Grammarly
Citation Finder ("may need a citation" + insertion). All adjacent; none open a cited source
to check whether it backs a specific written claim.

**Clarity — `claritybot.io`** *(verified live)* — a genuine adjacent competitor. AI
content-verification that explicitly flags "source/claim mismatch," "hallucinated
citations or facts," and "unsupported causal claims" with claim-by-claim reporting, and
uses **three independent domain-qualified reviewers** rather than one model — a close
parallel to our `--second-opinion`. Pay-per-review (~$1.61–$43 by length), closed, zero
retention. Author/content-facing. The nearest thing to us aimed at general (incl. academic)
documents, though it verifies claims broadly rather than being organized around a drafted
document's own bibliography + citation markers.

*Real but NOT a claim-verifier (relevance was overstated in first pass):* **scienceOS**
(`scienceos.ai`) — verified live, but it's a research-chat / PDF-extraction assistant
(query 230M+ papers, chat with uploaded PDFs, draft manuscripts). Its page describes **no**
claim↔cited-source verification. Reclassified as *discovery/extraction*, ~€7/mo.

---

## What the research says about doing this well (and how it maps to our roadmap)

The academic literature is the most useful output of this pass — it directly validates and
sharpens decisions we've already made. Classic works below are independently known-real;
2026 IDs are agent-reported.

- **Judge against the full evidence context, not an isolated sentence.**
  **MultiVerS / LongChecker** (NAACL Findings 2022, arXiv 2112.01640) encodes claim + the
  *whole abstract* jointly, precisely because rationale sentences lose meaning out of context
  (acronyms, anaphora). **This is exactly our ROADMAP priority 7** — the `--partial-check`
  false-alarm bug is that the combined judge sees only the one cosine window, not the source
  body. The literature says our fix direction is the right one.

- **"Component-complete multi-source" judging is a real, named-ish problem with a working
  template.** **ALCE** (EMNLP 2023, arXiv 2305.14627) checks entailment against the
  *concatenation of all cited passages* — a direct template for scoring our `--partial-check`.
  **HAGRID** takes the opposite policy (each source must independently suffice);
  **Minimal Evidence Group Identification** (TrustNLP@NAACL 2025) and **InteGround**
  (EMNLP 2025 Findings) are the closest formal treatments of multi-evidence synthesis.

- **General LLM judges have documented, specific weaknesses.** Claim-only lexical shortcuts
  (FEVER artifacts — a claim-only classifier hits 62% with no evidence), position/verbosity
  bias in LLM-as-judge, and — most relevant to us — **"salient-constraint checking"**:
  confirming the *salient* part of a claim while missing that a non-salient constraint is
  contradicted or uncited. "When Verification Fails: How Compositionally Infeasible Claims
  Escape Rejection" (Liu, Rao, Kim, Callison-Burch; arXiv 2604.10990, *verified*) shows
  models that saturate other benchmarks consistently *over-accept* such claims. That paper
  essentially *names our multi-marker over-support bug.*

- **Benchmark targets exist for the per-source judge step.** **SciFact** (EMNLP 2020,
  `github.com/allenai/scifact`) and **SciFact-Open** (2022) are the best direct evaluation
  target — same 3-class SUPPORTS/REFUTES/NEI scheme, sentence-level rationales. ALCE's
  methodology is the best template to adapt for scoring the multi-citation case. Caveat:
  SciFact-Open shows models tuned on small SciFact drop ≥15 F1 out-of-domain — mirrors our
  own overfitting worry.

- **Claim decomposition is known-hard.** **FActScore** (EMNLP 2023) — our conceptual cousin,
  decomposes into "atomic facts" — but 52% of auto-generated atomic facts needed human
  correction; **WiCE** (EMNLP 2023) does sub-sentence decomposition + *minimal supporting
  evidence subsets* (real partial-support cases). Sets expectations for our own decomposer's
  error rate.

- **Our embeddings could be upgraded.** We use **SPECTER** (ACL 2020). **SciNCL** (EMNLP
  2022, arXiv 2202.06671) beats SPECTER on 9/12 SciDocs metrics via better negative
  sampling; SPECTER2 exists (adapter-based) but has no peer-reviewed paper. A drop-in
  candidate if retrieval recall ever becomes the bottleneck.

---

## Where we're differentiated

Nothing found combines *all* of:
1. Split *prose* into claims **by citation marker** (claim ↔ the specific source it cites).
2. Decompose **each source** into atomic claims (not just chunk-and-retrieve).
3. 3-stage cosine → LLM-judge → full-text-extraction fallback.
4. A **server-free, persistent, reviewable HTML viewer** with per-claim confidence chips,
   human triage (wrong-source / rewrite / verdict-wrong), and export into a repair loop.
5. **Incremental content-hash re-runs** (reuse verdicts when text + source bytes unchanged).
6. The **`omitted`** verdict (relevant source material you *didn't* cite, ranked) and
   **`own`** claims (uncited author theses) as first-class, non-error categories.

**Correction (2026-07-05 second pass):** item 6 is *not* unreplicated — **PaperTrail**
(CHI 2026, below) decomposes both text and sources into claims and surfaces
supported / unsupported / **omitted** material, the same trio as our verdicts. So the
accurate claim is narrower: no *packaged, self-hostable, author-facing* tool combines all of
1–6, and **compositional multi-citation judgment (item 3's hardest part) remains
unimplemented in any shipped tool** (two independent negative results now confirm this).
The reviewer-workflow *product* (persistent server-free viewer + triage + repair-loop
export + incremental reruns) is the strongest differentiation; the *concepts* (decompose
both sides, supported/unsupported/omitted) are shared with the PaperTrail research prototype.
Items 1–3 are a solid, defensible engineering recipe but conceptually shared with
sciwrite-lint / SemanticCite / DeepSciVerify.

---

## Net-new from the Claude Science pass (2026-07-05, all verified by direct fetch unless noted)

A second, deeper source-hunt (via Claude Science) surfaced material the first sweep missed.
The high-value items, verified:

**PaperTrail** — arXiv 2602.21045, CHI 2026 (Martin-Boyle, Leckey, Brown, Kaur) *(verified)*.
"A Claim-Evidence Interface for Grounding Provenance in LLM-based Scholarly Q&A." Decomposes
**both** the answer and the source documents into claims/evidence and maps them to reveal
**supported / unsupported / omitted** material — the nearest published match to our verdict
model and viewer concept. Studied with 26 researchers. **Differs**: it grounds *LLM
scholarly-QA answers* against a corpus, is an interaction research prototype (not a packaged
permissive self-hostable tool), and doesn't do compositional multi-citation judgment.
(Name collision: unrelated to our internal `modules/papertrail/`.)

**SourceCheckup** — `github.com/kevinwu23/SourceCheckup` · Python · **no licence (GitHub API
`license: null`, first-party 2026-07-05 — do not vendor; the paper is the value: 88%
judge-vs-medical-expert agreement, see `PRIOR_ART_REUSE.md`)** · ~17★ · Nature
Communications 2025 (arXiv 2402.02008), Stanford (Kevin Wu et al.) *(verified)*. Splits an
answer into statements, pairs each with its cited source, scores whether the source supports
it ("fraction of statements supported by ≥1 citation"). A genuine open-source CORE analog.
**Differs**: audits *LLM answers* (medical domain, Mayo/UpToDate/r/AskDocs), per-source (not
compositional), research code not an author tool.

**GroundTruth** — `groundtruth.law` — commercial, closed *(exists; details partly
unverified)*. Tagline "Catch inaccurate citations"; per Claude Science it's author-facing
and places the cited source passage next to each claim for a *human* to confirm (legal-first,
human-in-the-loop, no source decomposition). The site is a JS app — only the tagline was
directly confirmable; the human-in-the-loop / legal-first detail is agent-reported.

**Valsci** — `github.com/bricee98/Valsci` · Python · GPL-3.0 · ~12★ · BMC Bioinformatics
2025 *(verified)*. Open, **self-hostable** (Docker/PM2, OpenAI-compatible incl. local LLMs)
large-batch scientific claim verification. Close in *spirit* (self-hostable, claim-level, LLM
judge) but **retrieves supporting literature itself from Semantic Scholar** rather than
checking a claim against the author's *pre-chosen* citations → adjacent, not our problem.

**Factiverse** — `factiverse.ai` — commercial, closed, Norwegian (Univ. Stavanger)
*(verified)*. Real-time checkable-claim extraction (110+ languages) + fact-check DB. Finds
sources across the web/DB; not tied to a document's own citations → adjacent (journalism/gov).

**Compositional / multi-evidence research (anchors our hardest feature):**
- **"Merging Facts, Crafting Fallacies"** — ACL Findings 2024 (Chiang & Lee) *(verified)*.
  Shows individually-verifiable facts combine into a non-factual whole; introduces
  **D-FActScore**. A second peer-reviewed anchor (with arXiv 2604.10990) for why per-source
  OR support is insufficient — the exact rationale for our `--partial-check`.
- **CiteME** — arXiv 2407.12861, 2024 (Press et al.) *(verified)*. Benchmark for attributing a
  claim to the correct paper; frontier LMs 4–18% vs 70% human — attribution direction, but
  claim↔paper linkage is first-class.

**Reported-real, not re-fetched this pass** (ACL papers with DOIs — lower stakes): ClaimVer
(EMNLP Findings 2024), CiteEval/CiteBench (ACL 2025), VeriScore (EMNLP Findings 2024),
WebCiteS (arXiv 2403.01774, Chinese attributed summarization), and several 2026 citation-
hallucination preprints (2606.23989, 2605.08583, 2601.05866, 2605.06635). ⚠️ Unverified:
CiteAudit (ResearchGate), HALLMARK baselines, Manusights.

**Reference-existence checkers (a bigger cluster than the first pass found — still a
*different* problem):** hallucinator, CheckIfExist (Univ. Turin, arXiv 2602.15871), SwanRef,
pvsundar/bibliography-verification-tool, Paperpal Reference Checker, Trinka Citation Checker,
and Chinese-language 文献引用真实性检验 (aigaixie). These verify a reference is *real /
well-formed*, never that it *supports* the claim.

**Two negative results worth recording** (from Claude Science, consistent with our own):
1. **No shipped tool implements compositional "all cited sources together" support
   judgment** — it exists only as a research failure-mode (Merging Facts; 2604.10990). Our
   strongest remaining differentiator.
2. **No non-English claim↔source *support* tool exists** — every non-English hit is a
   reference-existence/quality checker (Chinese market) or attribution/summarization (WebCiteS).

## Actionable follow-ups this surfaced (not yet scheduled)
- **Priority 7 is literature-endorsed**: feed the combined judge the full source context
  (MultiVerS), not the window. ALCE = the scoring template. arXiv 2604.10990 = the citable
  statement of the bug.
- **Cheap first-pass judge**: benchmark MiniCheck/AlignScore/HHEM (permissive variants only)
  as a pre-filter before the paid LLM judge — could cut per-run cost.
- **Read SemanticCite's "Partially Supported"** implementation — someone shipped our exact
  flag; learn from or differentiate against it.
- **SciFact as a regression target** for the per-source judge, alongside our 3 hand-audited papers.
- **SciNCL** as a SPECTER upgrade if retrieval recall becomes limiting.

## Verification status
- Verified by direct fetch: **sciwrite-lint**, **SemanticCite**, **GroundedAI/Veracity**,
  **Clarity**, **scienceOS** (real but reclassified — see below), and the five 2024–2026
  arXiv papers: **DeepSciVerify** (2605.27710), **CiteCheck** (2605.27700), **"When
  Verification Fails / Compositionally Infeasible Claims"** (2604.10990), **InteGround**
  (2509.16534), **Minimal Evidence Group Identification** (2404.15588) — all confirmed with
  correct titles/authors.
- Independently known-real (classics): FEVER, SciFact/-Open, MultiVerS, FActScore, WiCE,
  RARR, ALCE, AIS, AttributedQA, HAGRID, ExpertQA, SPECTER, SciNCL, MiniCheck, AlignScore,
  SummaC, RAGAS, TruLens, DeepEval, Phoenix, promptfoo, HHEM, and the named commercial
  discovery tools (Scite, Consensus, Elicit, SciSpace, Scholarcy, ResearchRabbit, Turnitin).
- **Corrections from the 2026-07-05 verification pass**: (1) **scienceOS** is a real product
  but does discovery/PDF-extraction, **not** claim↔source verification — the first-pass
  "mis-citation check" description was wrong; reclassified as adjacent. (2) **Clarity** is
  real and a *genuine* adjacent competitor (upgraded from "low-confidence"). (3) The
  "Petal = Paperpile" premise appears false and was dropped.
- **Still NOT personally verified** (agent-reported only): CiteAudit and the ~1% fabricated-
  citation audit figure. Low stakes — both are in the citation-*existence* genre, not ours.
