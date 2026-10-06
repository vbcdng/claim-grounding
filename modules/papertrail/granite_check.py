"""Free local second opinion on the claims the judge APPROVED (task #65).

WHAT IT DOES, IN ONE EXAMPLE. The tool judged claim t6 supported, quoting one
sentence from Smith 2021. This pass hands Granite Guardian — a freely usable
checker model that runs on this computer's processor at no money cost — the
claim plus a six-thousand-character excerpt of Smith 2021 centred on that
sentence, and asks whether the excerpt supports the claim. If Granite says no,
card t6 gets a caution mark reading as a question. The verdict never changes.

WHY ONLY APPROVED CLAIMS. Task #54 measured this model on 286 rows. Across all
rows it wrongly rejects 45% of correct citations, which is far too noisy to be a
judge. But on the subset our own judge had approved it flags 25 of the 28 the
tool got wrong (89%) and 33 of the 94 it got right (35%) — about 1.3 spurious
flags for every real catch. That is a good trade for a caution mark on a card
someone is already reading, and it only holds on this subset, so this pass never
looks at anything else.

TWO LIMITS THAT BELONG IN THE FEATURE'S OWN WORDING. Granite compares claim text
against evidence text only, so a claim attached to the WRONG paper is invisible
to it — that whole category stays the tool's job. And its disagreement is a
question, not a finding: roughly one flag in every two point three concerns a
claim that was perfectly fine.

WHERE THE EXCERPT COMES FROM (the first design question, not an afterthought).
The measured numbers came from a six-thousand-character window of raw source
text, centred by a plain word-overlap match on the evidence sentence. Handed
only the few sentences the viewer shows, the same model rejects 79% of correct
citations and is useless. So this pass rebuilds the same geometry from the run's
own sentence index (`<run>/source_claims/*.json`, which every run writes): the
sentences are joined back into document text in order, and
granite_model.slice_for cuts the same window with the same code. A claim citing
several sources is scored against each cited source's window in turn (up to
MAX_SLICES_PER_CLAIM) and keeps the BEST score, because the tool's own rule is
that any one cited source may carry the claim; a single-source claim, the case
the 89%/35% numbers were measured on, is one window and one call.

COST. No money at all — the weights sit on this computer. Time is the cost:
about two and a half to four minutes per window on this processor, so a run with
twenty approved claims takes roughly an hour. That is why the flag is off by
default.

Contract, same as deep-check: writes <run>/granite_check.json, stamped so a
re-run can never show an old answer beside a fresh verdict (task #57), and never
touches analysis.json.
"""

import json
import logging
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from modules.papertrail import deep_check_store, granite_model

logger = logging.getLogger(__name__)

FORMAT = "granite_check/1"
FILENAME = "granite_check.json"
PREV_FILENAME = "granite_check_prev.json"

# A claim citing more sources than this is scored against the first few only:
# each extra window is another two-to-four minutes for a claim one window has
# usually already settled.
MAX_SLICES_PER_CLAIM = 3


# --- reading the finished run -------------------------------------------------

