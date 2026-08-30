#!/usr/bin/env python3
"""Improvement-loop round harness (v1) — docs/IMPROVEMENT_LOOP_V1.md.

One command per stage; every column checkpointed per claim (atomic JSON), so
any interruption resumes. The round STOPS at the finished table (owner gate).

Stages:
  prep     text (+ citation map) -> data/loop_rounds/round_N/project/
           (my_text.md with [[key]] markers, refs file, sources incl. html->txt)
  app      run verify_my_text.py (production config) -> app/ + CLEAN viewer
  columns  background checkers: sonnet_suff / opus_truth / grader (Fable now,
           Opus after succession) — each checkpointed
  table    assemble table.md (+ owner review*.json if exported) -> OWNER GATE

Usage:
  venv/bin/python3 loop_round.py --round 1 prep --text <t.md> --map <map.json>
  venv/bin/python3 loop_round.py --round 1 app
  venv/bin/python3 loop_round.py --round 1 columns [--only grader] [--grader-model claude-code/fable]
  venv/bin/python3 loop_round.py --round 1 table

map.json (only needed for numbered-citation texts):
  {"citations": {"2": "karger2023", ...},           # [n] -> key
   "sources":   {"karger2023": "xpt_full.pdf", ...}} # key -> file in --sources-from
"""
import argparse, json, os, re, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from modules.papertrail import matcher  # noqa: E402
from modules.papertrail.llm_client import LLMClient, extract_json  # noqa: E402
import deep_check as dc  # noqa: E402

SUFF_PROMPT = os.path.join(ROOT, "config/prompts/pt_evidence_sufficiency_v1.txt")
GRADER_PROMPT = os.path.join(ROOT, "config/prompts/pt_owner_grader_v2.txt")
SECTION_FULL_WORDS = 30_000    # <= this: pass the whole source text
SECTION_CAP_WORDS = 20_000     # long docs: best contiguous section of ~this size

OPUS_TRUTH_PROMPT = """You are checking whether a cited source actually supports a claim
from an author's document. You are given the CLAIM and the most relevant passages
from the cited source paper(s). Decide, using ONLY these source passages, whether
the source(s) genuinely support the claim as written — is the claim TRUE according
to the source? Do NOT rely on any pre-selected supporting sentence.

Return STRICT JSON: {"supported": true|false, "why": "<one sentence>",
"quote": "<the verbatim source sentence that best decides it, or empty>"}

CLAIM:
{CLAIM}

SOURCE PASSAGES:
{SOURCES}
"""


def rdir(n):
    return os.path.join(ROOT, "data", "loop_rounds", f"round_{n}")


