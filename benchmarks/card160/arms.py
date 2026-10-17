#!/usr/bin/env python3
"""Stability study (internal label CARD160): the arms of the stability and
invariance table, in one place.

No model calls. Six subcommands:

  build-texts [--dest DIR]
      Write the text copies the claim arms need into DIR (default
      <repo data>/card160_texts/): reworded/<text>/ and negated/<text>/ for the
      six gate texts (from claim_variants.json; each variant replaces one exact
      span inside one claim and keeps every marker), and hard/ (the hard test
      rows with their two constructed sources). Each copy holds my_text.md and
      my_text.md.refs.txt; the reworded/negated copies cite the ORIGINAL
      sources folders, so nothing is duplicated. Prints the GATE_TEXTS strings.

  commands [--checkout DIR] [--host paid|free|sonnet|haiku|all]
      Print the exact command for every REGISTERED arm of every host (the
      arms the pre-registration names; REGISTERED below). Nothing is run.

  arms-json --host H [--out FILE]
      The arms file stability_table.py reads for host H (registered arms only).

  preflight --host H --arm A [--resume]
      Everything that must hold before an arm starts, checked with no model
      call (see preflight() below).

  word-change
      How much of each claim the rewording changed (share of words).

  pin-claude
      Copy the one Claude program version the study uses into its folder
      (arm_verify.PINNED_CLAUDE_*); every Claude start refuses without it.

Hosts: paid = Gemma 4 31B on OpenRouter at full precision
(benchmarks/run_gate_openrouter.sh, needs the author's go in their own words);
free = the same model on Google's free service, through the Gemma queue
(benchmarks/run_gate_gemma.sh); sonnet / haiku = the Claude subscription
through the checker's claude-code backend, run with the free script and
MODEL=claude-code/<full model id> (pinned, not an alias that may move; no API
key, no money; it uses the weekly allowance).
"""

import argparse
import json
import os
import shlex
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
VARIANTS = os.path.join(HERE, "claim_variants.json")
MEASURED_COMMIT = "4899c40"

# name | text | sources | embeddings donor — the gate's own list (both gate
# scripts), relative to the checkout the gate runs from.
GATE_TEXTS = [
    ("paper1", "data/paper1_import/my_text.md", "data/paper1_verification/sources", "data/paper1_verification"),
    ("bentonite", "examples/bentonite/my_text.md", "examples/bentonite/sources", "data/bentonite_verification"),
    ("chimp", "examples/chimpanzee_validation/my_text.md", "examples/chimpanzee_validation/sources", "data/chimp_verification"),
    ("essay", "data/loop_rounds/round_1/project/my_text.md", "data/loop_rounds/round_1/project/sources", "data/coverage_gate_run"),
    ("bohemia", "data/loop_rounds/round_3/project/my_text.md", "data/loop_rounds/round_3/project/sources", "data/gate_run_bohemia"),
    ("pots", "data/loop_rounds/round_4/project/my_text.md", "data/loop_rounds/round_4/project/sources", "data/gate_run_pots"),
]

PROMPTS_REWORDED = ",".join([
    "pt_support_judgment_prompt.txt=" + os.path.join(HERE, "prompts", "reworded_support.txt"),
    "pt_combined_judgment_prompt.txt=" + os.path.join(HERE, "prompts", "reworded_combined.txt"),
])

# kind: how stability_table.py pairs the arm with its baseline.
# texts: "gate+hard" = the six gate texts plus the hard rows; "reworded" /
# "negated" = the variant copies of the six.
# The list order is the RUN ORDER (design review 2026-10-02, item 7; doubt
# added right after bias on 2026-10-05): r2 sits in
# the middle and r3 last, so the three unchanged runs span the whole paid
# period and a drift of the service over time is not counted against the
# later arms. The other judges run only r1, r2, r3, in that order.
ARMS = [
    {"arm": "r1", "kind": "same", "texts": "gate+hard", "env": {},
     "what": "first run, nothing changed (the baseline every other arm is compared with)"},
    {"arm": "prompt", "kind": "same", "texts": "gate+hard",
     "env": {"CARD160_PROMPTS": PROMPTS_REWORDED},
     "what": "both verdict instructions reworded with the same meaning"},
    {"arm": "shuffle", "kind": "same", "texts": "gate+hard",
     "env": {"CARD160_SHUFFLE_SEED": "1"},
     "what": "the evidence handed to the judge in a different order"},
    {"arm": "bias", "kind": "same", "texts": "gate+hard",
     "env": {"CARD160_BIAS_NOTE": "1"},
     "what": "a note in the instruction: the writer is confident the claim is supported"},
    # The ninth paid arm (the author's decision of 2026-10-05): the
    # opposite note at the same place, so a judge that leans toward the writer
    # can be told apart from one that any note unsettles. It judges the same
    # texts as bias and runs right after it.
    {"arm": "doubt", "kind": "same", "texts": "gate+hard",
     "env": {"CARD160_DOUBT_NOTE": "1"},
     "what": "a note in the instruction: a reviewer doubts the claim is supported"},
    {"arm": "r2", "kind": "same", "texts": "gate+hard", "env": {},
     "what": "second run, nothing changed"},
    {"arm": "reworded", "kind": "reworded", "texts": "reworded", "env": {},
     "what": "every judged claim reworded with the same meaning"},
    {"arm": "negated", "kind": "negated", "texts": "negated", "env": {},
     "what": "53 claims an earlier run judged supported, turned into their opposite"},
    {"arm": "r3", "kind": "same", "texts": "gate+hard", "env": {},
     "what": "third run, nothing changed"},
]
REPEATS = ("r1", "r2", "r3")

