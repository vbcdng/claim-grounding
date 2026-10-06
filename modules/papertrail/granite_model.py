"""Granite Guardian 3.3 8B as a free local second opinion (task #65).

WHAT THIS IS. Granite Guardian is IBM's freely usable checker model (Apache
licence). It answers exactly one question: does this document support this
sentence? It runs on this computer's processor, so it costs no money — only
time (roughly two and a half to four minutes per claim, measured in task #54).
Task #54 measured eight such checkers on a 286-row corpus and found Granite the
only one whose approvals do NOT get more gullible when it is shown more source
text. On the claims our own judge approved, Granite flags 25 of the 28 our tool
got wrong and 33 of the 94 it got right — about 1.3 spurious flags per real
catch. So it is useful as a caution mark on a green card, never as a verdict.

WHY THIS FILE IS STDLIB-ONLY. `llama-cpp-python` (the package that runs a GGUF
weight file) is installed in the benchmark's own virtual environment, not in the
tool's. So this file must be runnable as a plain script under THAT interpreter:
`python3 modules/papertrail/granite_model.py --serve` starts a worker that loads
the model once and answers one JSON request per line on standard input.
granite_check.py drives it either in-process (when llama_cpp is importable) or
through that worker. Nothing here may import anything outside the standard
library or llama_cpp.

BYTE-IDENTICAL BY DESIGN. The prompt strings, the score extraction and the
source-slice geometry below are verbatim copies of
benchmarks/task54_grounding_checkers/run_checkers.py (Granite adapter) and
benchmarks/task54_grounding_checkers/build_corpus.py (`best_offset`,
`window_around`). They must stay copies: the measured 89% catch / 35%
false-alarm rates were produced by exactly this prompt on exactly this slice
geometry, and any drift silently invalidates them.
tests/test_granite_check.py asserts the copies still match the originals.
"""

import json
import math
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

MODEL_NAME = "granite-guardian-3.3-8b-Q4_K_M"
GGUF_FILENAME = "granite-guardian-3.3-8b-Q4_K_M.gguf"

# Where the weights already are on this machine (task #54 downloaded them).
# Overridable with PAPERTRAIL_GRANITE_GGUF for a machine that keeps them
# elsewhere; a `models/` copy is tried too so the benchmark folder is not a
# hard requirement.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_GGUF_PATHS = (
    os.path.join(_REPO_ROOT, "data", "task54_corpus", "hf_cache", "gguf", GGUF_FILENAME),
    os.path.join(_REPO_ROOT, "models", GGUF_FILENAME),
)

# The interpreter that HAS llama_cpp, used when this process does not.
DEFAULT_WORKER_PYTHON = os.path.join(
    _REPO_ROOT, "data", "task54_corpus", "venv_checkers", "bin", "python3")

SLICE_CHARS = 6000          # the benchmark's "large" pass; the thin pass was useless
MAX_INPUT_TOKENS = 6000     # same practical cap the benchmark ran under
N_CTX = 8192
THRESHOLD = 0.5             # the benchmark's classification cutoff

# --- prompt: verbatim from run_checkers.py -------------------------------------

GRANITE_SYSTEM_MESSAGE = (
    "As a judge agent, your role is to help assess whether the provided text "
    "meets the given judging criteria, utilizing all available information, "
    "including conversations, documents, and tools."
)

GRANITE_GROUNDEDNESS_CRITERIA = (
    "A text is considered ungrounded or unfaithful if it includes information "
    "lacking support from, or directly contradicting, the provided document(s). "
    "This risk arises when the text fabricates details, misinterprets the "
    "content, or makes unsupported extrapolations beyond what is explicitly "
    "stated in the document(s)."
)

GRANITE_SCORING_SCHEMA_ASSISTANT = (
    "If the last assistant's text meets the criteria, return 'yes'; otherwise, return 'no'."
)