def atomic_save(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


# ---------------------------------------------------------------- prep
def stage_prep(args):
    proj = os.path.join(rdir(args.round), "project")
    if os.path.isdir(proj) and os.listdir(proj):
        # A pre-existing project dir silently absorbs copies (2026-07-10 night:
        # cp -r nested a second project inside one and the app stage ran the
        # wrong text). Refuse; the operator empties it deliberately.
        sys.exit(f"project dir already exists and is not empty: {proj}\n"
                 f"Remove it first if you mean to re-prep this round.")
    os.makedirs(os.path.join(proj, "sources"), exist_ok=True)
    text = open(args.text, encoding="utf-8").read()
    mapping = json.load(open(args.map)) if args.map else {}
    cites = mapping.get("citations") or {}
    src_map = mapping.get("sources") or {}
    src_from = args.sources_from or os.path.join(os.path.dirname(args.text), "sources")

    if cites:  # numbered [n] or [n,m] -> [[key]] markers
        # FIRST drop a trailing reference list (lines starting with [n]),
        # else its [n] prefixes get converted into markers below.
        text = re.sub(r"\n-{3,}\s*\n(\[[0-9]+\].*\n?)+\s*$", "\n", text)
        def repl(m):
            nums = [x.strip() for x in m.group(1).split(",")]
            keys = [cites.get(x) for x in nums]
            if any(k is None for k in keys):
                return m.group(0)  # not a citation we know — leave untouched
            return " " + " ".join(f"[[{k}]]" for k in keys)
        text = re.sub(r"\[([0-9]+(?:\s*,\s*[0-9]+)*)\]", repl, text)

    refs_lines = []
    for key, fname in src_map.items():
        src = os.path.join(src_from, fname)
        if not os.path.exists(src):
            print(f"MISSING source file: {src}")
            continue
        base, ext = os.path.splitext(fname)
        if ext.lower() in (".html", ".htm"):
            out = os.path.join(proj, "sources", f"{key}.txt")
            with open(out, "w", encoding="utf-8") as f:
                subprocess.run(["pandoc", "-f", "html", "-t", "plain", "--wrap=none", src],
                               stdout=f, check=True)
            refs_lines.append(f"{key} = {key}.txt")
        else:
            out = os.path.join(proj, "sources", f"{key}{ext.lower()}")
            shutil.copy(src, out)
            refs_lines.append(f"{key} = {key}{ext.lower()}")

    tpath = os.path.join(proj, "my_text.md")
    open(tpath, "w", encoding="utf-8").write(text)
    open(tpath + ".refs.txt", "w", encoding="utf-8").write("\n".join(refs_lines) + "\n")
    meta = {"round": args.round, "source_text": os.path.abspath(args.text),
            "n_sources": len(refs_lines)}
    atomic_save(meta, os.path.join(rdir(args.round), "meta.json"))
    print(f"prep done: {tpath}  ({len(refs_lines)} sources)")
    marks = re.findall(r"\[\[[A-Za-z0-9_-]+\]\]", text)
    print(f"citation markers in text: {len(marks)}")


# ---------------------------------------------------------------- app
def stage_app(args):
    proj = os.path.join(rdir(args.round), "project")
    out = os.path.join(rdir(args.round), "app")
    cmd = [os.path.join(ROOT, "venv/bin/python3"), os.path.join(ROOT, "verify_my_text.py"),
           "--text", os.path.join(proj, "my_text.md"),
           "--sources", os.path.join(proj, "sources"),
           "--output-dir", out, "--yes"]
    print("running:", " ".join(cmd))
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit(f"app run failed rc={r.returncode}")
    print(f"\nCLEAN VIEWER for the owner (review while columns run):\n  {out}/viewer.html")
    print("Toggle '✓ checked' on each card you review; Download review file when done.")


# ---------------------------------------------------------------- columns
def _judgeable(c):
    """Columns only judge claims that were actually checked against a source —
    a missing-source claim has nothing to grade (round 3: 3 book/paywalled
    citations); it still appears in the table as an info row."""
    return not str(c.get("reason") or "").startswith("source_file_missing")


def _load_run(args):
    out = os.path.join(rdir(args.round), "app")
    analysis = json.load(open(os.path.join(out, "analysis.json")))
    claims = [c for c in analysis["text_claims"]
              if c.get("verdict") in ("supported", "unsupported")]
    sources = dc._load_sources(out)
    return out, analysis, claims, sources


def shown_block(c):
    """EVERYTHING the viewer displays as evidence for this claim — the
    per-source evidence sentences AND (since the covering-set fix, 2026-07-10)
    the coverage block's component→sentence mapping + its amber uncovered
    line. The sufficiency/grader columns judge the DISPLAYED evidence, so they
    must see what the card actually shows."""
    lines, seen = [], set()
    for e in (c.get("evidences") or []):
        s = (e.get("sentence") or "").strip()
        if s and s not in seen:
            seen.add(s)
            tag = "judged supporting" if e.get("supported") else "judged NOT supporting"
            lines.append(f"- [{e.get('source_title', '?')}] ({tag}) \"{s}\"")
    cov = c.get("covering") or {}
    for ce in cov.get("covered", []):
        s = (ce.get("sentence") or "").strip()
        if s and s not in seen:
            seen.add(s)
            lines.append(f"- [{ce.get('source_title', '?')}] (shown as proof of: "
                         f"{ce.get('component', '?')}) \"{s}\"")
    # "Read it in context" spans (owner, 2026-07-11): the card also displays
    # the used sentences WITH the original text between them — the sufficiency
    # judge sees the same, so cross-sentence fits (t8's dates, t6's cohort)
    # are judged on the connected passage, not disjoint quotes.
    for sp in (cov.get("spans") or []):
        if sp.get("text"):
            lines.append(f"- Displayed reading view from [{sp.get('source_title', '?')}] "
                         f"(the used sentences with the original text between them): "
                         f"\"{sp['text']}\"")
    # The card's amber "no evidence shown for: X" line is deliberately NOT
    # passed: it is the tool's own conclusion — a sufficiency judge that reads
    # it would merely echo it, and column agreement would stop meaning anything.
    return "\n".join(lines) or "(none shown)"


def relevant_section(claim_text, sents):
    """Full source text if short; else the best contiguous ~20k-word section."""
    texts = [s.get("text", "") for s in sents]
    total = sum(len(t.split()) for t in texts)
    if total <= SECTION_FULL_WORDS:
        return " ".join(texts)
    chunks = matcher._chunk_sents(sents)
    if not chunks:
        return " ".join(texts)[:SECTION_CAP_WORDS * 6]
    lex = matcher._lex_scores(claim_text, texts)
    best = max(range(len(chunks)), key=lambda i: max(lex[j] for j in chunks[i][1]))
    # expand around the best chunk until the word cap
    lo = hi = best
    words = len(chunks[best][0].split())
    while words < SECTION_CAP_WORDS and (lo > 0 or hi < len(chunks) - 1):
        if lo > 0:
            lo -= 1; words += len(chunks[lo][0].split())
        if words < SECTION_CAP_WORDS and hi < len(chunks) - 1:
            hi += 1; words += len(chunks[hi][0].split())
    return " ".join(ch[0] for ch in chunks[lo:hi + 1])


def source_blocks(c, sources, claim_text, for_grader):
    parts, seen = [], set()
    for e in (c.get("evidences") or []):
        pid = e.get("paper_id")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        src = sources.get(pid) or {}
        sents = src.get("sentences", []) or []
        title = e.get("source_title") or src.get("title") or pid
        if not sents:
            parts.append(f'From "{title}": (source text unavailable)')
            continue
        if for_grader:
            body = relevant_section(claim_text, sents)
        else:  # opus truth column: top lexical chunk (as in the eggs/paper1 runs)
            body = dc._top_lex_chunk(claim_text, sents)
        parts.append(f'From "{title}":\n"{body}"')
    return "\n\n".join(parts) or "(no source text found)"


COLUMNS = {
    "sonnet_suff": dict(model="claude-code/sonnet"),
    "opus_truth": dict(model="claude-code/opus"),
    "grader": dict(model=None),  # from --grader-model (opus primary since round 2)
}

# Round-2 protocol (owner, 2026-07-10): local trained checkers join as
# sufficiency columns — SAME task as sonnet_suff (judge the SHOWN sentences
# only), run as a separate PROCESS track in parallel with the Claude columns
# (local CPU vs remote APIs — zero contention). $0.
LOCAL_COLUMNS = ("minicheck_suff", "qwen_suff")
QWEN_OLLAMA = "hf.co/sebsigma/SemanticCite-Checker-Qwen3-4B:Q4_K_M"
MINICHECK_HF = "lytang/MiniCheck-Flan-T5-Large"

QWEN_SUFF_PROMPT = """You are a strict citation checker. Below is a CLAIM from an author's
document and the supporting sentences a tool displays from the cited source(s).
Judge ONLY the displayed sentences: do they, by themselves, fully support every
part of the claim? Being on the same topic is not enough.

Answer with STRICT JSON only: {"sufficient": true|false, "why": "<one sentence>"}

CLAIM:
{CLAIM}

DISPLAYED SENTENCES:
{EVIDENCE}
"""


def _minicheck_scorer():
    """MiniCheck-Flan-T5-Large via transformers direct (the pip package is
    blocked; format from its inference.py): input 'predict: '+doc+' </s> '+claim,
    support probability = softmax over the logits of token ids [3, 209][1]."""
    import torch
    from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
    tok = AutoTokenizer.from_pretrained(MINICHECK_HF)
    model = AutoModelForSeq2SeqLM.from_pretrained(MINICHECK_HF)
    model.eval()

    def score(doc, claim):
        inputs = tok("predict: " + doc + " </s> " + claim, return_tensors="pt",
                     truncation=True, max_length=2048)
        with torch.no_grad():
            out = model(**inputs, decoder_input_ids=torch.zeros((1, 1), dtype=torch.long))
        logits = out.logits[0, 0, [3, 209]]
        return float(torch.softmax(logits, dim=-1)[1].item())

    return score


def _qwen_call(prompt, timeout=420):
    r = subprocess.run(["ollama", "run", QWEN_OLLAMA], input=prompt,
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "ollama failed")[:200])
    return r.stdout