# The Claude judges are named by full model id, so the model cannot change
# under the study when the CLI's "sonnet"/"haiku" alias moves to a newer one.
HOSTS = {
    "paid": {"script": "benchmarks/run_gate_openrouter.sh", "model": None, "conc": "4"},
    "free": {"script": "benchmarks/run_gate_gemma.sh", "model": None, "conc": "2"},
    "sonnet": {"script": "benchmarks/run_gate_gemma.sh", "model": "claude-code/claude-sonnet-5-5",
               "conc": "2"},
    # Haiku 5.5 since 2026-10-08 (the author's ruling; was claude-haiku-4-5-20251001
    # until then; no Haiku arm had counted). The same tags card160haikur1..r3 are
    # kept; the partial 4.5 folder is moved aside unread before haiku r1 starts.
    "haiku": {"script": "benchmarks/run_gate_gemma.sh",
              "model": "claude-code/claude-haiku-5-5", "conc": "2"},
}

# The arms the pre-registration registers for each judge. Commands, arms files
# and the preflight exist only for these; adding one later is a new, separately
# dated addition to the pre-registration.
REGISTERED = {
    # In run order (ARMS above).
    "paid": ("r1", "prompt", "shuffle", "bias", "doubt", "r2", "reworded", "negated", "r3"),
    "free": ("r1", "r2", "r3"),
    # Three unchanged runs per Claude judge (the author's decision of 2026-10-02): the
    # Claude CLI runs at its own temperature, so one run cannot separate the
    # judge's own randomness from its disagreement with Gemma.
    "sonnet": ("r1", "r2", "r3"),
    "haiku": ("r1", "r2", "r3"),
}


def registered_arms(host):
    return [a for a in ARMS if a["arm"] in REGISTERED[host]]


# The main checkout: a card worktree links data/ to it, so its real path names
# the main checkout's data folder, which every checkout shares.
DATA = os.path.realpath(os.path.join(REPO, "data"))
MAIN = os.path.dirname(DATA)


# The local job queue that runs one free-Gemma job at a time (outside the repo).
GEMMA_QUEUE = os.path.join(os.path.expanduser("~"), ".config", "claim-grounding", "bin",
                           "gemma_queue.py")
# The queue's time limit for one free arm. A normal free arm of the seven
# texts took about 11 hours; the runner may retry one text's failed claims
# for up to 24 hours (arm_verify.RETRY_LIMIT_S), so 48 hours holds both.
FREE_QUEUE_TIMEOUT_H = 48


def default_dest():
    return os.path.join(DATA, "card160_texts")


def default_checkout():
    return os.path.join(MAIN, ".claude", "worktrees", "card160_measured")


def tag(host, arm):
    return f"card160{host}{arm}"


def apply_variants(md, rows, field):
    """The markdown with every row's md_original replaced by row[field]. Each
    original must occur exactly once; raises ValueError otherwise."""
    for r in rows:
        new = r.get(field)
        if not r.get("md_original") or not new:
            continue
        n = md.count(r["md_original"])
        if n != 1:
            raise ValueError(f"{r['text_name']}/{r['id']}: original span found {n} times")
        md = md.replace(r["md_original"], new)
    return md


def load_rows(path=VARIANTS):
    with open(path, encoding="utf-8") as f:
        return json.load(f)["rows"]


