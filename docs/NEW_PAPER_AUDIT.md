# New-paper mini-audit — hand-check ~8 verdicts before trusting a run

Every ground-truth number this tool has (the paper1 audit, the judge bench, the
regression harness) comes from ONE paper. A new paper is new territory: different
writing style, different source quality, different failure modes. Before treating
the viewer's colors as meaningful, spend ~15 minutes checking eight verdicts by
hand. This is a ritual, not tooling — the point is that a human reads real
evidence before relying on the run.

## Why 8, and why mostly greens

If the judge were badly wrong on this paper (error rate ≥ 25%), eight random
checks catch at least one error with ~90% probability. A clean 8/8 does NOT
prove the run is correct — it rules out gross failure, cheaply.

Sample supported-heavy (5 green, 3 red): a wrong red gets caught anyway when you
repair it and re-read the evidence, but a wrong green (a false positive, the
t37 class) is invisible unless you go looking. Greens are where silent damage
lives.

## The ritual

1. **Pick the sample.** 5 random supported + 3 random unsupported *judged*
   claims (skip `source_file_missing` ones — those are input problems, not
   judgments):

       venv/bin/python3 - <<'EOF'
       import json, random
       a = json.load(open("data/<run dir>/analysis.json"))
       j = [c for c in a["text_claims"]
            if c["verdict"] in ("supported", "unsupported")
            and not str(c.get("reason", "")).startswith("source_file_missing")]
       sup = [c for c in j if c["verdict"] == "supported"]
       uns = [c for c in j if c["verdict"] == "unsupported"]
       random.seed(8)   # fixed seed -> everyone checks the same sample
       for c in random.sample(sup, min(5, len(sup))) + random.sample(uns, min(3, len(uns))):
           print(c["id"], c["verdict"].upper(), "-", c["text"][:90])
       EOF

2. **Judge each one yourself.** In the viewer: read your sentence, open the
   cited source at the evidence (deep-link / highlighted text), and decide —
   does the source actually state or entail the citable assertion? Ignore the
   tool's verdict until you've decided.

3. **Score it.**
   - **7–8 of 8 agree** → trust the run *as a review-priority map*: colors tell
     you where to look first; they are still not ground truth.
   - **6 or fewer agree** → do NOT start repairing text. First check for wrong
     source files (`download_report.md`, content-check warnings — wrong files
     caused 4 of paper1's 11 audit-confirmed reds), then re-run with
     `--second-opinion` and read its flags, and consider a fuller audit. Note
     the direction: wrong reds = the judge is too strict on this paper's style;
     wrong greens = false positives — the dangerous kind, stop and investigate.

4. **File every disagreement** via the card's *verdict wrong* mark (+ note) and
   export review.json — `/apply-review` writes them to `verdict_feedback.json`,
   where they permanently outrank model verdicts (author-disputed chip, excluded
   from second opinions).

5. **While you're there, glance at the header:** the warnings banner (unusable
   sources), the "unverifiable (source file missing)" count, and the
   "citation suggestions" count (uncited factual assertions worth citing).

## Relation to the other trust layers

- `benchmarks/regression_check.py` guards **config changes** against paper1's
  hand-audited ground truth — it says nothing about a new paper.
- `--second-opinion` is a cheap model-vs-model flag pass — worth running on any
  paper you care about, but models share blind spots; it does not replace the
  eight human checks.
- This ritual is the only step where a **human** reads real evidence on the new
  paper before the run is trusted. Do not skip it because the other two passed.