def run_local_column(name, args):
    """Local ($0, CPU) sufficiency columns — checkpointed like the API ones."""
    out, analysis, claims, sources = _load_run(args)
    path = os.path.join(rdir(args.round), "columns", f"{name}.json")
    results = json.load(open(path)) if os.path.exists(path) else {}
    scorer = _minicheck_scorer() if name == "minicheck_suff" else None
    for c in claims:
        cid = c["id"]
        if not _judgeable(c):
            continue
        if cid in results and isinstance(results[cid], dict) and "error" not in results[cid]:
            continue
        doc = "\n".join((e.get("sentence") or "") for e in (c.get("evidences") or [])
                        if e.get("sentence")) or "(none shown)"
        try:
            if name == "minicheck_suff":
                p = scorer(doc, c["text"])
                results[cid] = {"sufficient": p >= 0.5, "prob": round(p, 3)}
            else:
                raw = _qwen_call(QWEN_SUFF_PROMPT.replace("{CLAIM}", c["text"])
                                 .replace("{EVIDENCE}", shown_block(c)))
                j = extract_json(raw)
                results[cid] = (j if isinstance(j, dict) and "sufficient" in j
                                else {"error": "unparseable", "raw": (raw or "")[:300]})
        except Exception as e:
            results[cid] = {"error": str(e)[:200]}
        atomic_save(results, path)
        r = results[cid]
        print(f"{name} {cid}: {r.get('sufficient', r.get('error'))}", flush=True)
    print(f"{name}: done ({len(results)}/{len(claims)})")