def load_sources(run_dir: str) -> Dict[str, Dict]:
    """paper_id -> the run's own sentence index for that source."""
    out: Dict[str, Dict] = {}
    sc_dir = os.path.join(run_dir, "source_claims")
    if not os.path.isdir(sc_dir):
        return out
    for fn in sorted(os.listdir(sc_dir)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(sc_dir, fn), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("paper_id"):
            out[d["paper_id"]] = d
    return out


def source_text(source: Dict[str, Any]) -> str:
    """The document text as the tool sees it: its sentences joined in order.

    This is the same material the judge read, so a window cut from it cannot
    show Granite something the judge never had.
    """
    sents = source.get("sentences") or []
    return " ".join((s.get("text") or "").strip() for s in sents if s.get("text"))


def approved_claims(analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The claims this pass is allowed to look at: verdict supported, with
    at least one quoted evidence sentence to centre a window on."""
    return [c for c in (analysis.get("text_claims") or [])
            if c.get("verdict") == "supported" and (c.get("evidences") or [])]


def slices_for_claim(claim: Dict[str, Any], sources: Dict[str, Dict],
                     size: int = granite_model.SLICE_CHARS,
                     max_slices: int = MAX_SLICES_PER_CLAIM) -> List[Dict[str, Any]]:
    """One excerpt per cited source that contributed evidence, best first.

    Each entry says which source it came from and whether the window was
    centred on the tool's own evidence sentence or (when that sentence cannot be
    found in the index) on the claim text — the same fallback the benchmark used.
    """
    out: List[Dict[str, Any]] = []
    seen = set()
    for ev in (claim.get("evidences") or []):
        pid = ev.get("paper_id")
        if not pid or pid in seen:
            continue
        src = sources.get(pid)
        if not src:
            continue
        text = source_text(src)
        if not text:
            continue
        seen.add(pid)
        sentence = (ev.get("sentence") or "").strip()
        needle = sentence or (claim.get("text") or "")
        centred_on = "evidence sentence" if sentence else "claim text"
        if sentence and sentence not in text:
            # The stored evidence may have been reflowed; word overlap still
            # centres the window, so say so rather than pretending otherwise.
            centred_on = "evidence sentence (matched by word overlap)"
        out.append({
            "paper_id": pid,
            "key": src.get("key") or ev.get("key"),
            "title": src.get("title"),
            "centred_on": centred_on,
            "text": granite_model.slice_for(text, needle, size=size),
            "source_chars": len(text),
        })
        if len(out) >= max_slices:
            break
    return out


# --- talking to the model -----------------------------------------------------

class Runner:
    """Scores claims, in this process when it can, in a helper process when not.

    `llama-cpp-python` (the package that runs the weight file) is installed in
    the checker benchmark's own virtual environment rather than the tool's, so
    the helper process is the normal path: it loads the five-gigabyte weight
    file once and answers one claim per line.
    """

    def __init__(self, gguf_path: Optional[str] = None,
                 worker_python: Optional[str] = None,
                 n_threads: Optional[int] = None):
        self.gguf_path = granite_model.resolve_gguf(gguf_path)
        self.n_threads = n_threads
        self.worker_python = (worker_python
                              or os.environ.get("PAPERTRAIL_GRANITE_PYTHON")
                              or granite_model.DEFAULT_WORKER_PYTHON)
        self.in_process = granite_model.have_llama_cpp()
        self._model = None
        self._proc: Optional[subprocess.Popen] = None

    # -- lifecycle
    def start(self):
        if not self.gguf_path:
            raise FileNotFoundError(
                "Granite Guardian's weight file is not on this computer. Looked at "
                + ", ".join(granite_model.DEFAULT_GGUF_PATHS)
                + " and at PAPERTRAIL_GRANITE_GGUF.")
        if self.in_process:
            self._model = granite_model.GraniteModel(self.gguf_path, self.n_threads)
            self._model.load()
            return
        if not os.path.exists(self.worker_python):
            raise FileNotFoundError(
                "This pass needs the llama-cpp-python package, which is not "
                "installed for this interpreter. It is installed at "
                f"{self.worker_python}, but that file does not exist. Point "
                "PAPERTRAIL_GRANITE_PYTHON at an interpreter that has it.")
        cmd = [self.worker_python, os.path.abspath(granite_model.__file__),
               "--serve", "--gguf", self.gguf_path]
        if self.n_threads:
            cmd += ["--threads", str(self.n_threads)]
        self._proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=None, text=True, bufsize=1, cwd=os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(granite_model.__file__)))))
        hello = self._read_json()
        if not hello or not hello.get("ready"):
            raise RuntimeError(f"the Granite helper process did not start: {hello!r}")

    def stop(self):
        if self._proc is not None:
            try:
                self._proc.stdin.write(json.dumps({"stop": True}) + "\n")
                self._proc.stdin.flush()
                self._proc.wait(timeout=20)
            except Exception:  # noqa: BLE001
                self._proc.kill()
            self._proc = None
        self._model = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False

    # -- one answer
    def _read_json(self) -> Optional[Dict[str, Any]]:
        """The next JSON line from the helper, skipping anything else it prints."""
        while True:
            line = self._proc.stdout.readline()
            if not line:
                return None
            line = line.strip()
            if not line.startswith("{"):
                if line:
                    logger.debug(f"granite helper said: {line[:200]}")
                continue
            try:
                return json.loads(line)
            except ValueError:
                continue

    def score(self, claim_text: str, evidence: str) -> Dict[str, Any]:
        if self._model is not None:
            return self._model.score(claim_text, evidence)
        if self._proc is None:
            raise RuntimeError("the Granite helper process is not running")
        self._proc.stdin.write(json.dumps(
            {"claim": claim_text, "evidence": evidence}) + "\n")
        self._proc.stdin.flush()
        reply = self._read_json()
        if reply is None:
            raise RuntimeError("the Granite helper process stopped answering")
        return reply


# --- the pass ------------------------------------------------------------------

def check_claim(claim: Dict[str, Any], slices: List[Dict[str, Any]], runner: Runner,
                threshold: float = granite_model.THRESHOLD) -> Dict[str, Any]:
    """Granite's answer about one approved claim.

    The best score across the cited sources decides, because the tool's own rule
    is that any one cited source may carry the claim.
    """
    if not slices:
        return {"error": "no source excerpt could be built for this claim"}
    per_source = []
    for sl in slices:
        try:
            r = runner.score(claim.get("text") or "", sl["text"])
        except Exception as e:  # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}"}
        if "error" in r:
            return {"error": str(r["error"])}
        per_source.append({
            "paper_id": sl["paper_id"], "key": sl.get("key"),
            "score": round(float(r.get("score", 0.0)), 6),
            "slice_chars": len(sl["text"]),
            "centred_on": sl["centred_on"],
            "truncated": bool(r.get("truncated")),
            "score_is_approx": bool(r.get("score_is_approx")),
            "seconds": r.get("seconds"),
        })
    best = max(per_source, key=lambda p: p["score"])
    return {
        "model": granite_model.MODEL_NAME,
        "score": best["score"],
        "threshold": threshold,
        "agrees": best["score"] >= threshold,
        "slice_chars": best["slice_chars"],
        "centred_on": best["centred_on"],
        "from_source": best.get("key") or best["paper_id"],
        "truncated": best["truncated"],
        "score_is_approx": best["score_is_approx"],
        "seconds": round(sum(p.get("seconds") or 0.0 for p in per_source), 3),
        "per_source": per_source,
    }


def check_run(run_dir: str, analysis: Dict[str, Any], limit: int = 0,
              threshold: float = granite_model.THRESHOLD,
              gguf_path: Optional[str] = None,
              worker_python: Optional[str] = None,
              n_threads: Optional[int] = None,
              slice_chars: int = granite_model.SLICE_CHARS,
              progress: bool = True) -> Dict[str, Dict[str, Any]]:
    """Score every approved claim of a finished run. Returns id -> answer."""
    sources = load_sources(run_dir)
    claims = approved_claims(analysis)
    if limit:
        claims = claims[:limit]
    if not claims:
        logger.info("No approved claims with quoted evidence in this run, so "
                    "there is nothing for the local checker to re-read.")
        return {}
    logger.info(
        f"Local checker: re-reading {len(claims)} approved claim(s) with "
        f"{slice_chars}-character source excerpts. This costs no money and "
        "takes roughly two and a half to four minutes per excerpt.")
    results: Dict[str, Dict[str, Any]] = {}
    t0 = time.time()
    with Runner(gguf_path=gguf_path, worker_python=worker_python,
                n_threads=n_threads) as runner:
        for i, c in enumerate(claims, 1):
            sl = slices_for_claim(c, sources, size=slice_chars)
            r = check_claim(c, sl, runner, threshold=threshold)
            results[c["id"]] = r
            if progress:
                if "error" in r:
                    logger.warning(f"  [{i}/{len(claims)}] {c['id']}: {r['error']}")
                else:
                    logger.info(
                        f"  [{i}/{len(claims)}] {c['id']}: "
                        f"{'agrees' if r['agrees'] else 'DISAGREES'} "
                        f"(score {r['score']:.3f}, {r['seconds']:.0f}s)")
    logger.info(f"Local checker finished in {(time.time() - t0) / 60:.1f} minutes.")
    return results


# --- on-disk bookkeeping (same staleness contract as deep-check, task #57) ----

def wrap(analysis: Dict[str, Any], results: Dict[str, Dict],
         checked_at: Optional[str] = None) -> Dict[str, Any]:
    return deep_check_store.wrap(analysis, granite_model.MODEL_NAME, results,
                                 checked_at=checked_at, fmt=FORMAT)


def validate(payload: Any, analysis: Dict[str, Any]):
    return deep_check_store.validate(payload, analysis, fmt=FORMAT,
                                     required_key="score")


def load_valid(run_dir: str, analysis: Dict[str, Any], filename: str = FILENAME):
    return deep_check_store.load_valid(run_dir, analysis, filename=filename,
                                       fmt=FORMAT, required_key="score")


def archive_previous(run_dir: str) -> Optional[str]:
    return deep_check_store.archive_previous(run_dir, filename=FILENAME,
                                             prev_filename=PREV_FILENAME)


def report_sentence(report: Dict[str, Any]) -> str:
    return deep_check_store.report_sentence(
        report, label="local-checker", noun_singular="answer")


def write_payload(run_dir: str, payload: Dict[str, Any]) -> str:
    path = os.path.join(run_dir, FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return path


def attach(analysis: Dict[str, Any], usable: Dict[str, Dict]) -> int:
    """Put the answers on the claim dicts the viewer reads. In memory only."""
    n = 0
    for tc in (analysis.get("text_claims") or []):
        r = usable.get(tc.get("id"))
        if r:
            tc["granite_check"] = r
            n += 1
    return n


def summary(results: Dict[str, Dict]) -> Dict[str, Any]:
    ok = [r for r in results.values() if isinstance(r, dict) and "score" in r]
    flagged = [r for r in ok if not r.get("agrees")]
    return {"checked": len(ok), "flagged": len(flagged),
            "errors": len(results) - len(ok),
            "minutes": round(sum(r.get("seconds") or 0 for r in ok) / 60.0, 1)}


def run_and_write(run_dir: str, analysis: Dict[str, Any], **kw) -> Dict[str, Any]:
    """The whole pass: score, stamp, write the file, attach for the viewer.

    Every failure is contained here: this is an optional extra opinion, so a
    missing weight file or a dead helper process must never disturb a run's
    verdicts. Returns the summary, with an "error" key when it could not run.
    """
    try:
        results = check_run(run_dir, analysis, **kw)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"The free local checker could not run (verdicts are "
                       f"unaffected): {e}")
        return {"checked": 0, "flagged": 0, "errors": 0, "minutes": 0.0,
                "error": str(e)[:300]}
    if not results:
        return {"checked": 0, "flagged": 0, "errors": 0, "minutes": 0.0}
    payload = wrap(analysis, results)
    write_payload(run_dir, payload)
    usable, _ = validate(payload, analysis)
    attach(analysis, usable)
    s = summary(results)
    logger.info(f"Local checker: {s['flagged']} of {s['checked']} approved claim(s) "
                f"got a caution mark; wrote {FILENAME}.")
    return s


__all__ = ["FORMAT", "FILENAME", "PREV_FILENAME", "MAX_SLICES_PER_CLAIM",
           "load_sources", "source_text", "approved_claims", "slices_for_claim",
           "Runner", "check_claim", "check_run", "wrap", "validate", "load_valid",
           "archive_previous", "report_sentence", "write_payload", "attach",
           "summary", "run_and_write"]