def build_prompt(claim: str, evidence: str) -> str:
    judge_content = (
        "### Criteria: " + GRANITE_GROUNDEDNESS_CRITERIA
        + "\n\n### Scoring Schema: " + GRANITE_SCORING_SCHEMA_ASSISTANT
    )
    return (
        "<|start_of_role|>system<|end_of_role|>" + GRANITE_SYSTEM_MESSAGE + "<|end_of_text|>\n"
        + "<|start_of_role|>document {\"document_id\" :\"0\"}<|end_of_role|>\n"
        + evidence + "<|end_of_text|>\n"
        + "<|start_of_role|>assistant<|end_of_role|>" + claim + "<|end_of_text|>\n"
        + "<|start_of_role|>judge_protocol<|end_of_role|>" + judge_content + "<|end_of_text|>\n"
        + "<|start_of_role|>judge{no_think}<|end_of_role|>"
    )


def parse_output(text: str, logprobs) -> Tuple[float, bool]:
    """Returns (probability the source supports the claim, is_approx_fallback).

    Granite answers "yes" when it FOUND a groundedness problem, so the number is
    inverted at the end. The probability comes from comparing the model's own
    confidence in "yes" against "no" at the position where it wrote one of them.
    """
    m = re.search(r"<score>\s*(yes|no)\s*</score>", text, re.IGNORECASE)
    if not m:
        raise ValueError(
            f"could not find a <score>yes|no</score> tag in granite guardian output: {text!r}")
    label = m.group(1).lower()  # 'yes' = groundedness RISK found (ungrounded)

    p_yes = None
    is_approx = False
    if logprobs and logprobs.get("tokens") and logprobs.get("top_logprobs"):
        tokens = logprobs["tokens"]
        top_logprobs_list = logprobs["top_logprobs"]
        token_logprobs = logprobs.get("token_logprobs") or []
        for i, tok in enumerate(tokens):
            bare = tok.strip().strip('"').strip("'").lower()
            if bare not in ("yes", "no"):
                continue

            def _best_logprob_for(word, alts):
                best = None
                for k, v in (alts or {}).items():
                    if v is None:
                        continue
                    if k.strip().strip('"').strip("'").lower() == word:
                        best = v if best is None else max(best, v)
                return best

            alts = top_logprobs_list[i] if i < len(top_logprobs_list) else {}
            lp_yes = _best_logprob_for("yes", alts)
            lp_no = _best_logprob_for("no", alts)
            chosen_lp = token_logprobs[i] if i < len(token_logprobs) else None

            if lp_yes is not None and lp_no is not None:
                a, b = math.exp(lp_yes), math.exp(lp_no)
                p_yes = a / (a + b) if (a + b) > 0 else (1.0 if bare == "yes" else 0.0)
            elif chosen_lp is not None:
                p_chosen = math.exp(chosen_lp)
                p_yes = p_chosen if bare == "yes" else (1.0 - p_chosen)
                is_approx = True
            break

    if p_yes is None:
        p_yes = 1.0 if label == "yes" else 0.0
        is_approx = True
    return 1.0 - p_yes, is_approx


# --- the source slice: verbatim geometry from build_corpus.py -----------------

_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def _words_with_offsets(text: str):
    return [(m.group(0).lower(), m.start()) for m in _WORD_RE.finditer(text)]


def best_offset(source_text: str, needle: str, win_words: int = 45, step: int = 6):
    """The character position in source_text whose local ~45-word window shares
    the most words with needle. Used only to CENTER a context window; it is not
    a verification algorithm. Returns (offset, overlap_score)."""
    if not source_text or not needle:
        return 0, 0.0
    needle_words = {w for w, _ in _words_with_offsets(needle) if len(w) > 2}
    if not needle_words:
        return 0, 0.0
    src_words = _words_with_offsets(source_text)
    n = len(src_words)
    if n == 0:
        return 0, 0.0
    best_score = -1.0
    best_i = 0
    for i in range(0, n, step):
        window = src_words[i: i + win_words]
        wset = {w for w, _ in window}
        overlap = len(needle_words & wset)
        if overlap > best_score:
            best_score = overlap
            best_i = i
    return src_words[best_i][1], best_score


def window_around(source_text: str, offset: int, size: int = SLICE_CHARS) -> str:
    if not source_text:
        return ""
    if len(source_text) <= size:
        return source_text
    half = size // 2
    start = max(0, offset - half)
    end = min(len(source_text), start + size)
    start = max(0, end - size)
    return source_text[start:end]


def slice_for(source_text: str, needle: str, size: int = SLICE_CHARS) -> str:
    """The ~6,000-character excerpt of the source, centred on `needle`.

    `needle` is the evidence sentence the tool judged on when there is one, and
    the claim itself otherwise — exactly the choice the benchmark made.
    """
    offset, _ = best_offset(source_text, needle)
    return window_around(source_text, offset, size)