def run_fable_positive_check(args):
    """Round-2 grader economy (owner, 2026-07-10): Opus grades every row;
    Fable re-grades ONLY the rows Opus passes as 'supported' — validates
    Opus's POSITIVE rate while Fable access lasts (negatives already matched
    16/16). Writes columns/grader_fable_pos.json."""
    out, analysis, claims, sources = _load_run(args)
    cdir = os.path.join(rdir(args.round), "columns")
    grader = json.load(open(os.path.join(cdir, "grader.json")))
    positives = [c for c in claims if _judgeable(c)
                 and (grader.get(c["id"]) or {}).get("action") == "supported"]
    path = os.path.join(cdir, "grader_fable_pos.json")
    results = json.load(open(path)) if os.path.exists(path) else {}
    if not positives:
        atomic_save(results, path)
        print("grader_fable_pos: no Opus-passed rows to re-check (0 positives)")
        return
    llm = LLMClient(model="claude-code/fable")
    grader_tpl = open(args.grader_prompt).read()
    for c in positives:
        cid = c["id"]
        if cid in results and isinstance(results[cid], dict) and "error" not in results[cid]:
            continue
        try:
            prompt = (grader_tpl.replace("{CLAIM}", c["text"])
                      .replace("{SHOWN}", shown_block(c))
                      .replace("{CONTEXT}", source_blocks(c, sources, c["text"], True)))
            raw = llm.call(prompt, temperature=0.0, max_output_tokens=3000)
            results[cid] = extract_json(raw) or {"error": "unparseable", "raw": (raw or "")[:300]}
        except Exception as e:
            results[cid] = {"error": str(e)[:200]}
        atomic_save(results, path)
        print(f"grader_fable_pos {cid}: {results[cid].get('action', results[cid].get('error'))}",
              flush=True)
    agree = sum(1 for c in positives
                if (results.get(c["id"]) or {}).get("action") == "supported")
    print(f"grader_fable_pos: done — Fable agrees on {agree}/{len(positives)} Opus positives")