def build_texts(dest, checkout_root=REPO, variants=VARIANTS):
    """Write the copies; return {"reworded": GATE_TEXTS str, "negated": ..., "hard": ...}."""
    rows = load_rows(variants)
    out = {}
    for field, kind in (("md_reworded", "reworded"), ("md_negated", "negated")):
        entries = []
        for name, text, sources, donor in GATE_TEXTS:
            src_md = os.path.join(checkout_root, text)
            with open(src_md, encoding="utf-8") as f:
                md = f.read()
            new = apply_variants(md, [r for r in rows if r["text_name"] == name], field)
            d = os.path.join(dest, kind, name)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "my_text.md"), "w", encoding="utf-8") as f:
                f.write(new)
            with open(src_md + ".refs.txt", encoding="utf-8") as f:
                refs = f.read()
            with open(os.path.join(d, "my_text.md.refs.txt"), "w", encoding="utf-8") as f:
                f.write(refs)
            entries.append(f"{name}|{os.path.join(d, 'my_text.md')}|{sources}|{donor}")
        out[kind] = ";".join(entries)
    hd = os.path.join(dest, "hard")
    os.makedirs(os.path.join(hd, "sources"), exist_ok=True)
    src = os.path.join(HERE, "hard_rows")
    for rel in ("my_text.md", "my_text.md.refs.txt"):
        with open(os.path.join(src, rel), encoding="utf-8") as f:
            data = f.read()
        with open(os.path.join(hd, rel), "w", encoding="utf-8") as f:
            f.write(data)
    for fn in sorted(os.listdir(os.path.join(src, "sources"))):
        with open(os.path.join(src, "sources", fn), encoding="utf-8") as f:
            data = f.read()
        with open(os.path.join(hd, "sources", fn), "w", encoding="utf-8") as f:
            f.write(data)
    out["hard"] = f"hard|{os.path.join(hd, 'my_text.md')}|{os.path.join(hd, 'sources')}|"
    return out


def _split(md_path):
    sys.path.insert(0, REPO)
    from modules.papertrail import text_decomposer
    with open(md_path, encoding="utf-8") as f:
        raw = f.read()
    _, body = text_decomposer.parse_references(raw, text_path=md_path)
    return text_decomposer.extract_claims(body)


def split_problems(dest, checkout_root=REPO, variants=VARIANTS):
    """Run the checker's own splitter on every original text and its copies;
    list every way a copy is split differently (claim count, a claim's markers,
    or a varied claim's new words not inside the claim with the same id). An
    empty list means the scorer can pair every varied claim by its id."""
    import re as _re

    def n(s):
        return _re.sub(r"\s+", " ", _re.sub(r"\[\[[^\]]*\]\]", "", s or "")).strip().casefold()

    rows = load_rows(variants)
    problems = []
    for name, text, _, _ in GATE_TEXTS:
        orig = _split(os.path.join(checkout_root, text))
        for kind, field in (("reworded", "md_reworded"), ("negated", "md_negated")):
            copy = _split(os.path.join(dest, kind, name, "my_text.md"))
            if len(copy) != len(orig):
                problems.append(f"{kind}/{name}: {len(copy)} claims, original has {len(orig)}")
                continue
            for a, b in zip(orig, copy):
                if (a.get("id"), a.get("markers")) != (b.get("id"), b.get("markers")):
                    problems.append(f"{kind}/{name}/{a.get('id')}: markers or id moved")
            by_id = {c.get("id"): c for c in copy}
            for r in rows:
                if r["text_name"] != name or not r.get(field):
                    continue
                c = by_id.get(r["id"])
                if c is None or n(r[field]) not in n(c.get("text")):
                    problems.append(f"{kind}/{name}/{r['id']}: changed words not in that claim")
    return problems


def word_change_share(a, b):
    """Share of the words of claim `a` that claim `b` does not keep in order
    (1 - matched words / words of a, by difflib on lower-cased words, markers
    left out)."""
    import difflib
    import re as _re

    def words(s):
        return _re.findall(r"\w+", _re.sub(r"\[\[[^\]]*\]\]", "", s or "").casefold())
    wa, wb = words(a), words(b)
    if not wa:
        return 0.0
    m = difflib.SequenceMatcher(a=wa, b=wb, autojunk=False)
    return 1.0 - sum(blk.size for blk in m.get_matching_blocks()) / len(wa)


def reworded_word_change(dest, checkout_root=REPO, variants=VARIANTS):
    """{text: {"claims": n, "mean": x, "min": x, "max": x}} over the claims the
    reworded copy changes: how much of each whole claim the rewording touches."""
    rows = load_rows(variants)
    out = {}
    for name, text, _, _ in GATE_TEXTS:
        ids = {r["id"] for r in rows if r["text_name"] == name and r.get("md_reworded")}
        orig = {c.get("id"): c.get("text") for c in _split(os.path.join(checkout_root, text))}
        copy = {c.get("id"): c.get("text")
                for c in _split(os.path.join(dest, "reworded", name, "my_text.md"))}
        shares = [word_change_share(orig[i], copy[i]) for i in sorted(ids) if i in orig and i in copy]
        if shares:
            out[name] = {"claims": len(shares), "mean": sum(shares) / len(shares),
                         "min": min(shares), "max": max(shares)}
    return out


