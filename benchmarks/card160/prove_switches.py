#!/usr/bin/env python3
"""Stability study (internal label CARD160): prove that every switch an arm's command passes is really read, by
running the MEASURED checkout's own gate scripts with each arm's exact
switches (arms.command_env, the same function COMMANDS.md is written from) —
with no model request possible:

  * EXTRA_FLAGS="--estimate": the checker prints its price estimate and returns
    BEFORE it builds any model client (verify_my_text.py, `if args.estimate:
    return` comes before the first LLMClient(...)), so not one request exists;
  * OPENROUTER_API_KEY is a dummy, so the paid script never reads the real key;
  * CARD160_BALANCE_READ=0 skips the free balance read;
  * every output folder goes under a proof root (GATE_OUT_ROOT), never data/.

One text per case keeps it short: the hard rows for the arms that run the six
texts plus the hard rows, the bentonite copy for the reworded and negated arms.

  ./venv/bin/python3 benchmarks/card160/prove_switches.py [--out-root DIR]
        [--json benchmarks/card160/switch_proof.json]

Exit 0 when every expected line and stamp was seen in every case.
"""

import argparse
import datetime as _dt
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
arm_verify = _load("arm_verify")

PROOF_GO = ("offline proof 2026-10-02 (stability study): checker in --estimate mode, which returns "
            "before any model client exists; dummy key; nothing can be sent")
CLEARED = ("PAPERTRAIL_REUSE_FROM", "PAPERTRAIL_RECORD_REQUESTS", "PAPERTRAIL_STABILITY_RUN",
           "PAPERTRAIL_LLM_EXTRA_BODY", "PROMPTS", "EXTRA_FLAGS", "REUSE_FROM", "RECORD_REQUESTS",
           "STABILITY_RUN", "AUTHOR_GO", "ESTIMATE_ONLY", "SCORE_ONLY", "MAX_USD", "MODEL",
           "GATE_TEXTS", "GATE_OUT_ROOT", "GATE_VERIFY_SCRIPT", "OPENROUTER_API_KEY",
           "CARD160_PROMPTS", "CARD160_SHUFFLE_SEED", "CARD160_BIAS_NOTE", "CARD160_DOUBT_NOTE",
           "CARD160_CLAUDE_FAIR",
           "CARD160_ARM", "CARD160_CHECKOUT", "RESUME")
DEFAULT_MODEL = {"paid": "openrouter/google/gemma-4-31b-it", "free": "gemini/gemma-4-31b-it",
                 "sonnet": "claude-code/claude-sonnet-5-5",
                 "haiku": "claude-code/claude-haiku-5-5"}

# Every registered arm of every judge: paid 9, free 3, Sonnet 3, Haiku 3.
CASES = [(h, a) for h in ("paid", "free", "sonnet", "haiku") for a in arms.REGISTERED[h]]


def one_text(arm, dest):
    rows = arms.gate_texts_for(arm, dest).split(";")
    want = "hard" if arm["texts"] == "gate+hard" else "bentonite"
    return next(r for r in rows if r.split("|")[0] == want)