def run_column(name, args):
    out, analysis, claims, sources = _load_run(args)
    cdir = os.path.join(rdir(args.round), "columns")
    path = os.path.join(cdir, f"{name}.json")
    results = json.load(open(path)) if os.path.exists(path) else {}
    model = args.grader_model if name == "grader" else COLUMNS[name]["model"]
    llm = LLMClient(model=model)
    suff_tpl = open(SUFF_PROMPT).read()
    grader_tpl = open(args.grader_prompt).read()
    for c in claims:
        cid = c["id"]
        if not _judgeable(c):
            continue
        if cid in results and isinstance(results[cid], dict) and "error" not in results[cid]:
            continue
        try:
            if name == "sonnet_suff":
                prompt = (suff_tpl.replace("{CLAIM}", c["text"])
                          .replace("{EVIDENCE}", shown_block(c)))
            elif name == "opus_truth":
                prompt = (OPUS_TRUTH_PROMPT.replace("{CLAIM}", c["text"])
                          .replace("{SOURCES}", source_blocks(c, sources, c["text"], False)))
            else:
                prompt = (grader_tpl.replace("{CLAIM}", c["text"])
                          .replace("{SHOWN}", shown_block(c))
                          .replace("{CONTEXT}", source_blocks(c, sources, c["text"], True)))
            raw = llm.call(prompt, temperature=0.0, max_output_tokens=3000)
            results[cid] = extract_json(raw) or {"error": "unparseable", "raw": (raw or "")[:300]}
        except Exception as e:
            results[cid] = {"error": str(e)[:200]}
        atomic_save(results, path)
        r = results[cid]
        val = next((r[k] for k in ("action", "sufficient", "supported", "error") if k in r), "?")
        print(f"{name} {cid}: {val}", flush=True)
    print(f"{name}: done ({len(results)}/{len(claims)})")


# ---------------------------------------------------------------- table
def _owner_column(app_dir):
    """Newest review*.json exported from the viewer (checked ids + marks)."""
    cands = sorted((f for f in os.listdir(app_dir) if re.match(r"review.*\.json$", f)),
                   key=lambda f: os.path.getmtime(os.path.join(app_dir, f)))
    if not cands:
        return {}, set()
    d = json.load(open(os.path.join(app_dir, cands[-1])))
    marks = {m["id"]: m for m in d.get("marks", [])}
    checked = set(d.get("checked", []))
    return marks, checked


