#!/usr/bin/env python3
"""Stability study (internal label CARD160): content fingerprints (SHA-256) of
everything the measurement depends on, for the pre-registration. No model
call, reads files only.

How every number is computed (this program IS the method; publish it with
the fingerprints):

  file fingerprint      SHA-256 of the file's exact bytes (what `sha256sum`
                        prints for it).
  combined fingerprint  SHA-256 of a UTF-8 text with one line per file,
                        "<relative path><two spaces><file fingerprint>\\n",
                        every line ending in a newline, the lines sorted by
                        relative path in byte order (Python's string order on
                        the paths, which for UTF-8 paths is the order of
                        `LC_ALL=C sort`). The order inside a line is path
                        first, then fingerprint: the reverse of sha256sum's
                        own output, so `sha256sum` output piped into
                        `sha256sum` does NOT give it.
  relative to           code: the root of the measured checkout; the hard
                        sentences: benchmarks/card160/hard_rows/, plus the
                        key file benchmarks/card160/hard_rows_ground_truth.json
                        added under the path "hard_rows_ground_truth.json";
                        an input folder (sources, search index, search
                        model): that folder.
  skipped               __pycache__ folders and *.pyc files.
  missing files         a reader without our private files gets a note per
                        missing file and the rest is still computed (exit 0);
                        --into refuses to write when anything is missing.

Two outputs
  The PUBLISHED list (printed, and written into the draft with --into) holds
  only files that can change a verdict or a reported number AND will be
  public, so a reader can recompute every fingerprint in it: the program
  files the runs execute (PRIVATE_CODE excluded: the 16 sentence-splitting
  files are imported only behind --aida-grounder, which every arm leaves
  off; jev_client.py and watchdog_miner.py are loaded only by programs
  outside the checker and by tests; hhem_check.py and pair_judge.py run only
  after the verdicts are written and the release leaves them out), the
  libraries list,
  our own texts and reference lists (and the two public examples), the answer
  keys, the reworded and negated copies, the hard sentences, the reworded
  instructions, the biased and the doubting note and the study's programs. Other people's
  source documents get no published fingerprint.
  The PRIVATE RECORD (--json, benchmarks/card160/fingerprints.json) holds
  everything: all code files incl. PRIVATE_CODE, every source folder, the
  inputs the preflight compares before each arm ("inputs": the texts, sources
  and search-index caches outside the measured commit, the copies in
  data/card160_texts, and the search model's files), file by file.

  The program files the release changes (RELEASE_CHANGED) are not in the
  combined fingerprint; the per-file list gives each with its measured and
  its released fingerprint and what changed.

  ./venv/bin/python3 benchmarks/card160/fingerprints.py [--json OUT]
        [--release DIR]    compare every public program file with the same
                           path in DIR (the release, or the copy prepared for
                           it): only the RELEASE_CHANGED files may differ,
                           and each of them must.
        [--into FILE]      (needs --release; refuses on any difference the
                           plan does not state) replace the text between the marker lines
                           <!-- fingerprints:start --> / <!-- fingerprints:end -->
                           (the summary table) and
                           <!-- fingerprint-files:start --> / <!-- fingerprint-files:end -->
                           (the per-file list of public program files) inside
                           FILE; every other line of FILE is kept. Refuses
                           (exit 2, FILE untouched) when a marker pair is
                           missing or any fingerprinted file is missing.
  Without --into it only prints; it never writes any file but --json.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


def _load(name):
    spec = importlib.util.spec_from_file_location(f"card160_{name}", os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


arms = _load("arms")

CODE_FILES = ["verify_my_text.py", "requirements.txt", "config/gemini_config.json",
              "benchmarks/run_gate_gemma.sh", "benchmarks/run_gate_openrouter.sh",
              "benchmarks/gate_resume.py", "benchmarks/regression_check.py",
              "benchmarks/coverage_check.py"]
CODE_DIRS = ["modules", "config/prompts"]
# Code files planned to stay private at the release. None of them runs in any
# arm: the only imports of modules/papertrail/aida/ sit inside
# `if args.aida_grounder:` in verify_my_text.py, every arm leaves that switch
# off (arm_verify.argv_problem refuses it), and jev_client / watchdog_miner are
# imported only by programs outside the checker (the maintenance program
# watchdog.py, research benchmarks) and by tests. They stay in the private
# record only.
# hhem_check.py + pair_judge.py (the small local checker on rejected claims)
# DO run in every arm (default on, the gate scripts leave it on), but only
# after analysis.json is written (verify_my_text.py at the measured commit:
# json.dump(analysis) at line 1388, hhem_check.run_and_write at line 1461,
# analysis.json not written again), and they write only hhem_check.json,
# which no scorer reads; so they cannot change a verdict or a reported
# number. The release leaves them out (publishing preparation, the author's
# decision of 2026-10-06), so they are private here too.
PRIVATE_CODE_PREFIXES = ("modules/papertrail/aida/",)
PRIVATE_CODE_FILES = ("modules/papertrail/jev_client.py", "modules/papertrail/watchdog_miner.py",
                      "modules/papertrail/hhem_check.py", "modules/papertrail/pair_judge.py")
# Public program files whose released version differs from the measured one
# (the publishing preparation of 2026-10-06), with what changed, in the plan's
# words. `--release DIR` checks that these, and only these, differ.
RELEASE_CHANGED = {
    "verify_my_text.py": (
        "Looks up the small local checker on rejected claims instead of loading it every "
        "time it starts, and accepts the switches of the switched-off sentence-splitting "
        "path even when that path is missing; when either is missing (as in the public "
        "copy) the run prints one sentence and goes on without it; when both are present "
        "it runs exactly as before. The help text of the switches of a missing part "
        "changes, and one comment is reworded. The steps that produce verdicts, the "
        "instructions to the judge and the settings are unchanged."),
    "modules/papertrail/viewer.py": (
        "The result page's legend explains, in one sentence, that the small local checker "
        "is not part of the public copy. Display only."),
    "modules/papertrail/viewer_v2.py": (
        "Uses that same legend sentence in the second result page. Display only."),
    "modules/papertrail/matcher.py": "One comment reworded; no code changed.",
    "modules/papertrail/aida_card.py": "One comment reworded; no code changed.",
}
KEY_FILES = ["benchmarks/paper1_ground_truth.json", "benchmarks/bentonite_ground_truth.json",
             "benchmarks/chimpanzee_ground_truth.json", "benchmarks/coverage_ground_truth_essay.json",
             "benchmarks/coverage_ground_truth_bohemia.json", "benchmarks/coverage_ground_truth_pots.json"]
ARM_FILES = ["prompts/reworded_support.txt", "prompts/reworded_combined.txt", "claim_variants.json",
             "variant_check.json", "arm_verify.py", "arm_guard.py", "arms.py", "stability_table.py", "fingerprints.py",
             "make_checkout.sh", "make_hard_key.py", "prove_switches.py",
             "arms_paid.json", "arms_free.json", "arms_sonnet.json", "arms_haiku.json"]
# Texts already in the public repository (examples/).
PUBLIC_TEXTS = ("bentonite", "chimp")
# Answer keys byte-identical at the measured commit and at the commit the
# public repository was published from (a diff between that commit and the
# measured one touches only the paper1 and essay keys).
PUBLIC_KEY_FILES = ("benchmarks/bentonite_ground_truth.json",
                    "benchmarks/chimpanzee_ground_truth.json",
                    "benchmarks/coverage_ground_truth_bohemia.json",
                    "benchmarks/coverage_ground_truth_pots.json")
# The search model the checker loads (modules/papertrail/embeddings.py
# DEFAULT_MODEL), from the local Hugging Face cache.
SEARCH_MODEL = "sentence-transformers/allenai-specter"

MARKS = {"summary": ("<!-- fingerprints:start -->", "<!-- fingerprints:end -->"),
         "files": ("<!-- fingerprint-files:start -->", "<!-- fingerprint-files:end -->")}
PUBLISHED_WORDS = {"public": "already public", "with results": "published with the results"}

MISSING = []    # notes for files a reader does not have


def sha(path):
    """SHA-256 of a file, or None (with a note) when it does not exist."""
    if not os.path.isfile(path):
        MISSING.append(path)
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def tree(root, rel_dirs, rel_files=(), skip=("__pycache__",)):
    """{relative path: sha256} for the files and every file under the dirs."""
    out = {}
    for rel in rel_files:
        p = os.path.join(root, rel)
        if os.path.isfile(p):
            out[rel] = sha(p)
        else:
            MISSING.append(p)
    for rel in rel_dirs:
        base = os.path.join(root, rel)
        if not os.path.isdir(base):
            MISSING.append(base)
            continue
        for dp, dns, fns in os.walk(base):
            dns[:] = sorted(d for d in dns if d not in skip)
            for fn in sorted(fns):
                if fn.endswith(".pyc"):
                    continue
                p = os.path.join(dp, fn)
                out[os.path.relpath(p, root)] = sha(p)
    return out


def combined(files):
    lines = "".join(f"{p}  {h}\n" for p, h in sorted(files.items()))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def is_private_code(path):
    return path.startswith(PRIVATE_CODE_PREFIXES) or path in PRIVATE_CODE_FILES


def release_check(code, release_dir):
    """Compare the measured PUBLIC program files with the same paths in a
    folder holding the release. Returns {"changed": {path: {"measured",
    "release"}}, "unexpected": [public paths that differ but are not in
    RELEASE_CHANGED], "absent": [public paths missing from the release],
    "same_as_measured": [RELEASE_CHANGED paths that turned out identical],
    "private_present": [private paths the release still holds]}."""
    out = {"folder": release_dir, "changed": {}, "unexpected": [], "absent": [],
           "same_as_measured": [], "private_present": []}
    for p, h in sorted(code.items()):
        rp = os.path.join(release_dir, p)
        if is_private_code(p):
            if os.path.isfile(rp):
                out["private_present"].append(p)
            continue
        if not os.path.isfile(rp):
            out["absent"].append(p)
            continue
        rh = sha(rp)
        if p in RELEASE_CHANGED:
            if rh == h:
                out["same_as_measured"].append(p)
            out["changed"][p] = {"measured": h, "release": rh}
        elif rh != h:
            out["unexpected"].append(p)
    return out


def release_problems(rel):
    """Sentences for every way the release differs from what the plan states."""
    probs = [f"{p}: differs from the measured version but the plan does not list it"
             for p in rel["unexpected"]]
    probs += [f"{p}: missing from the release folder" for p in rel["absent"]]
    probs += [f"{p}: listed as changed but identical to the measured version"
              for p in rel["same_as_measured"]]
    return probs


def pip_freeze(python=None):
    """(sha256, text) of `pip freeze` of the given Python (default: this one)."""
    try:
        text = subprocess.run([python or sys.executable, "-m", "pip", "freeze"],
                              capture_output=True, text=True, timeout=180).stdout
    except Exception:
        return "", ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest(), text


def search_model_dir(model=SEARCH_MODEL, env=None):
    """(revision, snapshot folder) of the search model in the Hugging Face
    cache, or ('', '') when it is not there."""
    env = os.environ if env is None else env
    hub = env.get("HF_HUB_CACHE") or os.path.join(
        env.get("HF_HOME") or os.path.join(os.path.expanduser("~"), ".cache", "huggingface"), "hub")
    base = os.path.join(hub, "models--" + model.replace("/", "--"))
    try:
        with open(os.path.join(base, "refs", "main"), encoding="utf-8") as f:
            rev = f.read().strip()
    except OSError:
        return "", ""
    snap = os.path.join(base, "snapshots", rev)
    return (rev, snap) if os.path.isdir(snap) else ("", "")


def input_fingerprints(checkout, dest, names=None):
    """{label: {"path": ..., "files": {relative path: sha256}}} for the inputs
    the arms read from OUTSIDE the measured commit (or that the commit check
    does not cover): every text, reference list, sources folder and
    search-index cache of the six texts, the reworded and negated copies, the
    hard-sentence copy, and the search model. `names` limits it to labels."""
    want = (lambda label: names is None or label in names)
    out = {}

    def add_file(label, path):
        if want(label):
            out[label] = {"path": path, "files": {os.path.basename(path): sha(path)}}

    def add_dir(label, path):
        if want(label):
            out[label] = {"path": path, "files": tree(path, ["."])}

    for name, text, sources, donor in arms.GATE_TEXTS:
        add_file(f"text/{name}", os.path.join(checkout, text))
        add_file(f"refs/{name}", os.path.join(checkout, text + ".refs.txt"))
        add_dir(f"sources/{name}", os.path.join(checkout, sources))
        add_dir(f"embeddings/{name}", os.path.join(checkout, donor, "embeddings"))
        for kind in ("reworded", "negated"):
            add_file(f"{kind}/{name}", os.path.join(dest, kind, name, "my_text.md"))
            add_file(f"{kind}_refs/{name}", os.path.join(dest, kind, name, "my_text.md.refs.txt"))
    add_dir("hard_copy", os.path.join(dest, "hard"))
    if want("search_model"):
        rev, snap = search_model_dir()
        out["search_model"] = {"path": snap, "revision": rev,
                               "files": tree(snap, ["."]) if snap else {}}
        if not snap:
            MISSING.append(f"search model {SEARCH_MODEL} (Hugging Face cache)")
    return out


def compare_inputs(recorded, now):
    """Sentences for every input that differs from the private record. A
    search-index cache may GAIN files (it is keyed by content, so a new file
    serves only a new source); every recorded file must be there unchanged.
    Every other input must match file for file."""
    problems = []
    for label, cur in now.items():
        rec = recorded.get(label)
        if rec is None:
            problems.append(f"{label}: not in the private fingerprint record")
            continue
        rf, cf = rec.get("files", {}), cur.get("files", {})
        changed = sorted(p for p in rf if cf.get(p) != rf[p])
        added = sorted(p for p in cf if p not in rf)
        if changed:
            problems.append(f"{label}: {len(changed)} file(s) differ from the record, "
                            f"e.g. {changed[0]}")
        if added and not label.startswith("embeddings/"):
            problems.append(f"{label}: {len(added)} file(s) not in the record, e.g. {added[0]}")
    return problems


def build(checkout, dest, libs=None, release=None):
    del MISSING[:]
    commit = subprocess.run(["git", "-C", checkout, "rev-parse", "HEAD"], capture_output=True,
                            text=True, timeout=30).stdout.strip()
    code = tree(checkout, CODE_DIRS, CODE_FILES)
    public = {p: h for p, h in code.items() if not is_private_code(p)}
    private = {p: h for p, h in code.items() if is_private_code(p)}
    unchanged = {p: h for p, h in public.items() if p not in RELEASE_CHANGED}
    lib_sha, lib_text = libs if libs is not None else pip_freeze()
    rev, _ = search_model_dir()
    rep = {"measured_commit": commit,
           "method": ("file = SHA-256 of the exact bytes; combined = SHA-256 of the UTF-8 text "
                      "of lines '<relative path>  <file sha256>\\n', sorted by path in byte "
                      "order; see fingerprints.py"),
           "code": {"combined_all": combined(code), "files": len(code),
                    "combined_public": combined(public), "public_files": len(public),
                    "combined_unchanged": combined(unchanged), "unchanged_files": len(unchanged),
                    "combined_private": combined(private), "private_files": len(private),
                    "per_file": {p: {"sha256": h, "published": "private" if p in private
                                     else "changed for release" if p in RELEASE_CHANGED
                                     else "with results"} for p, h in code.items()}},
           "libraries": {"pip_freeze_sha256": lib_sha,
                         "packages": len([ln for ln in lib_text.splitlines() if ln.strip()])},
           "search_model": {"name": SEARCH_MODEL, "revision": rev},
           "texts": {}, "sources": {}, "keys": {}, "variants": {}, "hard": {}, "arms": {}}
    for name, text, sources, _ in arms.GATE_TEXTS:
        mark = "public" if name in PUBLIC_TEXTS else "with results"
        rep["texts"][name] = {"text": sha(os.path.join(checkout, text)),
                              "refs": sha(os.path.join(checkout, text + ".refs.txt")),
                              "published": mark}
        src = tree(os.path.join(checkout, sources), ["."])
        rep["sources"][name] = {"combined": combined(src), "files": len(src),
                                "published": "private"}
        for kind in ("reworded", "negated"):
            p = os.path.join(dest, kind, name, "my_text.md")
            rep["variants"][f"{kind}/{name}"] = {"sha256": sha(p), "published": "with results"}
    for rel in KEY_FILES:
        rep["keys"][os.path.basename(rel)] = {
            "sha256": sha(os.path.join(checkout, rel)),
            "published": "public" if rel in PUBLIC_KEY_FILES else "with results"}
    hard = tree(os.path.join(HERE, "hard_rows"), ["."])
    hard["hard_rows_ground_truth.json"] = sha(os.path.join(HERE, "hard_rows_ground_truth.json"))
    rep["hard"] = {"combined": combined(hard), "files": len(hard), "per_file": hard,
                   "published": "with results"}
    for rel in ARM_FILES:
        rep["arms"][rel] = {"sha256": sha(os.path.join(HERE, rel)), "published": "with results"}
    av = _load("arm_verify")
    rep["arms"]["biased note (exact text)"] = {
        "sha256": hashlib.sha256(av.BIAS_NOTE.encode("utf-8")).hexdigest(),
        "published": "with results"}
    rep["arms"]["doubting note (exact text)"] = {
        "sha256": hashlib.sha256(av.DOUBT_NOTE.encode("utf-8")).hexdigest(),
        "published": "with results"}
    rep["inputs"] = input_fingerprints(checkout, dest)
    if release:
        rep["release"] = release_check(code, release)
    rep["missing"] = sorted(set(MISSING))
    return rep


ARM_NAMES = {
    "prompts/reworded_support.txt": "Reworded instruction for a single source passage (reworded_support.txt)",
    "prompts/reworded_combined.txt": "Reworded instruction for several passages together (reworded_combined.txt)",
    "claim_variants.json": "The reworded and negated claims, from which the copies are built (claim_variants.json)",
    "variant_check.json": "The Sonnet reader's separate check of every reworded and negated claim (variant_check.json)",
    "arm_verify.py": "The runner that applies each arm's one change (arm_verify.py)",
    "arm_guard.py": "The guard for used-up allowances and an empty balance (arm_guard.py)",
    "arms.py": "The list of arms and their exact commands (arms.py)",
    "stability_table.py": "The program that computes every reported number (stability_table.py)",
    "fingerprints.py": "The program that computes these fingerprints (fingerprints.py)",
    "make_checkout.sh": "The script that makes the clean copy of the measured version (make_checkout.sh)",
    "make_hard_key.py": "The program that writes the hard sentences' expected verdicts (make_hard_key.py)",
    "prove_switches.py": "The no-request proof that every switch is read (prove_switches.py)",
    "arms_paid.json": "The list of paid Gemma arms the counting program reads (arms_paid.json)",
    "arms_free.json": "The list of free Gemma arms the counting program reads (arms_free.json)",
    "arms_sonnet.json": "The list of Sonnet arms the counting program reads (arms_sonnet.json)",
    "arms_haiku.json": "The list of Haiku arms the counting program reads (arms_haiku.json)",
    "biased note (exact text)": "The biased note, its exact text (one line, followed by an empty line)",
    "doubting note (exact text)": "The doubting note of the doubt arm, its exact text (one line, followed by an empty line)",
}


def markdown(rep):
    """The PUBLISHED summary table: public files that can change a verdict or
    a number, nothing a reader cannot recompute."""
    c = rep["code"]
    pw = PUBLISHED_WORDS
    L = [f"Measured commit: `{rep['measured_commit']}`", "",
         "| What | SHA-256 | Published |", "|---|---|---|",
         f"| The measured checker, its {c['unchanged_files']} program files that the release "
         f"holds unchanged (listed below), combined | `{c['combined_unchanged']}` | "
         f"{pw['with results']} |",
         f"| The installed libraries (`pip freeze`, {rep['libraries']['packages']} packages) | "
         f"`{rep['libraries']['pip_freeze_sha256']}` | {pw['with results']} |"]
    for name, d in rep["texts"].items():
        L.append(f"| Test text {name} | `{d['text']}` | {pw[d['published']]} |")
        L.append(f"| Reference list of {name} | `{d['refs']}` | {pw[d['published']]} |")
    for k, v in rep["keys"].items():
        L.append(f"| Answer key {k} | `{v['sha256']}` | {pw[v['published']]} |")
    for k, v in rep["variants"].items():
        kind, name = k.split("/")
        L.append(f"| {kind.capitalize()} copy of {name} | `{v['sha256']}` | {pw[v['published']]} |")
    h = rep["hard"]
    L.append(f"| The ten hard sentences, their two sources and their expected verdicts "
             f"({h['files']} files: the folder `hard_rows/` and the file "
             f"`hard_rows_ground_truth.json`), combined | `{h['combined']}` | {pw[h['published']]} |")
    for k, v in rep["arms"].items():
        L.append(f"| {ARM_NAMES.get(k, k)} | `{v['sha256']}` | {pw[v['published']]} |")
    return "\n".join(L) + "\n"


def files_markdown(rep):
    """The PUBLISHED per-file list: the public program files the release holds
    unchanged, then the ones changed for the release with both fingerprints
    (the release column only when build() was given a release folder). The
    private ones are in the private record only."""
    per = rep["code"]["per_file"]
    L = ["| Program file (relative to the measured checkout) | SHA-256 |", "|---|---|"]
    for p, d in sorted(per.items()):
        if d["published"] == "with results":
            L.append(f"| `{p}` | `{d['sha256']}` |")
    changed = sorted(p for p, d in per.items() if d["published"] == "changed for release")
    if changed:
        rel = (rep.get("release") or {}).get("changed", {})
        L += ["", "Changed for the release (the measured version is not public; its "
              "fingerprint is shown on request against the file itself):", "",
              "| Program file | Measured version, SHA-256 | Released version, SHA-256 | "
              "What changed |", "|---|---|---|---|"]
        for p in changed:
            r = rel.get(p, {}).get("release") or "not yet computed"
            L.append(f"| `{p}` | `{per[p]['sha256']}` | `{r}` | {RELEASE_CHANGED[p]} |")
    return "\n".join(L) + "\n"


def write_into(path, blocks):
    """Replace the text between each marker pair in `path` with blocks[key].
    Returns '' on success, else a sentence; the file is untouched on error."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    for key, body in blocks.items():
        start, end = MARKS[key]
        i, j = text.find(start), text.find(end)
        if i < 0 or j < 0 or j < i or text.count(start) != 1 or text.count(end) != 1:
            return f"{path} does not hold exactly one '{start}' ... '{end}' pair; nothing written"
        text = text[:i + len(start)] + "\n" + body + text[j:]
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return ""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--checkout", default=arms.default_checkout())
    ap.add_argument("--dest", default=arms.default_dest())
    ap.add_argument("--json")
    ap.add_argument("--into")
    ap.add_argument("--release", help="a folder holding the release (or the copy prepared "
                    "for it): compare every public program file with it; required with --into")
    args = ap.parse_args(argv)
    rep = build(args.checkout, args.dest, release=args.release)
    for m in rep["missing"]:
        print(f"note: not found, left out: {m}")
    rel_probs = release_problems(rep["release"]) if args.release else []
    for p in rel_probs:
        print(f"release: {p}")
    if args.release:
        for p in rep["release"]["private_present"]:
            print(f"note: the release folder still holds the private file {p} "
                  "(expected in a private copy, never in the public one)")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1, sort_keys=True)
    md, files_md = markdown(rep), files_markdown(rep)
    if args.into:
        if rep["missing"]:
            print(f"{len(rep['missing'])} fingerprinted file(s) missing; {args.into} not written")
            return 2
        if not args.release:
            print(f"--into needs --release <folder>; {args.into} not written")
            return 2
        if rel_probs:
            print(f"the release differs from what the plan states; {args.into} not written")
            return 2
        err = write_into(args.into, {"summary": md, "files": files_md})
        if err:
            print(err)
            return 2
        print(f"wrote the two fingerprint blocks into {args.into}")
    else:
        print(md)
        print(files_md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