def gate_texts_for(arm, dest):
    gate = ";".join("|".join(t) for t in GATE_TEXTS)
    if arm["texts"] == "gate+hard":
        hd = os.path.join(dest, "hard")
        return gate + f";hard|{os.path.join(hd, 'my_text.md')}|{os.path.join(hd, 'sources')}|"
    kind = arm["texts"]
    return ";".join(f"{name}|{os.path.join(dest, kind, name, 'my_text.md')}|{sources}|{donor}"
                    for name, _, sources, donor in GATE_TEXTS)


# No go is stored anywhere in this folder. Every paid line of COMMANDS.md
# carries the placeholder from command_env; the author's go is put into
# AUTHOR_GO only when the arm is started, and the runner (arm_verify.py)
# refuses the placeholder before anything is sent.

# The spending ceiling of every paid arm (author 2026-10-05, raised from 1.20):
# above one arm's measured cost ($0.52 to $0.87) and the negated arm's
# estimated, not measured, cost (up to $1.16), so it should not cut an arm
# short; money is spent only as far as an arm needs it.
PAID_MAX_USD = "1.60"


def command(host, arm, checkout, dest):
    h = HOSTS[host]
    env = command_env(host, arm, checkout, dest)
    assigns = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
    run = f"{assigns} bash {os.path.join(checkout, h['script'])} {tag(host, arm['arm'])} {h['conc']}"
    if host == "free":
        return (f"python3 {GEMMA_QUEUE} add "
                f"--desc {shlex.quote('stability study: ' + arm['what'])} "
                f"--timeout-hours {FREE_QUEUE_TIMEOUT_H} "
                f"--command {shlex.quote(run)} --workdir {shlex.quote(checkout)} "
                "--approved '<the author's go for this queue entry>' --session <session id>")
    # Paid and Claude arms run by hand, logged to disk (author ruling 2026-08-10).
    return (f"cd {shlex.quote(checkout)} && ./run_logged.sh {tag(host, arm['arm'])} -- "
            f"env {run}")


def command_env(host, arm, checkout, dest):
    """The environment switches of one arm's command — the single source both
    COMMANDS.md and prove_switches.py are built from."""
    h = HOSTS[host]
    env = {"CARD160_CHECKOUT": checkout, "CARD160_ARM": tag(host, arm["arm"]),
           "GATE_VERIFY_SCRIPT": os.path.join(HERE, "arm_verify.py"),
           "GATE_TEXTS": gate_texts_for(arm, dest), "STABILITY_RUN": "1"}
    env.update(arm["env"])
    if h["model"]:
        env["MODEL"] = h["model"]
        env["CARD160_CLAUDE_FAIR"] = "1"
    if host == "paid":
        env["AUTHOR_GO"] = "<the author's own words and date>"
        env["MAX_USD"] = PAID_MAX_USD
        # The free-only money lock (FREE_GOOGLE_ONLY=1, set for unattended
        # sessions) refuses every OpenRouter client at construction, so a paid
        # arm started under it would crash on every text without a request.
        # The paid go lifts it for this one command only.
        env["FREE_GOOGLE_ONLY"] = "0"
    return env


def arms_json(host):
    """The comparisons stability_table.py makes for one judge (host):

    - "baseline": r1's own accuracy;
    - the three repeat pairs r2-r1, r3-r1, r3-r2 ("role": "repeat"; their
      smallest and largest count of changed verdicts is the range a one-run
      arm is read against);
    - every one-run arm against r1, with "repeats" naming r2 and r3, so each of
      its changed verdicts is sorted into "also changed in a repeat" or "moved
      by the arm" (r1, r2 and r3 agree and the arm differs from all three);
    - for a judge other than paid Gemma: its r1 against paid Gemma's r1, and
      the claim-by-claim comparison of all three runs of each judge
      ("kind": "judges": disagreement only on claims where all three runs of
      each judge agree, and majority verdict against majority verdict)."""
    base = tag(host, "r1")
    arms = [{"name": f"{host}: first run (its own accuracy; nothing to compare)",
             "kind": "baseline", "run": base, "baseline": base}]
    reps = [a["arm"] for a in registered_arms(host) if a["arm"] in REPEATS]
    for later, earlier in (("r2", "r1"), ("r3", "r1"), ("r3", "r2")):
        if later in reps and earlier in reps:
            arms.append({"name": f"{host}: {later} against {earlier} (nothing changed)",
                         "kind": "same", "role": "repeat",
                         "run": tag(host, later), "baseline": tag(host, earlier)})
    for a in registered_arms(host):
        if a["arm"] in REPEATS:
            continue
        arms.append({"name": f"{host}: {a['what']}", "kind": a["kind"],
                     "run": tag(host, a["arm"]), "baseline": base,
                     "repeats": [tag(host, "r2"), tag(host, "r3")]})
    if host != "paid":
        arms.append({"name": f"{host} first run against paid Gemma's first run",
                     "kind": "same", "role": "judges", "run": base,
                     "baseline": tag("paid", "r1")})
        arms.append({"name": f"{host} against paid Gemma, all three runs of each, claim by claim",
                     "kind": "judges",
                     "runs": [tag(host, r) for r in REPEATS],
                     "baseline_runs": [tag("paid", r) for r in REPEATS],
                     "run": base, "baseline": tag("paid", "r1")})
    return {"description": f"stability study arms for host {host}; baseline {base}",
            "arms": arms}