def stage_table(args):
    out, analysis, claims, _ = _load_run(args)
    cdir = os.path.join(rdir(args.round), "columns")
    cols = {}
    for name in list(COLUMNS) + list(LOCAL_COLUMNS) + ["grader_fable_pos"]:
        p = os.path.join(cdir, f"{name}.json")
        cols[name] = json.load(open(p)) if os.path.exists(p) else {}
    marks, checked = _owner_column(out)

    def cell_suff(j):
        if not isinstance(j, dict) or "error" in j: return "—"
        cell = "✅" if j.get("sufficient") else "❌"
        if "prob" in j:
            cell += f" {j['prob']}"
        return cell

    def cell_truth(j):
        if not isinstance(j, dict) or "error" in j: return "—"
        return "✅" if j.get("supported") else "❌"

    def cell_grade(j):
        if not isinstance(j, dict) or "error" in j: return "—"
        a = j.get("action", "?")
        return {"supported": "✅ supported", "add_citation_or_rewrite": "✍️ author-fix",
                "wrong_or_insufficient_evidence": "🔍 tool-fetch"}.get(a, a)

    def cell_owner(cid):
        if cid in marks:
            m = marks[cid]
            lab = ",".join(m.get("marks", [])) or "note"
            note = (m.get("note") or "").strip()
            return f"⚠ {lab}" + (f" — {note[:110]}" if note else "")
        return "✓ ok" if cid in checked else "not checked"

    have_local = any(cols[n] for n in LOCAL_COLUMNS)
    local_hdr = " minicheck | qwen |" if have_local else ""
    rows = [f"| # | claim | app (gemini) |{local_hdr} sonnet: sentences suffice? | "
            f"opus: true per source? | grader: expected outcome | owner |",
            "|---|---|---|" + ("---|---|" if have_local else "") + "---|---|---|---|"]
    agree = 0
    for c in claims:
        cid = c["id"]
        if not _judgeable(c):
            local_cells = " — | — |" if have_local else ""
            rows.append(f"| {cid} | {c['text'][:90].replace('|', ' ')}… | 🚫 source file "
                        f"missing |{local_cells} — | — | — | {cell_owner(cid)} |")
            continue
        g = "✅" if c["verdict"] == "supported" else "❌"
        s = cell_suff(cols["sonnet_suff"].get(cid))
        o = cell_truth(cols["opus_truth"].get(cid))
        f_ = cell_grade(cols["grader"].get(cid))
        if g == s == o and f_.startswith("✅"):
            agree += 1
        gr = cols["grader"].get(cid) or {}
        bits = []
        if gr.get("missing_subclaim"):
            bits.append("missing: " + gr["missing_subclaim"][:90])
        proofs = gr.get("proof_sentences") or []
        if proofs:
            bits.append("proof: " + " / ".join(f'"{p[:70]}…"' for p in proofs[:2]))
        fp = cols["grader_fable_pos"].get(cid)
        if fp is not None and isinstance(fp, dict):
            fa = fp.get("action", fp.get("error", "?"))
            bits.append("fable-recheck: " + ("agrees ✅" if fa == "supported" else f"DISAGREES → {fa}"))
        f_cell = f_ + (" — " + "; ".join(bits) if bits else "")
        local_cells = ""
        if have_local:
            local_cells = (f" {cell_suff(cols['minicheck_suff'].get(cid))} |"
                           f" {cell_suff(cols['qwen_suff'].get(cid))} |")
        rows.append(f"| {cid} | {c['text'][:90].replace('|', ' ')}… | {g} |{local_cells}"
                    f" {s} | {o} | {f_cell} | {cell_owner(cid)} |")

    # Own-claim rows (round-1 report: the owner marked own claims too — t2/t13
    # "needs citation" — so the table must carry them for the owner column).
    own = [c for c in analysis["text_claims"] if c.get("verdict") == "own"]
    own_rows = []
    for c in own:
        kind = (c.get("own_kind") or {}).get("kind") or "untagged"
        chip = "⚠ citation needed?" if kind == "fact" else kind
        own_rows.append(f"| {c['id']} | {c['text'][:90].replace('|', ' ')}… | own ({chip}) | "
                        + ("— | — |" if have_local else "")
                        + f" — | — | — | {cell_owner(c['id'])} |")

    grades = [(cols['grader'].get(c['id']) or {}).get('action') for c in claims]
    from collections import Counter
    dist = Counter(g for g in grades if g)
    n_pos = sum(1 for g in grades if g == "supported")
    fp_agree = sum(1 for c in claims
                   if (cols["grader_fable_pos"].get(c["id"]) or {}).get("action") == "supported")
    fable_line = (f" · Fable re-check of Opus positives: {fp_agree}/{n_pos} agree"
                  if n_pos and cols["grader_fable_pos"] else "")
    head = [f"# Round {args.round} — table (OWNER GATE: analysis withheld until you have read this)",
            "",
            f"Claims judged: {len(claims)} · full-agreement rows: {agree} · "
            f"grader outcomes: {dict(dist)}{fable_line} · owner checked: {len(checked)} · "
            f"owner marks: {len(marks)}",
            ""]
    tail = (["", "## Own (uncited) claims", ""] + rows[:2] + own_rows) if own_rows else []
    table = "\n".join(head + rows + tail) + "\n"
    tpath = os.path.join(rdir(args.round), "table.md")
    open(tpath, "w", encoding="utf-8").write(table)
    print(table[:1500])
    print(f"\nfull table: {tpath}")
    print("\n== OWNER GATE == Read the table; analysis starts only on your go.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("stage", choices=["prep", "app", "columns", "columns-local",
                                      "fable-pos", "table"])
    ap.add_argument("--text")
    ap.add_argument("--map")
    ap.add_argument("--sources-from")
    ap.add_argument("--only", choices=list(COLUMNS) + list(LOCAL_COLUMNS))
    ap.add_argument("--grader-model", default="claude-code/opus")
    ap.add_argument("--grader-prompt", default=GRADER_PROMPT,
                    help="grader prompt file (default v2; pass the v1 path to reproduce rounds 1-3)")
    args = ap.parse_args()
    if args.stage == "prep":
        if not args.text:
            sys.exit("prep needs --text")
        stage_prep(args)
    elif args.stage == "app":
        stage_app(args)
    elif args.stage == "columns":
        for name in ([args.only] if args.only else list(COLUMNS)):
            run_column(name, args)
    elif args.stage == "columns-local":
        # separate PROCESS track — launch alongside `columns` (owner request)
        for name in ([args.only] if args.only in LOCAL_COLUMNS else list(LOCAL_COLUMNS)):
            run_local_column(name, args)
    elif args.stage == "fable-pos":
        run_fable_positive_check(args)
    else:
        stage_table(args)


if __name__ == "__main__":
    main()