def run_case(host, arm_name, checkout, dest, out_root):
    arm = next(a for a in arms.ARMS if a["arm"] == arm_name)
    env = {k: v for k, v in os.environ.items() if k not in CLEARED}
    cmd_env = arms.command_env(host, arm, checkout, dest)
    env.update(cmd_env)
    row = one_text(arm, dest)
    case_root = os.path.join(out_root, f"{host}_{arm_name}")
    env.update({"GATE_TEXTS": row, "GATE_OUT_ROOT": case_root, "EXTRA_FLAGS": "--estimate",
                "CARD160_BALANCE_READ": "0", "RESUME": "0"})
    if host == "paid":
        env["AUTHOR_GO"] = PROOF_GO
        env["OPENROUTER_API_KEY"] = "sk-proof-not-a-real-key"
    script = os.path.join(checkout, arms.HOSTS[host]["script"])
    t = arms.tag(host, arm_name)
    planted = []
    if host in ("sonnet", "haiku") and arm_name in ("r2", "r3"):
        # The runner refuses a Claude r2/r3 unless that judge's r1 recorded the
        # same Claude program (arm_verify.cli_change_problem). Each proof case
        # has its own folder, so plant the r1 record the real r1 would leave.
        name0 = row.split("|")[0]
        r1 = os.path.join(case_root, f"gate_{arms.tag(host, 'r1')}_{name0}")
        os.makedirs(r1, exist_ok=True)
        with open(os.path.join(r1, ".card160_runs.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"claude_cli_version": arm_verify.pinned_claude_cli_version(),
                                "planted_by": "prove_switches.py"}) + "\n")
        planted.append(os.path.basename(r1))
    r = subprocess.run(["bash", script, t, arms.HOSTS[host]["conc"]], cwd=checkout, env=env,
                       capture_output=True, text=True, timeout=1500)
    log = r.stdout + r.stderr
    os.makedirs(case_root, exist_ok=True)
    with open(os.path.join(case_root, "gate_output.log"), "w", encoding="utf-8") as f:
        f.write(log)
    name = row.split("|")[0]
    out = os.path.join(case_root, f"gate_{t}_{name}")
    stamp = {}
    if os.path.isfile(os.path.join(out, ".card160_arm")):
        with open(os.path.join(out, ".card160_arm"), encoding="utf-8") as f:
            stamp = json.load(f)

    checks = []

    def check(what, ok, evidence=""):
        checks.append({"switch": what, "ok": bool(ok), "evidence": evidence[:300]})

    folders = sorted(d for d in os.listdir(case_root)
                     if d.startswith("gate_") and os.path.isdir(os.path.join(case_root, d))
                     and d not in planted)
    check("GATE_TEXTS", folders == [f"gate_{t}_{name}"], f"output folders: {folders}")
    check("GATE_VERIFY_SCRIPT", bool(stamp), f"arm stamp written by arm_verify.py: {stamp}")
    check("checkout commit", str(stamp.get("checkout_commit", "")).startswith(arms.MEASURED_COMMIT)
          and "dirty" not in str(stamp.get("checkout_commit", "")),
          f"checkout_commit={stamp.get('checkout_commit')}")
    want_model = cmd_env.get("MODEL") or DEFAULT_MODEL[host]
    check("MODEL", stamp.get("model") == want_model, f"--model reached the checker as {stamp.get('model')!r}")
    check("STABILITY_RUN", stamp.get("PAPERTRAIL_STABILITY_RUN") == "1",
          f"PAPERTRAIL_STABILITY_RUN={stamp.get('PAPERTRAIL_STABILITY_RUN')!r}")
    # The checker's estimate line: a priced model prints "Cost estimate for
    # model ...", the claude-code route prints "claude-code backend: $0 API spend".
    marks = ("Cost estimate for model", "claude-code backend: $0 API spend")
    check("checker reached", any(m in log for m in marks),
          next((ln for ln in log.splitlines() if any(m in ln for m in marks)), "no estimate line"))

    def lines(sub):
        return [ln for ln in log.splitlines() if sub in ln]

    p_lines = lines("[card160 prompt override]")
    if "CARD160_PROMPTS" in cmd_env:
        check("CARD160_PROMPTS", len({ln for ln in p_lines}) >= 2 and all(
            "reworded_" in ln for ln in p_lines), " | ".join(sorted(set(p_lines))))
    else:
        check("no prompt override", not p_lines, f"{len(p_lines)} override lines")
    s_lines = lines("evidence order shuffled, seed 1")
    if "CARD160_SHUFFLE_SEED" in cmd_env:
        check("CARD160_SHUFFLE_SEED", bool(s_lines), s_lines[0] if s_lines else "no shuffle line")
    else:
        check("no shuffle", not s_lines, f"{len(s_lines)} shuffle lines")
    b_lines = lines("[card160 biased note]")
    if "CARD160_BIAS_NOTE" in cmd_env:
        noted = all(arm_verify.BIAS_NOTE in open(os.path.join(out, ".card160_prompts", n),
                                                 encoding="utf-8").read()
                    for n in arm_verify.VERDICT_PROMPTS
                    if os.path.isfile(os.path.join(out, ".card160_prompts", n)))
        check("CARD160_BIAS_NOTE", len(set(b_lines)) >= 2 and noted, " | ".join(sorted(set(b_lines))))
    else:
        check("no biased note", not b_lines, f"{len(b_lines)} note lines")
    d_lines = lines("[card160 doubting note]")
    if "CARD160_DOUBT_NOTE" in cmd_env:
        noted = all(arm_verify.DOUBT_NOTE in open(os.path.join(out, ".card160_prompts", n),
                                                  encoding="utf-8").read()
                    for n in arm_verify.VERDICT_PROMPTS
                    if os.path.isfile(os.path.join(out, ".card160_prompts", n)))
        check("CARD160_DOUBT_NOTE", len(set(d_lines)) >= 2 and noted, " | ".join(sorted(set(d_lines))))
    else:
        check("no doubting note", not d_lines, f"{len(d_lines)} note lines")
    c_lines = lines("Claude judge gets the Gemma judge instructions")
    if cmd_env.get("CARD160_CLAUDE_FAIR") == "1":
        check("CARD160_CLAUDE_FAIR", bool(c_lines), c_lines[0] if c_lines else "no line")
        g = lines("subscription-limit guard on")
        check("subscription-limit guard", bool(g), g[0] if g else "no guard line")
    else:
        check("no Claude switch", not c_lines, f"{len(c_lines)} lines")
    if host == "paid":
        m = lines(f"spending ceiling: ${arms.PAID_MAX_USD}")
        check("MAX_USD", bool(m), m[0] if m else "no ceiling line")
        g = lines("balance guard on")
        check("balance guard", bool(g), g[0] if g else "no guard line")
        h = os.path.join(out, ".judge_host")
        check("paid host stamp", os.path.isfile(h) and open(h).read() == "paid-openrouter-bf16",
              open(h).read() if os.path.isfile(h) else "no .judge_host")
    runs = []
    if os.path.isfile(os.path.join(out, ".card160_runs.jsonl")):
        with open(os.path.join(out, ".card160_runs.jsonl"), encoding="utf-8") as f:
            runs = [json.loads(ln) for ln in f if ln.strip()]
    check("run record", bool(runs) and all(r.get("libraries_sha256") for r in runs)
          and all(r.get("model") == want_model for r in runs),
          f"{len(runs)} start(s) recorded; libraries {runs[0].get('libraries_sha256', '')[:12] if runs else '-'}")
    if host in ("sonnet", "haiku"):
        check("Claude CLI recorded", bool(runs) and all(r.get("claude_cli_version") for r in runs),
              (runs[0].get("claude_cli_version") or "none") if runs else "no run record")
        check("DISABLE_AUTOUPDATER", bool(runs) and all(r.get("DISABLE_AUTOUPDATER") == "1" for r in runs),
              f"DISABLE_AUTOUPDATER={runs[0].get('DISABLE_AUTOUPDATER')!r}" if runs else "no run record")
        if planted:
            check("same Claude program as r1", bool(stamp),
                  f"r1 record planted in {planted[0]}; the runner did not refuse")
    reqs = os.path.join(out, "llm_calls.jsonl")
    n_req = sum(1 for _ in open(reqs, encoding="utf-8")) if os.path.isfile(reqs) else 0
    check("no request made", n_req == 0, f"llm_calls.jsonl lines: {n_req}")
    return {"host": host, "arm": arm_name, "tag": t, "text": name, "exit": r.returncode,
            "ok": all(c["ok"] for c in checks), "checks": checks,
            "log": os.path.join(case_root, "gate_output.log")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--checkout", default=arms.default_checkout())
    ap.add_argument("--dest", default=arms.default_dest())
    ap.add_argument("--out-root", default=os.path.join(
        os.path.realpath(os.path.join(REPO, "logs")), "autonomous_day", "card160_switch_proof"))
    ap.add_argument("--json", default=os.path.join(HERE, "switch_proof.json"))
    ap.add_argument("--only", help="host/arm, for one case")
    args = ap.parse_args(argv)
    cases = [tuple(args.only.split("/"))] if args.only else CASES
    stamp_dir = os.path.join(args.out_root, _dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
    results = []
    for host, arm_name in cases:
        res = run_case(host, arm_name, args.checkout, args.dest, stamp_dir)
        results.append(res)
        print(f"{host}/{arm_name}: {'OK' if res['ok'] else 'FAILED'}", flush=True)
        for c in res["checks"]:
            print(f"   {'ok ' if c['ok'] else 'BAD'} {c['switch']}: {c['evidence'][:160]}", flush=True)
    report = {"generated_by": "benchmarks/card160/prove_switches.py",
              "when": _dt.datetime.now().isoformat(timespec="seconds"),
              "checkout": args.checkout, "measured_commit": arms.MEASURED_COMMIT,
              "how": "the measured checkout's own gate scripts, each arm's exact switches, the "
                     "checker in --estimate mode (returns before any model client exists), a "
                     "dummy OpenRouter key, output under a proof folder",
              "cases": results, "all_ok": all(r["ok"] for r in results)}
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1, ensure_ascii=False)
    print(f"{sum(r['ok'] for r in results)} of {len(results)} cases OK")
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