def _texts_of(arm, dest):
    """[(name, text path, sources path)] of an arm, relative paths unresolved."""
    out = []
    for row in gate_texts_for(arm, dest).split(";"):
        name, text, sources, _ = row.split("|")
        out.append((name, text, sources))
    return out


def _arm_verify():
    import importlib.util
    spec = importlib.util.spec_from_file_location("card160_arm_verify_for_arms",
                                                  os.path.join(HERE, "arm_verify.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _other_models(out_dir, want):
    """Judge models recorded in <out_dir>/.card160_runs.jsonl (one line per
    start, arm_verify.record_run) that are not `want`. Same arm tag, other
    judge = the Haiku 4.5 -> 5.5 change of 2026-10-08: a resume must never mix
    two judges in one folder. `want` None (Gemma hosts, model from the gate
    script) = no check."""
    if not want:
        return []
    seen = set()
    p = os.path.join(out_dir, ".card160_runs.jsonl")
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    m = json.loads(line).get("model") or ""
                except ValueError:
                    continue
                if m and m != want:
                    seen.add(m)
    return sorted(seen)


def preflight(host, arm_name, checkout=None, dest=None, root=None, resume=False,
              want_commit=None, environ=None, check_inputs=True, fingerprints_file=None):
    """Everything that must hold before an arm's command is started, checked
    without a model call. Returns a list of problems (empty = ready).

    - the measured checkout exists, sits exactly at the measured commit and has
      no edited tracked file;
    - every file the command names exists (the runner, the texts, their
      reference lists, the source folders, the prompt variants);
    - the copied texts are split by the checker exactly like their originals;
    - the arm's output folders do not exist yet (so nothing earlier can be
      reused: a repeat must ask again) — with resume=True a folder may exist
      only when it carries THIS arm's stamp;
    - no stop file of this judge's kind is waiting (arm_guard.py);
    - no answer-reuse switch is set in the environment, and neither the gate's
      PROMPTS nor its EXTRA_FLAGS;
    - every input outside the measured commit (texts, reference lists, sources,
      search-index caches, the copies, the search model) matches the private
      fingerprint record benchmarks/card160/fingerprints.json file for file."""
    import subprocess
    checkout = checkout or default_checkout()
    dest = dest or default_dest()
    root = root or DATA
    environ = os.environ if environ is None else environ
    problems = []
    arm = next((a for a in ARMS if a["arm"] == arm_name), None)
    if arm is None or host not in HOSTS:
        return [f"unknown host/arm {host}/{arm_name}"]
    if arm_name not in REGISTERED[host]:
        return [f"{host}/{arm_name} is not a registered arm of the pre-registration "
                f"(registered for {host}: {', '.join(REGISTERED[host])})"]
    want = want_commit or MEASURED_COMMIT
    try:
        head = subprocess.run(["git", "-C", checkout, "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=30).stdout.strip()
        dirty = subprocess.run(["git", "-C", checkout, "status", "--porcelain",
                                "--untracked-files=no"], capture_output=True, text=True,
                               timeout=60).stdout.strip()
    except Exception as e:
        head, dirty = "", f"git failed: {e}"
    if not head.startswith(want):
        problems.append(f"checkout {checkout} is at {head or 'nothing'}, not {want}")
    if dirty:
        problems.append(f"checkout {checkout} has edited tracked files: {dirty[:200]}")
    for f in (os.path.join(HERE, "arm_verify.py"), os.path.join(HERE, "arm_guard.py"),
              os.path.join(checkout, HOSTS[host]["script"])):
        if not os.path.isfile(f):
            problems.append(f"missing file {f}")
    for name, text, sources in _texts_of(arm, dest):
        t = text if os.path.isabs(text) else os.path.join(checkout, text)
        s = sources if os.path.isabs(sources) else os.path.join(checkout, sources)
        for f in (t, t + ".refs.txt"):
            if not os.path.isfile(f):
                problems.append(f"{name}: missing {f}")
        if not os.path.isdir(s) or not os.listdir(s):
            problems.append(f"{name}: no sources in {s}")
        out = os.path.join(root, f"gate_{tag(host, arm_name)}_{name}")
        if os.path.exists(out):
            stamp_file = os.path.join(out, ".card160_arm")
            mine = False
            if os.path.isfile(stamp_file):
                with open(stamp_file, encoding="utf-8") as fh:
                    try:
                        mine = json.load(fh).get("CARD160_ARM") == tag(host, arm_name)
                    except ValueError:
                        mine = False
            if not resume:
                problems.append(f"{name}: output folder {out} already exists — a fresh arm "
                                "must write into new folders (use a new tag, or --resume "
                                "to continue this same arm)")
            elif not mine:
                problems.append(f"{name}: {out} exists but was not made by this arm")
            else:
                other = _other_models(out, HOSTS[host]["model"])
                if other:
                    problems.append(f"{name}: {out} was started with another judge "
                                    f"({', '.join(other)}), not {HOSTS[host]['model']}; it "
                                    "cannot be resumed (move it aside unread)")
    if host in ("sonnet", "haiku"):
        problem = _arm_verify().pinned_claude_problem()
        if problem:
            problems.append(problem)
    for spec in arm["env"].get("CARD160_PROMPTS", "").split(","):
        if "=" in spec and not os.path.isfile(spec.split("=", 1)[1]):
            problems.append(f"missing prompt variant {spec.split('=', 1)[1]}")
    if arm["texts"] in ("reworded", "negated"):
        try:
            problems += [f"split: {p}" for p in split_problems(dest, checkout_root=checkout)]
        except Exception as e:
            problems.append(f"split check failed: {type(e).__name__}: {e}")
    kind = {"paid": "openrouter_balance", "sonnet": "claude_limit",
            "haiku": "claude_limit"}.get(host)
    if kind and os.path.isfile(os.path.join(root, f".card160_STOP_{kind}")):
        problems.append(f"stop file {os.path.join(root, '.card160_STOP_' + kind)} is waiting "
                        f"(arm_guard.py status / clear {kind})")
    for k in ("PAPERTRAIL_REUSE_FROM", "REUSE_FROM"):
        if environ.get(k):
            problems.append(f"{k} is set in the environment; a stability arm must ask again")
    # A gate PROMPTS would send the run past the runner (through
    # verify_with_prompts.py: no commit refusal, no guard, no stamp); a gate
    # EXTRA_FLAGS would add a checker switch no arm registers.
    for k in ("PROMPTS", "EXTRA_FLAGS"):
        if environ.get(k, "").strip():
            problems.append(f"{k} is set in the environment ({environ[k]!r}); a stability arm "
                            "sets nothing there (its instructions go through CARD160_PROMPTS)")
    if check_inputs:
        problems += input_problems(arm, checkout, dest, fingerprints_file)
    return problems


_INPUT_CACHE = {}


def input_labels(arm):
    """The private-record labels of every input this arm reads from outside the
    measured commit (fingerprints.input_fingerprints)."""
    labels = ["search_model"]
    for name, _, _, _ in GATE_TEXTS:
        labels += [f"sources/{name}", f"embeddings/{name}"]
        if arm["texts"] == "gate+hard":
            labels += [f"text/{name}", f"refs/{name}"]
        else:
            labels += [f"{arm['texts']}/{name}", f"{arm['texts']}_refs/{name}"]
    if arm["texts"] == "gate+hard":
        labels.append("hard_copy")
    return labels


def input_problems(arm, checkout, dest, fingerprints_file=None):
    """Compare the arm's inputs outside the measured commit (texts, reference
    lists, sources, search-index caches, the copies, the search model) with
    the private fingerprint record; a sentence per difference."""
    path = fingerprints_file or os.path.join(HERE, "fingerprints.json")
    if not os.path.isfile(path):
        return [f"no private fingerprint record at {path}"]
    with open(path, encoding="utf-8") as f:
        recorded = json.load(f).get("inputs") or {}
    import importlib.util
    spec = importlib.util.spec_from_file_location("card160_fingerprints_pf",
                                                  os.path.join(HERE, "fingerprints.py"))
    fp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fp)
    labels = tuple(input_labels(arm))
    key = (checkout, dest, labels)
    if key not in _INPUT_CACHE:
        _INPUT_CACHE[key] = fp.input_fingerprints(checkout, dest, names=set(labels))
    return [f"input changed: {p}" for p in fp.compare_inputs(recorded, _INPUT_CACHE[key])]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build-texts")
    b.add_argument("--dest", default=default_dest())
    c = sub.add_parser("commands")
    c.add_argument("--checkout", default=default_checkout())
    c.add_argument("--dest", default=default_dest())
    c.add_argument("--host", default="all", choices=list(HOSTS) + ["all"])
    c.add_argument("--out")
    j = sub.add_parser("arms-json")
    j.add_argument("--host", required=True, choices=list(HOSTS))
    j.add_argument("--out")
    p = sub.add_parser("preflight")
    p.add_argument("--host", required=True, choices=list(HOSTS))
    p.add_argument("--arm", required=True, choices=[a["arm"] for a in ARMS] + ["all"])
    p.add_argument("--checkout", default=default_checkout())
    p.add_argument("--dest", default=default_dest())
    p.add_argument("--resume", action="store_true",
                   help="allow folders this same arm already wrote (continuing after a stop)")
    sub.add_parser("pin-claude", help="copy the pinned Claude program version into the "
                   "study's folder (arm_verify.PINNED_CLAUDE_*); no model call")
    w = sub.add_parser("word-change")
    w.add_argument("--checkout", default=default_checkout())
    w.add_argument("--dest", default=default_dest())
    args = ap.parse_args(argv)
    if args.cmd == "pin-claude":
        av = _arm_verify()
        try:
            print("pinned Claude program:", av.pin_claude(), av.PINNED_CLAUDE_VERSION,
                  av.PINNED_CLAUDE_SHA256)
        except ValueError as e:
            print("not pinned:", e)
            return 1
        return 0
    if args.cmd == "word-change":
        for name, d in reworded_word_change(args.dest, checkout_root=args.checkout).items():
            print(f"{name}: {d['claims']} reworded claims; share of the claim's words changed: "
                  f"mean {100 * d['mean']:.0f}%, lowest {100 * d['min']:.0f}%, "
                  f"highest {100 * d['max']:.0f}%")
        return 0
    if args.cmd == "preflight":
        names = list(REGISTERED[args.host]) if args.arm == "all" else [args.arm]
        bad = 0
        for n in names:
            probs = preflight(args.host, n, args.checkout, args.dest, resume=args.resume)
            print(f"{args.host}/{n}: " + ("ready" if not probs else f"{len(probs)} problem(s)"))
            for pr in probs:
                print("  -", pr)
            bad += bool(probs)
        return 1 if bad else 0
    if args.cmd == "build-texts":
        for kind, s in build_texts(args.dest).items():
            print(f"GATE_TEXTS for {kind}: {s}")
        problems = split_problems(args.dest)
        for p in problems:
            print("SPLIT PROBLEM:", p)
        print(f"split check: {len(problems)} problem(s)")
        return 1 if problems else 0
    if args.cmd == "commands":
        # The pre-registered start order: Claude judges first, then paid, then free.
        hosts = ["sonnet", "haiku", "paid", "free"] if args.host == "all" else [args.host]
        lines = ["# NOT PUBLISHED: this file holds this computer's paths and the go placeholders. "
                 "The published copy of the commands is arms.py itself (command_env).",
                 f"# Stability study arm commands. Measured version: commit {MEASURED_COMMIT}, "
                 f"checkout {args.checkout} (benchmarks/card160/make_checkout.sh).",
                 "# Generated by: ./venv/bin/python3 benchmarks/card160/arms.py commands --out ...",
                 "# Only the arms the pre-registration registers are listed: paid Gemma all "
                 "nine, free Gemma r1 r2 r3, Sonnet r1 r2 r3, Haiku r1 r2 r3 (18 in all), "
                 "each judge's arms in run order (paid: r1 prompt shuffle bias doubt r2 reworded "
                 "negated r3).",
                 "# Run so far: nothing counts. A first Sonnet r1 start (2026-10-02 21:45, stopped at "
                 "01:00 on 2026-10-03 inside paper1) is not used: its folder was moved aside unread "
                 "to data/card160_discarded_sonnetr1_partial_2026-10-02, and Sonnet r1 starts again "
                 "from zero. The Haiku judge is Haiku 5.5 since 2026-10-08 (the author's "
                 "ruling); a partial Haiku 4.5 r1 start (37 of 81 paper1 claims, stopped "
                 "2026-10-08 09:09) is not used: data/gate_card160haikur1_paper1 must be moved "
                 "aside unread to data/card160_discarded_haiku45r1_partial_2026-10-08 before "
                 "Haiku r1 starts (the preflight refuses while it is there). Every Claude start uses the study's one fixed copy of the Claude "
                 "program (make it once with `arms.py pin-claude`). "
                 "Paid lines need the author's go in their own words "
                 "in AUTHOR_GO, given when the arm is started; "
                 "free lines need the author's go for the queue entry.",
                 "",
                 "## Before every arm (no model call)",
                 "",
                 "```",
                 "./venv/bin/python3 benchmarks/card160/arms.py pin-claude   # Claude judges only, once",
                 "./venv/bin/python3 benchmarks/card160/arms.py preflight --host <host> --arm <arm>",
                 "./venv/bin/python3 benchmarks/card160/arm_guard.py status",
                 f"./venv/bin/python3 benchmarks/card160/arm_guard.py balance --min {PAID_MAX_USD}   # paid arms only",
                 "```",
                 "",
                 "The preflight says `ready` only when the measured checkout sits exactly at the "
                 "measured commit with no edited file, every file the command names exists, the "
                 "copied texts split like their originals, the arm's output folders do not exist yet "
                 "(a repeat must ask again, so nothing earlier can be reused), no stop file of that "
                 "judge's kind is waiting, for a Claude judge the study's fixed copy of the Claude "
                 "program is in place with its pinned fingerprint, no answer-reuse switch is set, "
                 "the gate's `PROMPTS` and "
                 "`EXTRA_FLAGS` are empty, and every input outside the measured commit (texts, "
                 "reference lists, sources, search-index caches, the copies and the search model) "
                 "matches the private fingerprint record `fingerprints.json` file for file.",
                 "",
                 "## If an arm stops",
                 "",
                 "- **Claude judges (sonnet, haiku).** A used-up five-hour allowance does not stop the "
                 "arm: each call waits until the reset time the message names (plus two minutes; "
                 "when the message names a weekday or a date instead of a time, 10 minutes at a "
                 "time) and asks again; every wait is written to `<folder>/.card160_limit_waits.jsonl`. Only "
                 "when the waiting would pass 6 hours (`CARD160_LIMIT_MAX_WAIT_H`), for example on "
                 "the weekly limit, does the arm stop with one line `CARD160 SUBSCRIPTION LIMIT` and "
                 "the stop file `data/.card160_STOP_claude_limit`. After the reset: "
                 "`arm_guard.py clear claude_limit`, then run the SAME command again. The free "
                 "script skips every text it already finished; the text that was cut is judged "
                 "again from its first claim: a cut text never wrote a result file, and the "
                 "runner moves aside the checker's save-as-you-go journal of the cut start "
                 "(`.card160_discarded_checkpoint_<time>.jsonl`), so nothing half-done is kept.",
                 "- **Paid Gemma.** An empty balance stops the arm at once with one line "
                 "`CARD160 BALANCE EMPTY` and the stop file `data/.card160_STOP_openrouter_balance`; "
                 "the gate's retry pass and the remaining texts then exit at once without a request. "
                 "Before every text the balance is also read (no model call) and a text does not "
                 "start below $0.30 (`CARD160_MIN_BALANCE_USD`). After the top-up: "
                 "`arm_guard.py clear openrouter_balance`, then run the same command with "
                 "`GATE_TEXTS` cut down to the texts that have no `analysis.json` yet (the paid "
                 "script has no skip of its own; re-running a finished text would pay for it twice).",
                 "- **Free Gemma.** Google's free service only slows down (the client waits), and "
                 f"the queue entry has a {FREE_QUEUE_TIMEOUT_H}-hour limit. If the limit ends the job, "
                 "queue the SAME command again: the free script skips every text it already "
                 "finished.",
                 "- **Every judge: a call that fails is asked again until none fail** (the author "
                 "chose on 2026-10-02 to ask a failed claim again until none is left). After the gate's second pass, the "
                 "runner repeats that pass, with a 10-minute pause, while any claim's verdict still "
                 "came from a failed call; each repeat judges again only a claim marked as coming "
                 "from a failed call or whose extra check failed (its new answer replaces the old "
                 "one), and every other claim keeps its verdict. Every pass is written to "
                 "`<folder>/.card160_retries.jsonl`; an old `.card160_retry_report.json` is deleted "
                 "when the repeats begin. The "
                 "repeats of one text stop when no claim fails, when a stop file of the judge's "
                 "kind appears (nothing is sent after it), on paid Gemma when the arm's spending "
                 "(priced as the paid script prices it) is above `MAX_USD`, after three passes in a row that ended "
                 "with an error and wrote no result, or when 24 hours have passed since the second "
                 "pass began. A claim still failed then is named, with its error, in "
                 "`<folder>/.card160_retry_report.json` and in one line `CARD160 RETRY END`. The "
                 "scorer leaves out any claim whose verdict still came from a failed call, in either "
                 "run, counts it in its own column, lists it with its error, and names the text; "
                 "such a claim is never counted as a changed verdict or a wrong row."]
        for host in hosts:
            lines.append(f"\n## host: {host}")
            for a in registered_arms(host):
                lines.append(f"\n### {a['arm']}: {a['what']}\n")
                lines.append("```\n" + command(host, a, args.checkout, args.dest) + "\n```")
        text = "\n".join(lines) + "\n"
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text)
        else:
            print(text)
        return 0
    spec = arms_json(args.host)
    text = json.dumps(spec, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