# --- the model ----------------------------------------------------------------

def resolve_gguf(path: Optional[str] = None) -> Optional[str]:
    """Where the weight file is, or None when it is not on this machine."""
    for cand in ([path] if path else []) + [os.environ.get("PAPERTRAIL_GRANITE_GGUF")] \
            + list(DEFAULT_GGUF_PATHS):
        if cand and os.path.exists(cand):
            return cand
    return None


def have_llama_cpp() -> bool:
    try:
        import llama_cpp  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


class GraniteModel:
    """The weights, loaded once, answering one claim at a time."""

    def __init__(self, gguf_path: Optional[str] = None, n_threads: Optional[int] = None):
        self.gguf_path = resolve_gguf(gguf_path)
        self.n_threads = n_threads or os.cpu_count() or 4
        self._llm = None

    def load(self):
        if self._llm is not None:
            return
        if not self.gguf_path:
            raise FileNotFoundError(
                "Granite Guardian's weight file was not found. Looked at "
                + ", ".join(DEFAULT_GGUF_PATHS)
                + " and at PAPERTRAIL_GRANITE_GGUF. It is a 4.9 GB file, freely "
                  "downloadable from ibm-granite/granite-guardian-3.3-8b-GGUF "
                  "(Apache licence).")
        from llama_cpp import Llama
        self._llm = Llama(
            model_path=self.gguf_path,
            n_ctx=N_CTX,
            n_threads=self.n_threads,
            verbose=False,
            logits_all=True,  # needed for the per-token probabilities we read
        )

    def score(self, claim: str, evidence: str) -> Dict[str, Any]:
        """One answer: how strongly this evidence supports this claim, 0 to 1."""
        self.load()
        t0 = time.time()
        prompt = build_prompt(claim, evidence)
        tokens = self._llm.tokenize(prompt.encode("utf-8"))
        truncated = len(tokens) > MAX_INPUT_TOKENS
        if truncated:
            budget_words = max(20, len(evidence.split())
                               - (len(tokens) - MAX_INPUT_TOKENS) * 2)
            evidence = " ".join(evidence.split()[:budget_words])
            prompt = build_prompt(claim, evidence)
        out = self._llm(
            prompt,
            max_tokens=24,
            temperature=0.0,
            logprobs=20,
            stop=["<|end_of_text|>"],
        )
        choice = out["choices"][0]
        score, is_approx = parse_output(choice["text"], choice.get("logprobs"))
        return {"score": score, "truncated": truncated,
                "score_is_approx": is_approx, "seconds": round(time.time() - t0, 3)}


# --- worker mode --------------------------------------------------------------

def serve(argv: List[str]) -> int:
    """Answer one JSON request per line on standard input.

    Request:  {"claim": "...", "evidence": "..."}   (or {"ping": true})
    Reply:    {"score": 0.87, "seconds": 141.2, ...} or {"error": "..."}
    The model is loaded on the first real request, so a ping is cheap.
    """
    gguf = None
    threads = None
    for i, a in enumerate(argv):
        if a == "--gguf" and i + 1 < len(argv):
            gguf = argv[i + 1]
        if a == "--threads" and i + 1 < len(argv):
            threads = int(argv[i + 1])
    model = GraniteModel(gguf_path=gguf, n_threads=threads)
    sys.stdout.write(json.dumps({
        "ready": True, "model": MODEL_NAME, "gguf": model.gguf_path,
        "in_process": True}) + "\n")
    sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError as e:
            reply = {"error": f"unreadable request: {e}"}
        else:
            if req.get("stop"):
                break
            if req.get("ping"):
                reply = {"pong": True, "gguf": model.gguf_path}
            else:
                try:
                    reply = model.score(req.get("claim") or "", req.get("evidence") or "")
                except Exception as e:  # noqa: BLE001
                    reply = {"error": f"{type(e).__name__}: {e}"}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    if "--serve" in sys.argv:
        raise SystemExit(serve(sys.argv[1:]))
    print(__doc__)
    print("Run with --serve to answer JSON requests on standard input.")
    print("weights:", resolve_gguf() or "NOT FOUND")
    raise SystemExit(0)
